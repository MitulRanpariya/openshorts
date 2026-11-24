"""packaging: intro/outro cards, logo, credit and voiceover around a clip."""

import shutil
import subprocess

import pytest
from PIL import Image

import short_packager as pk
import render_qc

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_clip(path, seconds=4, size="540x960", audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=f=300:r=48000"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True)
    return str(path)


def make_voice(path, seconds=2.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=500:r=44100", "-t", str(seconds), str(path)], check=True)
    return str(path)


def test_validation_rejects_bad_enums_and_clamps_numbers():
    with pytest.raises(ValueError):
        pk.PackageOptions(logo_corner="middle").validate()
    with pytest.raises(ValueError):
        pk.PackageOptions(voiceover_mode="everywhere").validate()
    with pytest.raises(ValueError):
        pk.PackageOptions(accent_color="red;drawtext").validate()
    o = pk.PackageOptions(intro_seconds=99, logo_width_ratio=5, voiceover_gain_db=-99).validate()
    assert o.intro_seconds == pk.MAX_CARD_SECONDS and o.logo_width_ratio == 0.4
    assert o.voiceover_gain_db == -20.0
    assert pk.PackageOptions().is_empty()


def test_plan_lengths_follow_voiceover():
    clip = {"duration": 20.0}
    o = pk.PackageOptions(intro_title="T", intro_seconds=2, voiceover_path="v.wav",
                          voiceover_mode="intro")
    assert pk.plan(o, clip, 5.0) == {"intro": 5.3, "clip": 20.0, "outro": 0.0, "total": 25.3}
    o = pk.PackageOptions(voiceover_path="v.wav", voiceover_mode="outro", outro_seconds=0)
    assert pk.plan(o, clip, 3.0)["outro"] == 3.3
    o = pk.PackageOptions(voiceover_path="v.wav", voiceover_mode="over")
    assert pk.plan(o, clip, 3.0)["total"] == 20.0


def test_filter_graph_never_contains_user_text(tmp_path):
    evil = "x'];[0:v]drawtext=text=pwned;movie=/etc/passwd"
    o = pk.PackageOptions(intro_title=evil, outro_text=evil, credit_text=evil).validate()
    clip = {"duration": 10.0, "width": 1080, "height": 1920, "fps": 30.0, "has_audio": True}
    d = pk.plan(o, clip, None)
    cmd = pk.build_command("c.mp4", "o.mp4", clip, d,
                           {"intro_png": "i.png", "outro_png": "o.png", "overlay_png": "x.png"}, o)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "pwned" not in graph and "passwd" not in graph and "drawtext" not in graph


def test_logo_layer_lands_in_requested_corner(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo)
    layer = pk.prepare_logo(str(logo), (1080, 1920), 0.2, "bottom-left")
    alpha = layer.split()[-1]
    x0, y0, x1, y1 = alpha.getbbox()
    assert x0 < 100 and y1 > 1920 * 0.7


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["intro", "outro", "over"])
def test_package_renders_and_passes_qc(tmp_path, mode):
    clip = make_clip(tmp_path / "clip.mp4")
    voice = make_voice(tmp_path / "vo.wav")
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (120, 60), (0, 128, 255, 220)).save(logo)
    o = pk.PackageOptions(intro_title="Title", intro_context="Context", intro_seconds=1.5,
                          outro_text="Follow for more", outro_seconds=1.5,
                          logo_path=str(logo), credit_text="Source: test (CC BY)",
                          voiceover_path=voice, voiceover_mode=mode, voiceover_offset=1.0)
    name, recipe = pk.package_clip(clip, str(tmp_path), "clip.mp4", o)
    assert name.startswith("final_") and name.endswith("_clip.mp4")
    out = tmp_path / name
    report = render_qc.run_qc(str(out), expected_size=(540, 960),
                              expected_duration=recipe["durations"]["total"], write=False)
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["duration"] == statuses["resolution"] == statuses["codec"] == "PASS"
    assert statuses["streams"] == "PASS" and statuses["decode"] == "PASS"
    assert recipe["elements"]["voiceover"] == mode
    lo, hi = recipe["excerpt_range"]
    assert hi - lo == pytest.approx(recipe["durations"]["clip"], abs=0.01)
    assert not list(tmp_path.glob("temp_pkg_*"))           # work dir cleaned up


@needs_ffmpeg
def test_package_clip_without_audio(tmp_path):
    clip = make_clip(tmp_path / "silent.mp4", audio=False)
    name, recipe = pk.package_clip(clip, str(tmp_path), "silent.mp4",
                                   pk.PackageOptions(outro_text="Bye", outro_seconds=1))
    facts = render_qc.probe(str(tmp_path / name))
    assert facts["has_audio"] and facts["sample_rate"] == 48000


def test_missing_asset_and_empty_options_refused(tmp_path):
    with pytest.raises(ValueError):
        pk.package_clip("c.mp4", str(tmp_path), "c.mp4", pk.PackageOptions())
    with pytest.raises(ValueError):
        pk.package_clip("c.mp4", str(tmp_path), "c.mp4",
                        pk.PackageOptions(logo_path=str(tmp_path / "nope.png")))
