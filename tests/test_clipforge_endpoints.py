"""HTTP tests for the ClipForge clip endpoints: review, asset upload, package."""

import asyncio
import json
import shutil
import subprocess

import httpx
import pytest

app_module = pytest.importorskip("app")

JOB_ID = "clipforge-endpoint-test-job"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _request(method, path, json_body=None, files=None, data=None):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.request(method, path, json=json_body, files=files, data=data)
    return asyncio.run(_do())


@pytest.fixture()
def job(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    job_dir = out_root / JOB_ID
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    clip = {"start": 0.0, "end": 3.0, "video_url": f"/videos/{JOB_ID}/t_clip_1.mp4",
            "topic": "Habits"}
    meta = job_dir / "t_metadata.json"
    meta.write_text(json.dumps({"shorts": [clip], "output_format": "auto"}))
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=540x960:rate=30", "-f", "lavfi", "-i",
                        "sine=f=300:r=48000", "-t", "3", "-c:v", "libx264", "-preset",
                        "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        str(job_dir / "t_clip_1.mp4")], check=True)
    app_module.jobs[JOB_ID] = {"status": "completed", "logs": [],
                               "result": {"clips": [dict(clip)]}, "user_id": None,
                               "watermark": False}
    try:
        yield {"dir": job_dir, "meta": meta}
    finally:
        app_module.jobs.pop(JOB_ID, None)


def _meta(job):
    return json.loads(job["meta"].read_text())


def test_review_persists_and_clears(job):
    r = _request("POST", "/api/clip/review",
                 {"job_id": JOB_ID, "clip_index": 0, "decision": "keep", "note": "great hook"})
    assert r.status_code == 200
    saved = _meta(job)["shorts"][0]["review"]
    assert saved["decision"] == "keep" and saved["note"] == "great hook"
    assert app_module.jobs[JOB_ID]["result"]["clips"][0]["review"]["decision"] == "keep"
    r = _request("POST", "/api/clip/review", {"job_id": JOB_ID, "clip_index": 0, "decision": "none"})
    assert r.status_code == 200 and "review" not in _meta(job)["shorts"][0]


def test_review_rejects_bad_input(job):
    assert _request("POST", "/api/clip/review",
                    {"job_id": JOB_ID, "clip_index": 0, "decision": "love"}).status_code == 400
    assert _request("POST", "/api/clip/review",
                    {"job_id": JOB_ID, "clip_index": 7, "decision": "keep"}).status_code == 404
    assert _request("POST", "/api/clip/review",
                    {"job_id": "nope", "clip_index": 0, "decision": "keep"}).status_code == 404


def test_review_waits_for_a_running_job(job):
    app_module.jobs[JOB_ID]["status"] = "processing"
    assert _request("POST", "/api/clip/review",
                    {"job_id": JOB_ID, "clip_index": 0, "decision": "keep"}).status_code == 409


def test_asset_upload_validates_type_and_content(job):
    bad_ext = _request("POST", "/api/clip/asset", data={"job_id": JOB_ID, "kind": "logo"},
                       files={"file": ("x.svg", b"<svg/>", "image/svg+xml")})
    assert bad_ext.status_code == 400
    fake_png = _request("POST", "/api/clip/asset", data={"job_id": JOB_ID, "kind": "logo"},
                        files={"file": ("x.png", b"not really a png", "image/png")})
    assert fake_png.status_code == 400
    assert not list(job["dir"].glob("asset_*"))          # refused files are deleted
    bad_kind = _request("POST", "/api/clip/asset", data={"job_id": JOB_ID, "kind": "script"},
                        files={"file": ("x.png", b"x", "image/png")})
    assert bad_kind.status_code == 400


def test_package_refuses_foreign_asset_names(job):
    for name in ("../../etc/passwd", "asset_logo_123.png", "t_clip_1.mp4"):
        r = _request("POST", "/api/clip/package",
                     {"job_id": JOB_ID, "clip_index": 0, "logo_asset": name})
        assert r.status_code == 400, name


@needs_ffmpeg
def test_upload_then_package_end_to_end(job, tmp_path):
    from PIL import Image
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 50), (255, 0, 0, 200)).save(logo)
    voice = tmp_path / "vo.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=600:r=44100",
                    "-t", "1.5", str(voice)], check=True)
    r = _request("POST", "/api/clip/asset", data={"job_id": JOB_ID, "kind": "logo"},
                 files={"file": ("logo.png", logo.read_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    logo_asset = r.json()["asset"]
    r = _request("POST", "/api/clip/asset", data={"job_id": JOB_ID, "kind": "voiceover"},
                 files={"file": ("vo.wav", voice.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json()["duration"] == pytest.approx(1.5, abs=0.1)
    voice_asset = r.json()["asset"]

    r = _request("POST", "/api/clip/package", {
        "job_id": JOB_ID, "clip_index": 0, "intro_title": "Habits", "outro_text": "Follow",
        "credit_text": "Source: test", "logo_asset": logo_asset,
        "voiceover_asset": voice_asset, "voiceover_mode": "intro"})
    assert r.status_code == 200, r.text
    final = r.json()["final"]
    assert (job["dir"] / final["file"]).is_file()
    assert final["qc"]["status"] in ("PASS", "WARN")
    assert final["recipe"]["elements"]["voiceover"] == "intro"
    assert _meta(job)["shorts"][0]["final"]["file"] == final["file"]


# --- rights + YouTube publishing (Google mocked at youtube_publisher.upload) ---
@pytest.fixture()
def yt(monkeypatch, tmp_path):
    import youtube_publisher as ytp
    monkeypatch.setenv("CLIPFORGE_STATE_DIR", str(tmp_path / "state"))
    for name in ("RIGHTS_DEFAULT_CATEGORY", "YOUTUBE_AUTO_UPLOAD", "GEMINI_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    uploads = []

    def fake_upload(path, body, http, progress=None):
        uploads.append((path, body))
        return {"video_id": "VID9", "privacy": body["status"]["privacyStatus"],
                "url": "https://youtube.com/shorts/VID9", "upload_url": "secret-session"}
    monkeypatch.setattr(ytp, "upload", fake_upload)
    return uploads


def test_rights_record_and_audit_log(job, yt):
    r = _request("POST", "/api/job/rights", {"job_id": JOB_ID, "category": "cc",
                                              "license": "CC BY 4.0", "attribution": "Show by X"})
    assert r.status_code == 200 and r.json()["public_allowed"] is True
    meta = _meta(job)
    assert meta["rights"]["category"] == "cc" and len(meta["rights_log"]) == 1
    assert _request("POST", "/api/job/rights",
                    {"job_id": JOB_ID, "category": "mine"}).status_code == 400
    got = _request("GET", f"/api/job/{JOB_ID}/rights").json()
    assert got["rights"]["license"] == "CC BY 4.0"


@needs_ffmpeg
def test_public_upload_blocked_without_rights_private_allowed(job, yt):
    r = _request("POST", "/api/youtube/upload", {"job_id": JOB_ID, "clip_index": 0,
                                                  "title": "T", "privacy": "public"})
    assert r.status_code == 400 and "rights" in r.json()["detail"]
    assert yt == []
    r = _request("POST", "/api/youtube/upload", {"job_id": JOB_ID, "clip_index": 0,
                                                  "title": "T", "privacy": "private"})
    assert r.status_code == 200, r.text
    rec = r.json()["youtube"]
    assert rec["video_id"] == "VID9" and "upload_url" not in rec
    assert _meta(job)["shorts"][0]["youtube"]["video_id"] == "VID9"


@needs_ffmpeg
def test_upload_refuses_qc_fail_and_drafts_metadata_without_model(job, yt):
    meta = _meta(job)
    meta["shorts"][0]["qc"] = {"status": "FAIL", "checks": []}
    job["meta"].write_text(json.dumps(meta))
    r = _request("POST", "/api/youtube/upload", {"job_id": JOB_ID, "clip_index": 0})
    assert r.status_code == 400 and "quality" in r.json()["detail"]
    meta["shorts"][0]["qc"] = {"status": "PASS", "checks": []}
    meta["shorts"][0]["video_title_for_youtube_short"] = "Fallback title"
    job["meta"].write_text(json.dumps(meta))
    r = _request("POST", "/api/youtube/upload", {"job_id": JOB_ID, "clip_index": 0})
    assert r.status_code == 200, r.text
    assert yt[-1][1]["snippet"]["title"] == "Fallback title"


def test_scheduled_upload_needs_future_time(job, yt):
    _request("POST", "/api/job/rights", {"job_id": JOB_ID, "category": "own"})
    r = _request("POST", "/api/youtube/upload", {"job_id": JOB_ID, "clip_index": 0, "title": "T",
                                                  "publish_at": "2001-01-01T00:00:00Z"})
    assert r.status_code == 400


def test_youtube_status_without_connection(yt):
    d = _request("GET", "/api/youtube/status").json()
    assert d["connected"] is False and d["channel"] is None
