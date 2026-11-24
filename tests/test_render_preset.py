"""RENDER_PRESET=hd: finished 1080x1920 deliverables; legacy stays unchanged."""

import shutil
import subprocess

import pytest

import ffmpeg_utils as fu
import recut
import reframe_v2


@pytest.fixture
def hd(monkeypatch):
    monkeypatch.setenv("RENDER_PRESET", "hd")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    monkeypatch.delenv("X264_PRESET", raising=False)
    monkeypatch.delenv("AUDIO_NORMALIZE", raising=False)
    monkeypatch.delenv("SPEECH_CLEANUP", raising=False)


def test_unknown_or_unset_preset_is_legacy(monkeypatch):
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    assert fu.render_preset() == "legacy"
    monkeypatch.setenv("RENDER_PRESET", "ULTRA")
    assert fu.render_preset() == "legacy"
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    assert fu.video_encode_args(fu.QUALITY) == ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    assert fu.audio_encode_args() == ["-af", fu.LOUDNORM_FILTER, "-c:a", "aac"]


def test_hd_video_args_force_yuv420p_high_and_lanczos(hd):
    for tier in (fu.QUALITY, fu.QUALITY_FAST, fu.DELIVERY, fu.INTERMEDIATE):
        args = fu.video_encode_args(tier)
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"
        assert args[args.index("-profile:v") + 1] == "high"
        assert "lanczos" in args[args.index("-sws_flags") + 1]
    assert fu.video_encode_args(fu.INTERMEDIATE)[5] == "14"   # near-lossless intermediates
    assert fu.video_encode_args(fu.QUALITY)[5] == "17"


def test_hd_respects_x264_preset_override(hd, monkeypatch):
    monkeypatch.setenv("X264_PRESET", "veryfast")
    assert fu.video_encode_args(fu.QUALITY)[3] == "veryfast"
    assert fu._X264_HD_ARGS[fu.QUALITY][3] == "medium"        # not mutated


def test_hd_audio_chain_and_rate(hd):
    args = fu.audio_encode_args()
    chain = args[args.index("-af") + 1]
    assert chain.startswith("highpass") and chain.endswith(fu.LOUDNORM_FILTER)
    assert args[args.index("-ar") + 1] == "48000"
    assert args[args.index("-b:a") + 1] == "192k"


def test_hd_audio_switches(hd, monkeypatch):
    monkeypatch.setenv("SPEECH_CLEANUP", "0")
    assert fu.hd_audio_filter() == fu.LOUDNORM_FILTER
    monkeypatch.setenv("AUDIO_NORMALIZE", "0")
    assert fu.hd_audio_args()[:2] == ["-c:a", "aac"]


def test_processed_audio_only_gets_join_fades(hd):
    chain = fu.hd_audio_filter(processed=True, fade_duration=10.0)
    assert "loudnorm" not in chain and "afftdn" not in chain
    assert chain == "afade=t=in:d=0.012,afade=t=out:st=9.988:d=0.012"
    assert fu.hd_audio_filter(processed=True) is None
    assert fu.hd_audio_filter(processed=True, fade_duration=0.02) is None  # too short to fade


@pytest.mark.parametrize("src,aspect,expected", [
    ((3840, 2160), 9 / 16, (1080, 1920)),     # 4K no longer ships 1216x2160
    ((1920, 1080), 9 / 16, (1080, 1920)),
    ((1280, 720), 9 / 16, (1080, 1920)),
    ((1920, 1080), 1.0, (1080, 1080)),
])
def test_hd_delivery_size_is_fixed(hd, src, aspect, expected):
    assert reframe_v2.delivery_size(*src, aspect) == expected


def test_legacy_delivery_size_unchanged(monkeypatch):
    monkeypatch.delenv("RENDER_PRESET", raising=False)
    assert reframe_v2.delivery_size(3840, 2160, 9 / 16) == (1216, 2160)


def test_recut_fast_path_does_not_reprocess_audio(hd):
    segs = [{"start": 0.0, "end": 5.0}, {"start": 8.0, "end": 12.0}]
    cmds = recut.cut_commands("in.mp4", segs, ["a.mp4", "b.mp4"], audio_processed=True)
    for cmd, seg in zip(cmds, segs):
        chain = cmd[cmd.index("-af") + 1]
        assert "loudnorm" not in chain and chain.startswith("afade=t=in")
    source = recut.cut_commands("src.mp4", segs[:1], ["a.mp4"])[0]
    assert "loudnorm" in source[source.index("-af") + 1]
    assert "afade" not in source[source.index("-af") + 1]     # one piece: no join


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_hd_args_encode_a_real_file(hd, tmp_path):
    out = tmp_path / "o.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=608x1080:rate=30",
                    "-f", "lavfi", "-i", "sine=f=300:r=44100", "-t", "1",
                    "-vf", "scale=1080:1920", *fu.video_encode_args(fu.QUALITY_FAST),
                    *fu.audio_encode_args(), "-shortest", str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=width,height,pix_fmt,profile,sample_rate", "-of",
                            "default=nw=1", str(out)], capture_output=True, text=True).stdout
    assert "width=1080" in probe and "height=1920" in probe
    assert "pix_fmt=yuv420p" in probe and "profile=High" in probe
    assert "sample_rate=48000" in probe
