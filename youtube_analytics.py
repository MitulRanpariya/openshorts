"""Performance of uploaded clips and editorial observations (ClipForge Phase 6).

Only metrics the official APIs return are shown:

* YouTube Data API ``videos.list`` (statistics): views, likes, comments.
* YouTube Analytics API (``yt-analytics.readonly``): views, estimated minutes
  watched, average view duration, average view percentage, subscribers
  gained, per video. A metric the API does not return is ``None`` and the
  dashboard says "unavailable": nothing is estimated or scraped.

``observations`` groups clips by editorial features (duration band, strongest
score dimension, topic present, packaged or not) and reports averages WITH the
sample size and the date range. It describes this channel's recent sample; it
never claims a cause and never changes the ranking weights on its own.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Optional

import httpx

import youtube_publisher as ytp

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
ANALYTICS_METRICS = ("views,estimatedMinutesWatched,averageViewDuration,"
                     "averageViewPercentage,subscribersGained")
MIN_GROUP = 3       # smaller groups are listed but marked "too few to compare"


def video_stats(ids: list, http: httpx.Client) -> dict:
    """{video_id: {views, likes, comments}} from videos.list (50 per call)."""
    out = {}
    token = ytp.access_token(http)
    for i in range(0, len(ids), 50):
        resp = http.get(f"{ytp.API}/videos", params={
            "part": "statistics", "id": ",".join(ids[i:i + 50])},
            headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            raise ytp.YouTubeError(ytp._api_error("read video statistics", resp))
        for item in resp.json().get("items") or []:
            st = item.get("statistics") or {}
            out[item["id"]] = {k: _int(st.get(src)) for k, src in
                               (("views", "viewCount"), ("likes", "likeCount"),
                                ("comments", "commentCount"))}
    return out


def analytics(video_id: str, published: str, http: httpx.Client) -> Optional[dict]:
    """Analytics API metrics for one video since it went up; None if refused."""
    start = (published or "")[:10] or (datetime.now(timezone.utc) - timedelta(days=28)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resp = http.get(ANALYTICS, params={
        "ids": "channel==MINE", "startDate": start, "endDate": end,
        "metrics": ANALYTICS_METRICS, "filters": f"video=={video_id}"},
        headers={"Authorization": f"Bearer {ytp.access_token(http)}"})
    if resp.status_code != 200:
        return None
    data = resp.json()
    headers = [h["name"] for h in data.get("columnHeaders") or []]
    rows = data.get("rows") or []
    if not rows:
        return {name: None for name in ANALYTICS_METRICS.split(",")} | {"range": [start, end]}
    return {**{h: rows[0][i] for i, h in enumerate(headers)}, "range": [start, end]}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def uploaded_clips(output_root: str) -> list:
    """(metadata_path, clip_index, clip) for every clip with a video id."""
    found = []
    for path in glob.glob(os.path.join(output_root, "*", "*_metadata.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for i, clip in enumerate(data.get("shorts") or []):
            if (clip.get("youtube") or {}).get("video_id"):
                found.append((path, i, clip))
    return found


def refresh(output_root: str, http: httpx.Client) -> int:
    """Fetch current metrics for every uploaded clip; store them in metadata."""
    clips = uploaded_clips(output_root)
    if not clips:
        return 0
    ids = [c["youtube"]["video_id"] for _, _, c in clips]
    stats = video_stats(ids, http)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_path = {}
    for path, index, clip in clips:
        vid = clip["youtube"]["video_id"]
        metrics = {"fetched_at": now, "source": "YouTube Data API + Analytics API",
                   "views": None, "likes": None, "comments": None,
                   **(stats.get(vid) or {})}
        extra = analytics(vid, clip["youtube"].get("finished_at") or "", http)
        if extra:
            metrics["analytics"] = extra
        by_path.setdefault(path, []).append((index, metrics))
    from jsonfile import write_json_atomic
    for path, updates in by_path.items():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for index, metrics in updates:
            data["shorts"][index].setdefault("youtube", {})["metrics"] = metrics
        write_json_atomic(path, data, indent=2)
    return len(clips)


# --------------------------------------------------------------------------- #
# Observations (pure)
# --------------------------------------------------------------------------- #
def _features(clip):
    dur = float(clip.get("end", 0)) - float(clip.get("start", 0))
    band = "under 30 s" if dur < 30 else "30-45 s" if dur < 45 else "45-60 s" if dur <= 60 else "over 60 s"
    breakdown = clip.get("score_breakdown") or {}
    strongest = max(breakdown, key=breakdown.get) if breakdown else None
    return {
        "duration": band,
        "strongest dimension": strongest or "not scored",
        "packaging": "finished short" if (clip.get("final") or {}).get("file") else "plain clip",
        "context flags": "flagged" if clip.get("context_flags") else "clean",
    }


def _value(clip, metric):
    m = (clip.get("youtube") or {}).get("metrics") or {}
    if metric in m and m[metric] is not None:
        return m[metric]
    return (m.get("analytics") or {}).get(metric)


def observations(clips: list, metric: str = "averageViewPercentage") -> dict:
    """Averages of ``metric`` per feature value, with sample sizes."""
    rows = [(c, _value(c, metric)) for c in clips]
    rows = [(c, v) for c, v in rows if isinstance(v, (int, float))]
    fetched = sorted(((c.get("youtube") or {}).get("metrics") or {}).get("fetched_at", "")
                     for c, _ in rows)
    out = {"metric": metric, "sample_size": len(rows),
           "fetched": [fetched[0], fetched[-1]] if fetched else None,
           "note": ("Averages over this channel's uploaded clips. They describe what "
                    "happened, not why; small groups are not comparable."),
           "groups": {}}
    for clip, value in rows:
        for feature, label in _features(clip).items():
            out["groups"].setdefault(feature, {}).setdefault(label, []).append(value)
    for feature, labels in out["groups"].items():
        out["groups"][feature] = {
            label: {"n": len(vals), "average": round(mean(vals), 2),
                    "comparable": len(vals) >= MIN_GROUP}
            for label, vals in sorted(labels.items())}
    return out


def _main():
    import clipforge_config
    clipforge_config.apply()
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    with httpx.Client(timeout=60) as http:
        n = refresh(root, http)
    print(f"refreshed {n} uploaded clip(s)")
    clips = [c for _, _, c in uploaded_clips(root)]
    print(json.dumps(observations(clips), indent=2))


if __name__ == "__main__":
    _main()
