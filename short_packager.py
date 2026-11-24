"""Finished-short packaging: the creator's own value around a source excerpt.

ClipForge Phase 4. Takes a rendered clip (captions and hook already burned)
and produces ``final_<ts>_<clean>.mp4`` with any of:

* an intro / context card: a title and an optional context line over a
  blurred, darkened frame of the clip, held for ``intro_seconds`` or for as
  long as the creator's intro voiceover lasts;
* the creator's voiceover: as the intro, as the outro, or OVER the clip at an
  offset with the source ducked underneath (sidechain compression);
* a logo in a corner and a small source-credit line (attribution);
* an outro / call-to-action card.

The excerpt and the creator's material stay distinguishable: the recipe
(``shorts[i].final.recipe``) records every element and its source.

Safety: all text is rendered to PNG with PIL, never passed to drawtext; the
filter graph is built only from numbers this module computes and fixed
filter names; file inputs are passed as argv entries, never as filter text.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import ffmpeg_utils

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
TITLE_FONT = os.path.join(FONT_DIR, "Montserrat-ExtraBold.ttf")
LOGO_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
VOICEOVER_MODES = ("intro", "outro", "over")
MAX_CARD_SECONDS = 15.0
FFMPEG_TIMEOUT = 1800


@dataclass
class PackageOptions:
    intro_title: str = ""
    intro_context: str = ""
    intro_seconds: float = 2.0
    outro_text: str = ""
    outro_seconds: float = 2.5
    logo_path: str = ""
    logo_corner: str = "top-right"
    logo_width_ratio: float = 0.14
    credit_text: str = ""
    voiceover_path: str = ""
    voiceover_mode: str = "intro"
    voiceover_offset: float = 0.0
    voiceover_gain_db: float = 0.0
    duck_source: bool = True
    accent_color: str = "#FFE500"
    extra: dict = field(default_factory=dict)

    def validate(self):
        """Clamp numbers, refuse unknown enum values. Raises ValueError."""
        if self.logo_corner not in LOGO_CORNERS:
            raise ValueError(f"logo_corner must be one of {LOGO_CORNERS}")
        if self.voiceover_mode not in VOICEOVER_MODES:
            raise ValueError(f"voiceover_mode must be one of {VOICEOVER_MODES}")
        self.intro_seconds = _clamp(self.intro_seconds, 0.0, MAX_CARD_SECONDS)
        self.outro_seconds = _clamp(self.outro_seconds, 0.0, MAX_CARD_SECONDS)
        self.logo_width_ratio = _clamp(self.logo_width_ratio, 0.05, 0.4)
        self.voiceover_offset = _clamp(self.voiceover_offset, 0.0, 600.0)
        self.voiceover_gain_db = _clamp(self.voiceover_gain_db, -20.0, 20.0)
        for name in ("intro_title", "intro_context", "outro_text", "credit_text"):
            setattr(self, name, str(getattr(self, name) or "").strip()[:300])
        _parse_color(self.accent_color)
        return self

    def is_empty(self):
        return not (self.intro_title or self.intro_context or self.outro_text
                    or self.logo_path or self.credit_text or self.voiceover_path)


def _clamp(value, lo, hi):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo


def _parse_color(text):
    text = str(text or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        raise ValueError("accent_color must look like #RRGGBB")
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


# --------------------------------------------------------------------------- #
# Media facts
# --------------------------------------------------------------------------- #
def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ValueError("cannot read media file")
    data = json.loads(out.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    info = {"duration": duration, "has_audio": audio is not None}
    if video:
        num, _, den = str(video.get("avg_frame_rate") or "30/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 30.0
        info.update(width=int(video["width"]), height=int(video["height"]),
                    fps=round(fps if 10 <= fps <= 120 else 30.0, 3))
    return info


# --------------------------------------------------------------------------- #
# Cards (PIL)
# --------------------------------------------------------------------------- #
def _font(size):
    try:
        return ImageFont.truetype(TITLE_FONT, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _draw_block(img, lines_spec, center_y):
    """Draw [(text, font, fill)] stacked and centred around ``center_y``."""
    draw = ImageDraw.Draw(img)
    w = img.width
    rows = []
    for text, font, fill in lines_spec:
        for line in _wrap(draw, text, font, int(w * 0.84)):
            box = draw.textbbox((0, 0), line, font=font)
            rows.append((line, font, fill, box[3] - box[1]))
    gap = int(w * 0.02)
    total = sum(r[3] for r in rows) + gap * max(0, len(rows) - 1)
    y = int(center_y - total / 2)
    for line, font, fill, height in rows:
        lw = draw.textlength(line, font=font)
        draw.text(((w - lw) / 2, y), line, font=font, fill=fill,
                  stroke_width=max(2, int(font.size * 0.06)), stroke_fill=(0, 0, 0))
        y += height + gap


def frame_at(video, t, out_png):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", video,
                    "-frames:v", "1", out_png], check=True, timeout=120)
    return out_png


def render_card(background_png, size, title="", subtitle="", accent=(255, 229, 0)):
    """A card: blurred, darkened background + centred title/subtitle."""
    w, h = size
    base = Image.open(background_png).convert("RGB").resize((w, h))
    base = base.filter(ImageFilter.GaussianBlur(radius=max(8, w // 40)))
    base = Image.blend(base, Image.new("RGB", (w, h), (0, 0, 0)), 0.55)
    spec = []
    if title:
        spec.append((title, _font(int(w * 0.085)), accent))
    if subtitle:
        spec.append((subtitle, _font(int(w * 0.05)), (255, 255, 255)))
    if spec:
        _draw_block(base, spec, h * 0.45)
    return base


def render_text_overlay(size, text, rel_y=0.055, rel_size=0.03):
    """Transparent full-frame PNG with one small line of text (source credit)."""
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(max(14, int(w * rel_size)))
    line = text if draw.textlength(text, font=font) <= w * 0.9 else \
        _wrap(draw, text, font, int(w * 0.9))[0] + "…"
    lw = draw.textlength(line, font=font)
    pad = int(w * 0.012)
    y = int(h * rel_y)
    box = draw.textbbox(((w - lw) / 2, y), line, font=font)
    draw.rounded_rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                           radius=pad, fill=(0, 0, 0, 150))
    draw.text(((w - lw) / 2, y), line, font=font, fill=(255, 255, 255, 235))
    return img


def prepare_logo(logo_path, size, ratio, corner):
    """Full-frame transparent PNG with the logo placed in a corner."""
    w, h = size
    logo = Image.open(logo_path).convert("RGBA")
    target_w = max(16, int(w * ratio))
    logo = logo.resize((target_w, max(1, int(logo.height * target_w / logo.width))))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = int(w * 0.04)
    top = int(h * 0.11)       # under the platform's top bar
    bottom = int(h * 0.80)    # above the caption/UI zone
    x = m if corner.endswith("left") else w - logo.width - m
    y = top if corner.startswith("top") else bottom - logo.height
    canvas.alpha_composite(logo, (x, y))
    return canvas


# --------------------------------------------------------------------------- #
# Filter graph (numbers and fixed names only)
# --------------------------------------------------------------------------- #
def plan(opts: PackageOptions, clip: dict, voice_seconds: Optional[float]):
    """Durations of each part. Pure; used by build_command and tests."""
    vo_intro = voice_seconds if (opts.voiceover_path and opts.voiceover_mode == "intro") else 0
    vo_outro = voice_seconds if (opts.voiceover_path and opts.voiceover_mode == "outro") else 0
    want_intro = bool(opts.intro_title or opts.intro_context or vo_intro)
    want_outro = bool(opts.outro_text or vo_outro)
    intro = max(opts.intro_seconds or 0, (vo_intro or 0) + 0.3) if want_intro else 0.0
    outro = max(opts.outro_seconds or 0, (vo_outro or 0) + 0.3) if want_outro else 0.0
    if want_intro and intro < 1.0:
        intro = 1.0
    if want_outro and outro < 1.0:
        outro = 1.0
    return {"intro": round(intro, 3), "clip": round(clip["duration"], 3),
            "outro": round(outro, 3),
            "total": round(intro + clip["duration"] + outro, 3)}


def build_command(clip_path, out_path, clip, durations, assets, opts: PackageOptions):
    """ffmpeg argv. ``assets``: dict of prepared PNG/audio paths (or None)."""
    w, h, fps = clip["width"], clip["height"], clip["fps"]
    args = ["ffmpeg", "-y", "-hide_banner", "-v", "error"]
    inputs = []

    def add(*input_args):
        inputs.append(list(input_args))
        return len(inputs) - 1

    i_clip = add("-i", clip_path)
    i_intro = add("-i", assets["intro_png"]) if durations["intro"] else None
    i_outro = add("-i", assets["outro_png"]) if durations["outro"] else None
    i_overlay = add("-loop", "1", "-framerate", f"{fps:g}", "-i", assets["overlay_png"]) \
        if assets.get("overlay_png") else None
    i_voice = add("-i", assets["voice"]) if assets.get("voice") else None
    for chunk in inputs:
        args += chunk

    fmt = f"fps={fps:g},scale={w}:{h},setsar=1,format=yuv420p"

    def card(index, seconds):
        # A slow push-in (to 6 %) so a card reads as motion, not a frozen
        # frame; zoompan emits exactly `frames` frames from the one still.
        frames = max(1, int(round(seconds * fps)))
        step = 0.06 / frames
        return (f"[{index}:v]scale={w * 2}:{h * 2},zoompan=z='min(1+{step:.6f}*on,1.06)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:"
                f"fps={fps:g},setsar=1,format=yuv420p")
    aformat = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    f = []
    # Main excerpt video, with the logo/credit layer on top.
    if i_overlay is not None:
        f.append(f"[{i_clip}:v]{fmt}[cv0]")
        f.append(f"[cv0][{i_overlay}:v]overlay=0:0:shortest=1,format=yuv420p[cv]")
    else:
        f.append(f"[{i_clip}:v]{fmt}[cv]")
    # Main excerpt audio (silence when the clip has none).
    if clip["has_audio"]:
        f.append(f"[{i_clip}:a]{aformat}[ca0]")
    else:
        f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{durations['clip']:.3f}[ca0]")
    voice_gain = f"volume={opts.voiceover_gain_db:.2f}dB"
    mode = opts.voiceover_mode if i_voice is not None else None
    if mode == "over":
        delay_ms = int(round(opts.voiceover_offset * 1000))
        f.append(f"[{i_voice}:a]{aformat},{voice_gain},adelay={delay_ms}:all=1,"
                 f"apad,atrim=0:{durations['clip']:.3f},asplit=2[vo][vokey]")
        if opts.duck_source:
            f.append("[ca0][vokey]sidechaincompress=threshold=0.03:ratio=8:attack=20:"
                     "release=350[ducked]")
            f.append("[ducked][vo]amix=inputs=2:duration=first:normalize=0[ca]")
        else:
            f.append("[vokey]anullsink")
            f.append("[ca0][vo]amix=inputs=2:duration=first:normalize=0[ca]")
    else:
        f.append("[ca0]anull[ca]")

    parts = []
    if i_intro is not None:
        fade_out = max(0.0, durations["intro"] - 0.25)
        f.append(f"{card(i_intro, durations['intro'])},fade=t=in:d=0.25,"
                 f"fade=t=out:st={fade_out:.3f}:d=0.25[iv]")
        if mode == "intro":
            f.append(f"[{i_voice}:a]{aformat},{voice_gain},apad,"
                     f"atrim=0:{durations['intro']:.3f}[ia]")
        else:
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{durations['intro']:.3f}[ia]")
        parts.append(("[iv]", "[ia]"))
    parts.append(("[cv]", "[ca]"))
    if i_outro is not None:
        f.append(f"{card(i_outro, durations['outro'])},fade=t=in:d=0.25[ov]")
        if mode == "outro":
            f.append(f"[{i_voice}:a]{aformat},{voice_gain},apad,"
                     f"atrim=0:{durations['outro']:.3f}[oa]")
        else:
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{durations['outro']:.3f}[oa]")
        parts.append(("[ov]", "[oa]"))
    joined = "".join(v + a for v, a in parts)
    f.append(f"{joined}concat=n={len(parts)}:v=1:a=1[vout][aout0]")
    # One loudness pass over the whole short so the voiceover and the excerpt
    # sit at the same level; the clip's speech chain already ran.
    if ffmpeg_utils._normalize_on():
        f.append(f"[aout0]{ffmpeg_utils.LOUDNORM_FILTER},aresample=48000[aout]")
    else:
        f.append("[aout0]anull[aout]")

    args += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]"]
    args += ffmpeg_utils.video_encode_args(ffmpeg_utils.QUALITY)
    if "-pix_fmt" not in args:
        args += ["-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-b:a", ffmpeg_utils.HD_AUDIO_BITRATE, "-ar", "48000",
             *ffmpeg_utils.METADATA_SCRUB, "-movflags", "+faststart",
             "-t", f"{durations['total']:.3f}", out_path]
    return args


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def package_clip(clip_path, output_dir, clean_name, opts: PackageOptions, runner=None):
    """Render the packaged short. Returns (filename, recipe dict)."""
    opts.validate()
    if opts.is_empty():
        raise ValueError("nothing to add: set a title, outro, logo, credit or voiceover")
    for path in (opts.logo_path, opts.voiceover_path):
        if path and not os.path.isfile(path):
            raise ValueError("a referenced asset file does not exist")
    clip = probe(clip_path)
    if "width" not in clip:
        raise ValueError("clip has no video stream")
    voice_seconds = probe(opts.voiceover_path)["duration"] if opts.voiceover_path else None
    durations = plan(opts, clip, voice_seconds)
    size = (clip["width"], clip["height"])
    accent = _parse_color(opts.accent_color)
    out_name = f"final_{int(time.time())}_{uuid.uuid4().hex[:6]}_{clean_name}"
    out_path = os.path.join(output_dir, out_name)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix="temp_pkg_") as work:
        assets = {}
        if durations["intro"]:
            bg = frame_at(clip_path, min(1.0, clip["duration"] / 2), os.path.join(work, "bg_in.png"))
            render_card(bg, size, opts.intro_title, opts.intro_context, accent).save(
                os.path.join(work, "intro.png"))
            assets["intro_png"] = os.path.join(work, "intro.png")
        if durations["outro"]:
            bg = frame_at(clip_path, max(0.0, clip["duration"] - 1.0), os.path.join(work, "bg_out.png"))
            render_card(bg, size, opts.outro_text, "", accent).save(os.path.join(work, "outro.png"))
            assets["outro_png"] = os.path.join(work, "outro.png")
        if opts.logo_path or opts.credit_text:
            layer = Image.new("RGBA", size, (0, 0, 0, 0))
            if opts.logo_path:
                layer.alpha_composite(prepare_logo(opts.logo_path, size, opts.logo_width_ratio,
                                                   opts.logo_corner))
            if opts.credit_text:
                layer.alpha_composite(render_text_overlay(size, opts.credit_text))
            layer.save(os.path.join(work, "overlay.png"))
            assets["overlay_png"] = os.path.join(work, "overlay.png")
        if opts.voiceover_path:
            assets["voice"] = opts.voiceover_path
        cmd = build_command(clip_path, out_path, clip, durations, assets, opts)
        (runner or _run)(cmd)
    recipe = {
        "v": 1,
        "source_clip": os.path.basename(clip_path),
        "durations": durations,
        "elements": {
            "intro_card": bool(durations["intro"]),
            "outro_card": bool(durations["outro"]),
            "logo": bool(opts.logo_path),
            "credit": bool(opts.credit_text),
            "voiceover": opts.voiceover_mode if opts.voiceover_path else None,
        },
        # The excerpt's place inside the finished short: everything outside
        # it is the creator's own material.
        "excerpt_range": [durations["intro"], round(durations["intro"] + durations["clip"], 3)],
        "options": {k: v for k, v in asdict(opts).items()
                    if k not in ("logo_path", "voiceover_path", "extra")},
    }
    return out_name, recipe


def _run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=FFMPEG_TIMEOUT)
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", "replace")[-500:]
        raise RuntimeError(f"packaging failed: {tail}")
