"""Automated quality control of a rendered clip (ClipForge Phase 2).

One ffprobe call plus one decode pass (blackdetect + freezedetect on the video,
ebur128 + silencedetect on the audio) produce a report of checks, each PASS,
WARN or FAIL with a short reason. FAIL means the file must not be delivered as
ready nor uploaded; WARN asks a human to look. The report is written beside
the clip as ``<clip>.qc.json`` (data only: numbers and fixed strings).

Pure parsing helpers are separate from the subprocess calls so the thresholds
are unit-testable without media.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Optional

from jsonfile import write_json_atomic

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_RANK = {PASS: 0, WARN: 1, FAIL: 2}
QC_VERSION = 1
SIDECAR_SUFFIX = ".qc.json"

# Thresholds. Podcasts are mostly static, so a frozen picture only warns.
DURATION_TOLERANCE = 0.6        # s between expected and actual length
BLACK_MIN_SECONDS = 0.5         # blackdetect d=
BLACK_FAIL_RATIO = 0.3
FREEZE_MIN_SECONDS = 3.0        # freezedetect d=
FREEZE_FAIL_RATIO = 0.9
SILENCE_MIN_SECONDS = 2.0
SILENCE_WARN_RATIO = 0.4
LOUDNESS_TARGET = -14.0
LOUDNESS_WARN_DELTA = 2.5
LOUDNESS_SILENT = -50.0         # integrated below this: no usable audio
TRUE_PEAK_WARN = -0.5           # dBTP
TIMEOUT_SECONDS = 600


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    status: str = PASS
    checks: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    version: int = QC_VERSION

    def add(self, name, status, detail):
        self.checks.append(Check(name, status, detail))
        if _RANK[status] > _RANK[self.status]:
            self.status = status

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- #
# Parsing (pure)
# --------------------------------------------------------------------------- #
def _fps(rate: str) -> Optional[float]:
    try:
        num, _, den = (rate or "").partition("/")
        value = float(num) / float(den or 1)
        return value if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def parse_probe(data: dict) -> dict:
    """Relevant facts from ``ffprobe -show_streams -show_format`` JSON."""
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    out = {"duration": duration, "has_video": bool(video), "has_audio": bool(audio)}
    if video:
        out.update({
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "video_codec": str(video.get("codec_name") or ""),
            "pix_fmt": str(video.get("pix_fmt") or ""),
            "profile": str(video.get("profile") or ""),
            "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        })
    if audio:
        out.update({
            "audio_codec": str(audio.get("codec_name") or ""),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
        })
    return out


_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")
_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")
_EBUR_I = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_DECODE_ERROR = re.compile(r"error while decoding|corrupt|invalid data found|"
                           r"missing picture|concealing", re.I)
_EBUR_TP = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


def _spans(starts, ends, duration):
    """Pair start/end markers; an open span runs to the end of the file."""
    spans = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration
        spans.append((max(0.0, start), max(start, end)))
    return spans


def _total(spans):
    return sum(e - s for s, e in spans)


def _num(text):
    return float("-inf") if text == "-inf" else float(text)


def parse_analysis(stderr: str, duration: float) -> dict:
    """Black/freeze/silence spans and loudness from the decode pass log."""
    black = [(float(a), float(b)) for a, b in _BLACK.findall(stderr)]
    freeze = _spans([float(x) for x in _FREEZE_START.findall(stderr)],
                    [float(x) for x in _FREEZE_END.findall(stderr)], duration)
    silence = _spans([float(x) for x in _SIL_START.findall(stderr)],
                     [float(x) for x in _SIL_END.findall(stderr)], duration)
    i_match = _EBUR_I.findall(stderr)
    tp_match = _EBUR_TP.findall(stderr)
    return {
        "black_seconds": round(_total(black), 3),
        "black_spans": black[:20],
        "freeze_seconds": round(_total(freeze), 3),
        "silence_seconds": round(_total(silence), 3),
        "integrated_lufs": _num(i_match[-1]) if i_match else None,
        "true_peak_dbtp": _num(tp_match[-1]) if tp_match else None,
    }


def evaluate(probe: dict, analysis: Optional[dict], *, expected_size=None,
             expected_duration=None, expect_audio=True, hd=False,
             upscaled=False, decode_errors: str = "") -> Report:
    """Turn measurements into PASS/WARN/FAIL checks."""
    r = Report(metrics={**probe, **(analysis or {})})
    dur = probe.get("duration") or 0.0

    if not probe.get("has_video"):
        r.add("streams", FAIL, "no video stream")
        return r
    if expect_audio and not probe.get("has_audio"):
        r.add("streams", FAIL, "no audio stream")
    else:
        r.add("streams", PASS, "video and audio present" if probe.get("has_audio")
              else "video present")

    if dur <= 0.5:
        r.add("duration", FAIL, f"duration {dur:.2f}s")
    elif expected_duration and abs(dur - expected_duration) > DURATION_TOLERANCE:
        r.add("duration", FAIL if dur < expected_duration else WARN,
              f"{dur:.2f}s, expected {expected_duration:.2f}s")
    else:
        r.add("duration", PASS, f"{dur:.2f}s")

    w, h = probe.get("width", 0), probe.get("height", 0)
    if expected_size and (w, h) != tuple(expected_size):
        r.add("resolution", FAIL, f"{w}x{h}, expected {expected_size[0]}x{expected_size[1]}")
    elif w % 2 or h % 2 or not w or not h:
        r.add("resolution", FAIL, f"invalid size {w}x{h}")
    elif upscaled:
        r.add("resolution", WARN, f"{w}x{h}, upscaled from a smaller source")
    else:
        r.add("resolution", PASS, f"{w}x{h}")

    codec, pix = probe.get("video_codec"), probe.get("pix_fmt")
    if codec != "h264":
        r.add("codec", FAIL, f"video codec {codec}, expected h264")
    elif pix != "yuv420p":
        r.add("codec", FAIL, f"pixel format {pix}; players need yuv420p")
    else:
        r.add("codec", PASS, f"h264 {probe.get('profile', '')} yuv420p".strip())

    fps = probe.get("fps")
    if not fps or fps < 15 or fps > 61:
        r.add("frame_rate", WARN, f"frame rate {fps}")
    else:
        r.add("frame_rate", PASS, f"{fps:.2f} fps")

    if probe.get("has_audio"):
        rate = probe.get("sample_rate", 0)
        if probe.get("audio_codec") != "aac":
            r.add("audio_format", WARN, f"audio codec {probe.get('audio_codec')}")
        elif hd and rate != 48000:
            r.add("audio_format", WARN, f"{rate} Hz, expected 48000")
        else:
            r.add("audio_format", PASS, f"aac {rate} Hz")

    if decode_errors.strip():
        r.add("decode", FAIL, "decoder errors: " + decode_errors.strip()[:160])
    elif analysis is not None:
        r.add("decode", PASS, "decoded cleanly")

    if analysis is None:
        return r

    black = analysis["black_seconds"]
    if dur and black / dur >= BLACK_FAIL_RATIO:
        r.add("black_frames", FAIL, f"{black:.1f}s of black")
    elif black > 0:
        r.add("black_frames", WARN, f"{black:.1f}s of black")
    else:
        r.add("black_frames", PASS, "none")

    frozen = analysis["freeze_seconds"]
    if dur and frozen / dur >= FREEZE_FAIL_RATIO:
        r.add("frozen_frames", FAIL, f"picture frozen for {frozen:.1f}s")
    elif frozen > 0:
        r.add("frozen_frames", WARN, f"picture frozen for {frozen:.1f}s")
    else:
        r.add("frozen_frames", PASS, "none")

    if probe.get("has_audio"):
        lufs = analysis.get("integrated_lufs")
        if lufs is None:
            r.add("loudness", WARN, "loudness not measured")
        elif lufs < LOUDNESS_SILENT:
            r.add("loudness", FAIL, "audio is silent")
        elif abs(lufs - LOUDNESS_TARGET) > LOUDNESS_WARN_DELTA:
            r.add("loudness", WARN, f"{lufs:.1f} LUFS, target {LOUDNESS_TARGET:.0f}")
        else:
            r.add("loudness", PASS, f"{lufs:.1f} LUFS")
        peak = analysis.get("true_peak_dbtp")
        if peak is not None and peak > TRUE_PEAK_WARN:
            r.add("true_peak", WARN, f"true peak {peak:.1f} dBTP may clip")
        elif peak is not None:
            r.add("true_peak", PASS, f"{peak:.1f} dBTP")
        silent = analysis["silence_seconds"]
        if dur and silent / dur >= SILENCE_WARN_RATIO:
            r.add("silence", WARN, f"{silent:.1f}s of silence")
        else:
            r.add("silence", PASS, f"{silent:.1f}s of silence")
    return r


# --------------------------------------------------------------------------- #
# Running (subprocess)
# --------------------------------------------------------------------------- #
def probe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError("ffprobe could not read the file")
    return parse_probe(json.loads(result.stdout or "{}"))


def analyse(path: str, duration: float, has_audio: bool) -> tuple[dict, str]:
    """One decode pass; returns (analysis, decoder error text)."""
    vf = (f"blackdetect=d={BLACK_MIN_SECONDS}:pix_th=0.10,"
          f"freezedetect=n=-60dB:d={FREEZE_MIN_SECONDS}")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", path,
           "-map", "0:v:0", "-vf", vf]
    if has_audio:
        af = f"silencedetect=n=-45dB:d={SILENCE_MIN_SECONDS},ebur128=peak=true"
        cmd += ["-map", "0:a:0", "-af", af]
    cmd += ["-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    errors = "\n".join(line for line in result.stderr.splitlines()
                       if _DECODE_ERROR.search(line))
    if result.returncode != 0 and not errors:
        errors = f"ffmpeg exited with {result.returncode}"
    return parse_analysis(result.stderr, duration), errors


def run_qc(path: str, *, expected_size=None, expected_duration=None,
           expect_audio=True, hd=False, upscaled=False, write=True) -> Report:
    """Measure ``path`` and (by default) write ``<path>.qc.json``."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        report = Report()
        report.add("file", FAIL, "file missing or empty")
    else:
        try:
            facts = probe(path)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            report = Report()
            report.add("file", FAIL, "ffprobe could not read the file")
        else:
            try:
                analysis, errors = analyse(path, facts["duration"], facts["has_audio"])
            except subprocess.TimeoutExpired:
                analysis, errors = None, "decode pass timed out"
            report = evaluate(facts, analysis, expected_size=expected_size,
                              expected_duration=expected_duration,
                              expect_audio=expect_audio, hd=hd, upscaled=upscaled,
                              decode_errors=errors)
    if write and os.path.isdir(os.path.dirname(os.path.abspath(path))):
        write_json_atomic(path + SIDECAR_SUFFIX, report.to_dict())
    return report


def read_report(path: str) -> Optional[dict]:
    """The stored report of a clip, validated as data (never trusted blindly)."""
    try:
        with open(path + SIDECAR_SUFFIX, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in _RANK:
        return None
    checks = [
        {"name": str(c.get("name", ""))[:40], "status": c.get("status"),
         "detail": str(c.get("detail", ""))[:200]}
        for c in data.get("checks") or [] if isinstance(c, dict) and c.get("status") in _RANK
    ]
    return {"status": data["status"], "checks": checks}
