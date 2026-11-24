"""render_qc: deterministic checks on synthetic lavfi media (no real footage)."""

import json
import shutil
import subprocess

import pytest

import render_qc as qc

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make(path, video="testsrc2=size=1080x1920:rate=30", audio="sine=f=220:r=48000",
         seconds=3, vcodec=("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"),
         extra_audio=()):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    cmd += ["-t", str(seconds), *vcodec]
    if audio:
        cmd += [*extra_audio, "-c:a", "aac", "-ar", "48000", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return str(path)


def statuses(report):
    return {c.name: c.status for c in report.checks}


# --------------------------------------------------------------------------- #
# Pure evaluation
# --------------------------------------------------------------------------- #
GOOD_PROBE = {"duration": 30.0, "has_video": True, "has_audio": True, "width": 1080,
              "height": 1920, "video_codec": "h264", "pix_fmt": "yuv420p",
              "profile": "High", "fps": 30.0, "audio_codec": "aac",
              "sample_rate": 48000, "channels": 2}
GOOD_ANALYSIS = {"black_seconds": 0.0, "black_spans": [], "freeze_seconds": 0.0,
                 "silence_seconds": 1.0, "integrated_lufs": -14.2, "true_peak_dbtp": -1.8}


def test_good_clip_passes():
    r = qc.evaluate(GOOD_PROBE, GOOD_ANALYSIS, expected_size=(1080, 1920),
                    expected_duration=30.0, hd=True)
    assert r.status == qc.PASS, r.checks


@pytest.mark.parametrize("probe_patch,analysis_patch,check,status", [
    ({"width": 1216, "height": 2160}, {}, "resolution", qc.FAIL),
    ({"pix_fmt": "yuv444p"}, {}, "codec", qc.FAIL),
    ({"video_codec": "hevc"}, {}, "codec", qc.FAIL),
    ({"has_audio": False}, {}, "streams", qc.FAIL),
    ({"duration": 22.0}, {}, "duration", qc.FAIL),
    ({"sample_rate": 96000}, {}, "audio_format", qc.WARN),
    ({}, {"black_seconds": 12.0}, "black_frames", qc.FAIL),
    ({}, {"black_seconds": 0.8}, "black_frames", qc.WARN),
    ({}, {"freeze_seconds": 29.0}, "frozen_frames", qc.FAIL),
    ({}, {"freeze_seconds": 4.0}, "frozen_frames", qc.WARN),
    ({}, {"integrated_lufs": float("-inf")}, "loudness", qc.FAIL),
    ({}, {"integrated_lufs": -24.0}, "loudness", qc.WARN),
    ({}, {"true_peak_dbtp": 0.3}, "true_peak", qc.WARN),
    ({}, {"silence_seconds": 20.0}, "silence", qc.WARN),
])
def test_each_defect_is_flagged(probe_patch, analysis_patch, check, status):
    r = qc.evaluate({**GOOD_PROBE, **probe_patch}, {**GOOD_ANALYSIS, **analysis_patch},
                    expected_size=(1080, 1920), expected_duration=30.0, hd=True)
    assert statuses(r)[check] == status
    assert qc._RANK[r.status] >= qc._RANK[status]


def test_upscaled_source_warns_and_decode_errors_fail():
    r = qc.evaluate(GOOD_PROBE, GOOD_ANALYSIS, upscaled=True, decode_errors="corrupt frame")
    assert statuses(r)["resolution"] == qc.WARN
    assert statuses(r)["decode"] == qc.FAIL and r.status == qc.FAIL


def test_parse_analysis_reads_ffmpeg_log():
    log = """
[blackdetect @ 0x1] black_start:0 black_end:1.2 black_duration:1.2
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 5.0
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 9.5
[silencedetect @ 0x3] silence_start: 20
  Integrated loudness:
    I:         -15.3 LUFS
  True peak:
    Peak:       -2.1 dBFS
"""
    a = qc.parse_analysis(log, duration=25.0)
    assert a["black_seconds"] == 1.2
    assert a["freeze_seconds"] == 4.5
    assert a["silence_seconds"] == 5.0          # open span runs to the end
    assert a["integrated_lufs"] == -15.3 and a["true_peak_dbtp"] == -2.1


# --------------------------------------------------------------------------- #
# Real ffmpeg on synthetic fixtures
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_real_valid_clip_passes_and_writes_sidecar(tmp_path):
    clip = make(tmp_path / "ok.mp4", audio="sine=f=220:r=48000,volume=0.25")
    r = qc.run_qc(clip, expected_size=(1080, 1920), expected_duration=3.0, hd=True)
    s = statuses(r)
    assert s["resolution"] == s["codec"] == s["streams"] == s["decode"] == qc.PASS
    assert r.status in (qc.PASS, qc.WARN)       # a pure sine is not -14 LUFS speech
    stored = qc.read_report(clip)
    assert stored["status"] == r.status
    assert json.loads((tmp_path / "ok.mp4.qc.json").read_text())["version"] == qc.QC_VERSION


@needs_ffmpeg
def test_real_black_frozen_and_silent_clip(tmp_path):
    clip = make(tmp_path / "bad.mp4", video="color=c=black:size=1080x1920:rate=30",
                audio="anullsrc=r=48000:cl=stereo", seconds=4)
    s = statuses(qc.run_qc(clip, write=False))
    assert s["black_frames"] == qc.FAIL
    assert s["loudness"] == qc.FAIL


@needs_ffmpeg
def test_real_missing_audio_and_wrong_size(tmp_path):
    clip = make(tmp_path / "noaudio.mp4", video="testsrc2=size=720x1280:rate=30", audio=None)
    r = qc.run_qc(clip, expected_size=(1080, 1920), write=False)
    s = statuses(r)
    assert s["streams"] == qc.FAIL and s["resolution"] == qc.FAIL and r.status == qc.FAIL


@needs_ffmpeg
def test_real_444_output_fails_codec_check(tmp_path):
    clip = make(tmp_path / "444.mp4", vcodec=("-c:v", "libx264", "-preset", "ultrafast",
                                              "-pix_fmt", "yuv444p"))
    assert statuses(qc.run_qc(clip, write=False))["codec"] == qc.FAIL


@needs_ffmpeg
def test_real_corrupt_and_missing_files_fail(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00not a video" * 100)
    assert qc.run_qc(str(junk), write=False).status == qc.FAIL
    assert qc.run_qc(str(tmp_path / "absent.mp4"), write=False).status == qc.FAIL


def test_read_report_rejects_garbage(tmp_path):
    clip = tmp_path / "c.mp4"
    (tmp_path / "c.mp4.qc.json").write_text('{"status": "<script>", "checks": []}')
    assert qc.read_report(str(clip)) is None
    (tmp_path / "c.mp4.qc.json").write_text("not json")
    assert qc.read_report(str(clip)) is None
