"""Direct YouTube upload through the YouTube Data API v3 (ClipForge Phase 5).

Your own Google OAuth client ("Desktop app"), your own channel, no third
party in between. Pieces:

* OAuth installed-app flow with PKCE on a loopback port
  (``python youtube_publisher.py auth``). The refresh token lives in
  ``.clipforge/youtube_token.json`` (git-ignored, mode 600) and never leaves
  the server: the browser only ever sees "connected" and the channel title.
* Resumable uploads (``videos.insert``), resumed from the stored session URL
  after a crash; one publish record per clip file so a retry never creates a
  second video (idempotency).
* Safe defaults: private unless configured otherwise; a scheduled or public
  upload needs a recorded rights category that allows it.
* A local daily counter so the 10,000-unit API quota (~1,600 per upload) is
  not exhausted by a burst of auto-uploads.

All network calls go through an injectable ``httpx.Client`` for tests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx

SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_URI = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
API = "https://www.googleapis.com/youtube/v3"
CHUNK = 8 * 1024 * 1024
PRIVACY = ("private", "unlisted", "public")
QUOTA_UNITS_PER_UPLOAD = 1600
DEFAULT_MAX_PER_DAY = 6

# Rights categories (recorded per job by the creator; not legal advice).
RIGHTS_CATEGORIES = ("own", "licensed", "cc", "public_domain", "unclear")
# Allowed to go public / scheduled-public. "cc" also needs attribution and a
# licence that allows commercial adaptation (no NC/ND) when monetised.
PUBLIC_OK = ("own", "licensed", "cc", "public_domain")

_lock = threading.Lock()


class YouTubeError(RuntimeError):
    """An upload/auth problem with a message safe to show the user."""


class NotConnected(YouTubeError):
    pass


# --------------------------------------------------------------------------- #
# Local state
# --------------------------------------------------------------------------- #
def state_dir() -> str:
    path = os.environ.get("CLIPFORGE_STATE_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".clipforge")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _token_path():
    return os.path.join(state_dir(), "youtube_token.json")


def _quota_path():
    return os.path.join(state_dir(), "youtube_quota.json")


def _write_private(path, data):
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def client_file() -> str:
    return os.environ.get("YOUTUBE_OAUTH_CLIENT_FILE", "").strip()


def load_client(path: Optional[str] = None) -> dict:
    """client_id/secret from Google's downloaded JSON (installed or web)."""
    path = path or client_file()
    if not path or not os.path.isfile(path):
        raise YouTubeError("YouTube OAuth client file not configured "
                           "(youtube.oauth_client_file in clipforge.config.yaml)")
    data = _read_json(path) or {}
    block = data.get("installed") or data.get("web") or {}
    if not block.get("client_id") or not block.get("client_secret"):
        raise YouTubeError("the OAuth client file has no client_id/client_secret")
    return {"client_id": block["client_id"], "client_secret": block["client_secret"]}


def load_token() -> Optional[dict]:
    return _read_json(_token_path())


def save_token(token: dict):
    keep = {k: token[k] for k in ("access_token", "refresh_token", "expires_at", "scope")
            if k in token}
    _write_private(_token_path(), keep)


def disconnect():
    for path in (_token_path(),):
        if os.path.exists(path):
            os.remove(path)


# --------------------------------------------------------------------------- #
# OAuth (installed app, PKCE, loopback)
# --------------------------------------------------------------------------- #
def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def auth_url(client: dict, redirect_uri: str, state: str, challenge: str) -> str:
    q = {
        "client_id": client["client_id"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": " ".join(SCOPES), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    }
    return f"{AUTH_URI}?{urllib.parse.urlencode(q)}"


def _token_from_response(resp: httpx.Response, previous: Optional[dict] = None) -> dict:
    if resp.status_code != 200:
        raise YouTubeError(f"Google refused the token request ({resp.status_code})")
    data = resp.json()
    token = dict(previous or {})
    token["access_token"] = data["access_token"]
    token["expires_at"] = int(time.time()) + int(data.get("expires_in", 3600)) - 60
    if data.get("refresh_token"):
        token["refresh_token"] = data["refresh_token"]
    if data.get("scope"):
        token["scope"] = data["scope"]
    return token


def exchange_code(client, code, redirect_uri, verifier, http: httpx.Client) -> dict:
    resp = http.post(TOKEN_URI, data={
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri})
    token = _token_from_response(resp)
    if not token.get("refresh_token"):
        raise YouTubeError("Google returned no refresh token; remove the app's access "
                           "at myaccount.google.com/permissions and connect again")
    return token


def access_token(http: httpx.Client) -> str:
    """A valid access token, refreshed when needed."""
    with _lock:
        token = load_token()
        if not token or not token.get("refresh_token"):
            raise NotConnected("YouTube is not connected: run "
                               "`python youtube_publisher.py auth` once")
        if token.get("access_token") and token.get("expires_at", 0) > time.time():
            return token["access_token"]
        client = load_client()
        resp = http.post(TOKEN_URI, data={
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
        if resp.status_code in (400, 401):
            raise NotConnected("YouTube access was revoked or expired; connect again")
        token = _token_from_response(resp, token)
        save_token(token)
        return token["access_token"]


def run_local_auth(open_browser=True, port=0, http: Optional[httpx.Client] = None):
    """Interactive one-time connection from a terminal on this machine."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    client = load_client()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_error(400, "state mismatch")
                return
            result["code"] = q.get("code", [""])[0]
            result["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h3>ClipForge: YouTube connected. You can close this tab.</h3>")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = auth_url(client, redirect, state, challenge)
    print("\nOpen this URL in a browser signed in to the channel's Google account:\n")
    print(url + "\n")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    while "code" not in result:
        server.handle_request()
    server.server_close()
    if result.get("error") or not result.get("code"):
        raise YouTubeError(f"authorization failed: {result.get('error') or 'no code'}")
    own = http is None
    http = http or httpx.Client(timeout=60)
    try:
        token = exchange_code(client, result["code"], redirect, verifier, http)
        save_token(token)
        info = channel_info(http)
    finally:
        if own:
            http.close()
    return info


def channel_info(http: httpx.Client) -> dict:
    resp = http.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                    headers={"Authorization": f"Bearer {access_token(http)}"})
    if resp.status_code != 200:
        raise YouTubeError(f"could not read the channel ({resp.status_code})")
    items = resp.json().get("items") or []
    if not items:
        raise YouTubeError("this Google account has no YouTube channel")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


# --------------------------------------------------------------------------- #
# Rights gate
# --------------------------------------------------------------------------- #
def clean_rights(raw: dict) -> dict:
    """Validate a rights record from the dashboard. Raises ValueError."""
    category = str(raw.get("category") or "").strip()
    if category not in RIGHTS_CATEGORIES:
        raise ValueError(f"category must be one of {RIGHTS_CATEGORIES}")
    out = {
        "category": category,
        "license": str(raw.get("license") or "").strip()[:120],
        "attribution": str(raw.get("attribution") or "").strip()[:300],
        "evidence": str(raw.get("evidence") or "").strip()[:500],
        "notes": str(raw.get("notes") or "").strip()[:500],
        "intended_use": str(raw.get("intended_use") or "monetized").strip()[:40],
    }
    if category == "cc" and not out["license"]:
        raise ValueError("a Creative Commons source needs its licence (e.g. CC BY 4.0)")
    return out


def rights_allow_public(rights: Optional[dict]):
    """(allowed, reason). Only the creator's recorded category decides."""
    if not rights or rights.get("category") not in PUBLIC_OK:
        return False, ("record the source's rights first (own / licensed / CC / public "
                       "domain); unclear sources upload as private or unlisted only")
    if rights["category"] == "cc":
        lic = rights.get("license", "").upper().replace("-", " ")
        if rights.get("intended_use", "monetized") == "monetized" and \
                (" NC" in f" {lic}" or " ND" in f" {lic}"):
            return False, "a NonCommercial/NoDerivatives licence does not allow a monetised edit"
        if not rights.get("attribution") and not lic.replace(" ", "").startswith("CC0"):
            return False, "a CC BY source needs the attribution text"
    return True, ""


def shorts_warning(duration: float, rights: Optional[dict]) -> Optional[str]:
    if duration > 60 and (not rights or rights.get("category") != "own"):
        return ("Shorts over 60 s from someone else's video are blocked worldwide if a "
                "Content ID claim is made; keep third-party Shorts under 60 s when unsure.")
    return None


# --------------------------------------------------------------------------- #
# Quota
# --------------------------------------------------------------------------- #
def max_per_day() -> int:
    try:
        return max(1, int(os.environ.get("YOUTUBE_MAX_PER_DAY", DEFAULT_MAX_PER_DAY)))
    except ValueError:
        return DEFAULT_MAX_PER_DAY


def _today():
    # The API's quota resets at midnight Pacific time.
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001  (no tz database: standard time is close enough)
        return (now - timedelta(hours=8)).strftime("%Y-%m-%d")


def uploads_today() -> int:
    data = _read_json(_quota_path()) or {}
    return int(data.get(_today(), 0))


def note_upload():
    with _lock:
        data = _read_json(_quota_path()) or {}
        today = _today()
        data = {today: int(data.get(today, 0)) + 1}
        _write_private(_quota_path(), data)


# --------------------------------------------------------------------------- #
# Publish records (idempotency)
# --------------------------------------------------------------------------- #
def record_path(video_path: str) -> str:
    return video_path + ".publish.json"


def read_record(video_path: str) -> Optional[dict]:
    return _read_json(record_path(video_path))


def _file_key(video_path):
    st = os.stat(video_path)
    return hashlib.sha256(f"{os.path.basename(video_path)}:{st.st_size}".encode()).hexdigest()[:24]


def build_body(title, description, tags=None, privacy="private", publish_at=None,
               category_id="22", made_for_kids=False, language=None, synthetic=False):
    if privacy not in PRIVACY:
        raise ValueError(f"privacy must be one of {PRIVACY}")
    title = " ".join(str(title or "").replace("<", "").replace(">", "").split())[:100]
    if not title:
        raise ValueError("a title is required")
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(made_for_kids),
              "containsSyntheticMedia": bool(synthetic)}
    if publish_at:
        # YouTube publishes a private video at publishAt.
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    snippet = {"title": title,
               "description": str(description or "").replace("<", "").replace(">", "")[:4900],
               "categoryId": str(category_id or "22")}
    clean_tags, used = [], 0
    for t in (tags or []):
        t = str(t).strip().lstrip("#").replace("<", "").replace(">", "")[:60]
        # YouTube refuses a tag list over 500 characters (a tag with a space counts its quotes).
        cost = len(t) + 1 + (2 if " " in t else 0)
        if not t or t in clean_tags or used + cost > 450 or len(clean_tags) >= 20:
            continue
        clean_tags.append(t)
        used += cost
    if clean_tags:
        snippet["tags"] = clean_tags
    if language:
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = str(language)[:8]
    return {"snippet": snippet, "status": status}


def _body_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()[:24]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open_session(video_path, body, http, key, size) -> dict:
    if uploads_today() >= max_per_day():
        raise YouTubeError(f"daily upload limit reached ({max_per_day()}); "
                           "it resets at midnight Pacific time")
    resp = http.post(UPLOAD_URI, json=body, headers={
        "Authorization": f"Bearer {access_token(http)}",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size)})
    if resp.status_code != 200 or not resp.headers.get("Location"):
        raise YouTubeError(_api_error("start the upload", resp))
    record = {"key": key, "state": "uploading", "upload_url": resp.headers["Location"],
              "body_hash": _body_hash(body),
              "privacy": body["status"]["privacyStatus"],
              "publish_at": body["status"].get("publishAt"),
              "title": body["snippet"]["title"],
              "started_at": _now_iso()}
    _write_private(record_path(video_path), record)
    note_upload()
    return record


def _finish(video_path, record, video, progress) -> dict:
    record.update(state="uploaded", video_id=video["id"],
                  url=f"https://youtube.com/shorts/{video['id']}",
                  finished_at=_now_iso())
    record.pop("upload_url", None)
    _write_private(record_path(video_path), record)
    if progress:
        progress(1.0)
    return record


UPLOAD_RETRIES = 4


def upload(video_path: str, body: dict, http: httpx.Client,
           progress: Optional[Callable[[float], None]] = None) -> dict:
    """Upload once per file. Returns the publish record (with ``video_id``).

    A finished record is returned as ``duplicate`` (nothing is sent). An open session is
    resumed only for the same request body; a changed title, privacy or schedule opens a
    new session, so a retry can never publish with settings the caller no longer asked for.
    """
    if not os.path.isfile(video_path):
        raise YouTubeError("video file not found")
    key = _file_key(video_path)
    record = read_record(video_path) or {}
    if record.get("key") == key and record.get("video_id"):
        return {**record, "duplicate": True}
    size = os.path.getsize(video_path)
    resumable = (record.get("key") == key and record.get("upload_url")
                 and record.get("body_hash") == _body_hash(body))
    if not resumable:
        record = _open_session(video_path, body, http, key, size)
    failures = 0
    offset = None           # None: ask the server where the session stands
    with open(video_path, "rb") as f:
        while True:
            try:
                if offset is None:
                    offset, video = _session_state(http, record["upload_url"], size)
                    if video:
                        # The last chunk was accepted but its answer never arrived.
                        return _finish(video_path, record, video, progress)
                    if offset < 0:
                        # The session expired: start again from byte 0.
                        record = _open_session(video_path, body, http, key, size)
                        offset = 0
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                resp = http.put(record["upload_url"], content=chunk, headers={
                    "Authorization": f"Bearer {access_token(http)}",
                    "Content-Range": f"bytes {offset}-{end}/{size}"}, timeout=600)
            except httpx.HTTPError as e:
                failures += 1
                if failures > UPLOAD_RETRIES:
                    raise YouTubeError(f"the upload lost its connection ({type(e).__name__}); "
                                       "run it again to resume") from e
                time.sleep(min(2 ** failures, 20))
                offset = None
                continue
            if resp.status_code == 308:
                rng = resp.headers.get("Range", "")
                offset = int(rng.split("-")[-1]) + 1 if rng else 0
                failures = 0
                if progress:
                    progress(offset / size)
                continue
            if resp.status_code in (200, 201):
                return _finish(video_path, record, resp.json(), progress)
            if resp.status_code in (500, 502, 503, 504) and failures < UPLOAD_RETRIES:
                failures += 1
                time.sleep(min(2 ** failures, 20))
                offset = None
                continue
            raise YouTubeError(_api_error("upload the video", resp))


def _session_state(http, session, size):
    """(bytes the server holds, finished video or None). -1 bytes: the session is gone."""
    resp = http.put(session, headers={"Authorization": f"Bearer {access_token(http)}",
                                      "Content-Range": f"bytes */{size}"})
    if resp.status_code == 308:
        rng = resp.headers.get("Range", "")
        return (int(rng.split("-")[-1]) + 1 if rng else 0), None
    if resp.status_code in (200, 201):
        try:
            video = resp.json()
        except ValueError:
            video = None
        if isinstance(video, dict) and video.get("id"):
            return size, video
        raise YouTubeError("YouTube finished the upload but did not return the video id; "
                           "check YouTube Studio before uploading again")
    if resp.status_code in (404, 410):
        return -1, None
    if resp.status_code in (500, 502, 503, 504):
        raise httpx.TransportError(f"session probe {resp.status_code}")
    raise YouTubeError(_api_error("resume the upload", resp))


def _api_error(what, resp):
    try:
        err = resp.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason") or err.get("status") or ""
        message = err.get("message") or ""
    except ValueError:
        reason, message = "", resp.text[:120]
    if reason in ("quotaExceeded", "uploadLimitExceeded"):
        return "YouTube's daily upload quota is used up; try again tomorrow"
    return f"could not {what} ({resp.status_code} {reason}): {message[:160]}"


# --------------------------------------------------------------------------- #
# Auto-upload planning
# --------------------------------------------------------------------------- #
def _env_int(name, default):
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


def settings() -> dict:
    env = os.environ
    times = [t.strip() for t in env.get("YOUTUBE_SCHEDULE_TIMES", "").split(",") if t.strip()]
    return {
        "auto_upload": env.get("YOUTUBE_AUTO_UPLOAD", "0").strip() == "1",
        "privacy": env.get("YOUTUBE_PRIVACY", "private").strip() or "private",
        "schedule_times": times,
        "timezone": env.get("YOUTUBE_TIMEZONE", "UTC").strip() or "UTC",
        "max_per_job": _env_int("YOUTUBE_MAX_PER_JOB", 3),
        "min_score": _env_int("YOUTUBE_MIN_SCORE", 60),
        "require_keep": env.get("YOUTUBE_REQUIRE_KEEP", "0").strip() == "1",
        "use_final": env.get("YOUTUBE_USE_FINAL", "1").strip() != "0",
        "category_id": env.get("YOUTUBE_CATEGORY_ID", "22").strip() or "22",
        "default_tags": [t.strip() for t in env.get("YOUTUBE_DEFAULT_TAGS", "").split(",")
                         if t.strip()],
    }


def pick_clips(shorts: list, cfg: dict) -> list:
    """Indices worth auto-uploading, best first."""
    ranked = []
    for i, c in enumerate(shorts):
        review = (c.get("review") or {}).get("decision")
        if review == "reject":
            continue
        if cfg["require_keep"] and review not in ("keep", "pin"):
            continue
        if ((c.get("qc") or {}).get("status")) == "FAIL":
            continue
        try:
            score = int(c.get("predicted_score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < cfg["min_score"] and review not in ("keep", "pin"):
            continue
        ranked.append((review == "pin", score, -i, i))
    ranked.sort(reverse=True)
    return [i for *_, i in ranked[:max(0, cfg["max_per_job"])]]


def next_slots(n: int, times: list, tz_name: str, taken: set, now: Optional[datetime] = None):
    """The next ``n`` free publish times (RFC 3339 UTC) at the daily ``times``."""
    if not times or n <= 0:
        return []
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    parsed = []
    for t in times:
        try:
            hh, mm = (int(x) for x in str(t).strip().split(":")[:2])
        except ValueError:
            continue
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            parsed.append((hh, mm))
    if not parsed:
        return []
    out = []
    day = now.date()
    for _ in range(366):
        for hh, mm in sorted(parsed):
            slot = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if slot <= now + timedelta(minutes=30):
                continue
            iso = slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if iso not in taken:
                out.append(iso)
                if len(out) == n:
                    return out
        day += timedelta(days=1)
    return out


def taken_slots() -> set:
    data = _read_json(os.path.join(state_dir(), "youtube_schedule.json")) or {}
    return set(data.get("slots") or [])


def reserve_slot(iso: str):
    with _lock:
        path = os.path.join(state_dir(), "youtube_schedule.json")
        data = _read_json(path) or {}
        slots = sorted(set(data.get("slots") or []) | {iso})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_private(path, {"slots": [s for s in slots if s >= cutoff]})


def claim_slots(n: int, times: list, tz_name: str) -> list:
    """Pick and reserve the next ``n`` slots in one step, so two jobs finishing together
    never get the same publish time."""
    with _lock:
        path = os.path.join(state_dir(), "youtube_schedule.json")
        data = _read_json(path) or {}
        taken = set(data.get("slots") or [])
        slots = next_slots(n, times, tz_name, taken)
        if slots:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            _write_private(path, {"slots": [s for s in sorted(taken | set(slots)) if s >= cutoff]})
        return slots


def release_slot(iso: str):
    """Give back a slot whose upload did not happen."""
    with _lock:
        path = os.path.join(state_dir(), "youtube_schedule.json")
        data = _read_json(path) or {}
        _write_private(path, {"slots": [s for s in (data.get("slots") or []) if s != iso]})


def describe() -> dict:
    """Connection state for the dashboard (no secrets)."""
    cfg = settings()
    token = load_token() or {}
    return {
        "client_configured": bool(client_file() and os.path.isfile(client_file())),
        "connected": bool(token.get("refresh_token")),
        "uploads_today": uploads_today(),
        "max_per_day": max_per_day(),
        "auto_upload": cfg["auto_upload"],
        "privacy": cfg["privacy"],
        "schedule_times": cfg["schedule_times"],
        "timezone": cfg["timezone"],
    }


def _main(argv):
    import argparse
    parser = argparse.ArgumentParser(description="ClipForge YouTube connection")
    parser.add_argument("command", choices=("auth", "status", "disconnect"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        import clipforge_config
        clipforge_config.apply()
    except Exception as e:  # noqa: BLE001
        print(f"config: {e}")
    if args.command == "auth":
        info = run_local_auth(open_browser=not args.no_browser)
        print(f"Connected to channel: {info['title']}")
    elif args.command == "disconnect":
        disconnect()
        print("Disconnected (local token removed).")
    else:
        print(json.dumps(describe(), indent=2))
        if (load_token() or {}).get("refresh_token"):
            with httpx.Client(timeout=30) as http:
                try:
                    print("channel:", channel_info(http)["title"])
                except YouTubeError as e:
                    print("channel check failed:", e)


if __name__ == "__main__":
    _main(sys.argv[1:])
