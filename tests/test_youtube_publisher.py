"""youtube_publisher: OAuth tokens, resumable upload, idempotency, gates.

Every Google endpoint is an httpx.MockTransport: no test talks to YouTube."""

import json
import os
import time
from datetime import datetime, timezone

import httpx
import pytest

import youtube_publisher as ytp


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_STATE_DIR", str(tmp_path / "state"))
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}))
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_FILE", str(client))
    for name in ("YOUTUBE_MAX_PER_DAY", "YOUTUBE_AUTO_UPLOAD", "YOUTUBE_SCHEDULE_TIMES"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def connect(expired=False):
    ytp.save_token({"access_token": "at-1", "refresh_token": "rt-1",
                    "expires_at": int(time.time()) + (-10 if expired else 3600)})


class FakeGoogle:
    """Token refresh + resumable upload in two chunks + channel lookup."""

    def __init__(self, fail_first_put=False):
        self.received = 0
        self.size = None
        self.calls = []
        self.fail_first_put = fail_first_put

    def __call__(self, request: httpx.Request):
        self.calls.append((request.method, str(request.url)))
        url = str(request.url)
        if url.startswith(ytp.TOKEN_URI):
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            assert body["grant_type"] in ("refresh_token", "authorization_code")
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600,
                                             **({"refresh_token": "rt-new"}
                                                if body["grant_type"] == "authorization_code" else {})})
        if url.startswith("https://www.googleapis.com/upload/"):
            meta = json.loads(request.content)
            assert meta["status"]["privacyStatus"] in ytp.PRIVACY
            self.size = int(request.headers["X-Upload-Content-Length"])
            return httpx.Response(200, headers={"Location": "https://upload.example/session/1"})
        if url.startswith("https://upload.example/session/1"):
            rng = request.headers["Content-Range"]
            if rng.startswith("bytes */"):
                if self.received == 0:
                    return httpx.Response(308)
                return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"})
            if self.fail_first_put:
                self.fail_first_put = False
                return httpx.Response(503, json={"error": {"message": "backend error"}})
            self.received += len(request.content)
            if self.received < self.size:
                return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"})
            return httpx.Response(200, json={"id": "VID123"})
        if url.startswith(f"{ytp.API}/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "My Channel"}}]})
        return httpx.Response(404)


def video(tmp_path, size=ytp.CHUNK + 1000):
    p = tmp_path / "clip.mp4"
    p.write_bytes(os.urandom(size))
    return str(p)


def test_not_connected_is_reported():
    with pytest.raises(ytp.NotConnected):
        ytp.access_token(httpx.Client(transport=httpx.MockTransport(FakeGoogle())))


def test_expired_token_is_refreshed_and_saved():
    connect(expired=True)
    fake = FakeGoogle()
    assert ytp.access_token(httpx.Client(transport=httpx.MockTransport(fake))) == "at-2"
    assert ytp.load_token()["refresh_token"] == "rt-1"         # kept across refresh
    mode = os.stat(os.path.join(ytp.state_dir(), "youtube_token.json")).st_mode & 0o777
    assert mode == 0o600


def test_exchange_code_requires_refresh_token():
    http = httpx.Client(transport=httpx.MockTransport(FakeGoogle()))
    tok = ytp.exchange_code(ytp.load_client(), "code", "http://127.0.0.1:1", "v" * 50, http)
    assert tok["refresh_token"] == "rt-new"


def test_auth_url_uses_pkce_and_offline_access():
    verifier, challenge = ytp.pkce_pair()
    url = ytp.auth_url(ytp.load_client(), "http://127.0.0.1:5555", "st", challenge)
    assert "code_challenge_method=S256" in url and "access_type=offline" in url
    assert "youtube.upload" in url and "csec" not in url


def test_resumable_upload_in_chunks_then_idempotent(tmp_path):
    connect()
    fake = FakeGoogle()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    path = video(tmp_path)
    body = ytp.build_body("A title", "desc", ["#shorts"], privacy="unlisted")
    rec = ytp.upload(path, body, http)
    assert rec["video_id"] == "VID123" and rec["state"] == "uploaded"
    assert "upload_url" not in ytp.read_record(path)
    puts = [c for c in fake.calls if c[0] == "PUT"]
    assert len(puts) >= 3                                   # probe + 2 chunks
    again = ytp.upload(path, body, http)
    assert again["duplicate"] is True and again["video_id"] == "VID123"
    assert ytp.uploads_today() == 1


def test_interrupted_upload_resumes_same_session(tmp_path):
    connect()
    fake = FakeGoogle(fail_first_put=True)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    path = video(tmp_path, size=2000)
    body = ytp.build_body("T", "", privacy="private")
    with pytest.raises(ytp.YouTubeError):
        ytp.upload(path, body, http)
    assert ytp.read_record(path)["upload_url"]
    rec = ytp.upload(path, body, http)                      # resumes, no new session
    assert rec["video_id"] == "VID123"
    starts = [c for c in fake.calls if "upload/youtube" in c[1]]
    assert len(starts) == 1 and ytp.uploads_today() == 1


def test_daily_limit_blocks_new_uploads(tmp_path, monkeypatch):
    connect()
    monkeypatch.setenv("YOUTUBE_MAX_PER_DAY", "1")
    ytp.note_upload()
    http = httpx.Client(transport=httpx.MockTransport(FakeGoogle()))
    with pytest.raises(ytp.YouTubeError, match="daily upload limit"):
        ytp.upload(video(tmp_path, 100), ytp.build_body("T", ""), http)


def test_build_body_rules():
    b = ytp.build_body("  <b>Title</b>  " + "x" * 200, "d<script>", ["#Shorts", " ", "podcast"],
                       privacy="public", publish_at="2030-01-01T10:00:00Z")
    assert len(b["snippet"]["title"]) == 100 and "<" not in b["snippet"]["title"]
    assert "<" not in b["snippet"]["description"]
    assert b["status"]["privacyStatus"] == "private"        # scheduled = private until publishAt
    assert b["snippet"]["tags"] == ["Shorts", "podcast"]
    with pytest.raises(ValueError):
        ytp.build_body("", "")
    with pytest.raises(ValueError):
        ytp.build_body("t", "", privacy="everyone")


@pytest.mark.parametrize("rights,ok", [
    (None, False),
    ({"category": "unclear"}, False),
    ({"category": "own"}, True),
    ({"category": "cc", "license": "CC BY 4.0", "attribution": "Show by X"}, True),
    ({"category": "cc", "license": "CC BY 4.0", "attribution": ""}, False),
    ({"category": "cc", "license": "CC BY-NC 4.0", "attribution": "x",
      "intended_use": "monetized"}, False),
    ({"category": "cc", "license": "CC BY-NC 4.0", "attribution": "x",
      "intended_use": "personal"}, True),
])
def test_rights_gate(rights, ok):
    assert ytp.rights_allow_public(rights)[0] is ok


def test_clean_rights_validation():
    with pytest.raises(ValueError):
        ytp.clean_rights({"category": "stolen"})
    with pytest.raises(ValueError):
        ytp.clean_rights({"category": "cc"})
    r = ytp.clean_rights({"category": "own", "notes": "x" * 900})
    assert len(r["notes"]) == 500


def test_shorts_warning_only_for_long_third_party():
    assert ytp.shorts_warning(90, {"category": "cc"})
    assert ytp.shorts_warning(90, {"category": "own"}) is None
    assert ytp.shorts_warning(45, None) is None


def test_pick_clips_respects_review_qc_and_score():
    cfg = {"max_per_job": 2, "min_score": 60, "require_keep": False}
    shorts = [
        {"predicted_score": 90, "review": {"decision": "reject"}},
        {"predicted_score": 80, "qc": {"status": "FAIL"}},
        {"predicted_score": 70},
        {"predicted_score": 40, "review": {"decision": "pin"}},
        {"predicted_score": 65},
    ]
    assert ytp.pick_clips(shorts, cfg) == [3, 2]
    assert ytp.pick_clips(shorts, {**cfg, "require_keep": True}) == [3]


def test_next_slots_skip_taken_and_past():
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)      # 17:30 in Kolkata
    slots = ytp.next_slots(3, ["18:00", "21:00"], "Asia/Kolkata",
                           {"2026-11-25T15:30:00Z"}, now=now)
    assert slots == ["2026-11-26T12:30:00Z", "2026-11-26T15:30:00Z", "2026-11-27T12:30:00Z"]
    assert ytp.next_slots(2, [], "UTC", set()) == []


def test_describe_never_exposes_tokens():
    connect()
    d = ytp.describe()
    assert d["connected"] is True and d["client_configured"] is True
    assert "rt-1" not in json.dumps(d) and "at-1" not in json.dumps(d)
