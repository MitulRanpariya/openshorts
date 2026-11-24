"""youtube_analytics: official metrics only, honest observations."""

import json

import httpx
import pytest

import youtube_analytics as yta
import youtube_publisher as ytp


@pytest.fixture(autouse=True)
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_STATE_DIR", str(tmp_path / "state"))
    ytp.save_token({"access_token": "at", "refresh_token": "rt", "expires_at": 9_999_999_999})


def make_job(root, clips):
    d = root / "job1"
    d.mkdir(parents=True)
    (d / "x_metadata.json").write_text(json.dumps({"shorts": clips}))
    return d / "x_metadata.json"


def handler(request: httpx.Request):
    url = str(request.url)
    if "/videos" in url:
        return httpx.Response(200, json={"items": [
            {"id": "A", "statistics": {"viewCount": "120", "likeCount": "9"}}]})
    if "youtubeanalytics" in url:
        if "video%3D%3DB" in url or "video==B" in url:
            return httpx.Response(403, json={"error": {"message": "forbidden"}})
        return httpx.Response(200, json={
            "columnHeaders": [{"name": n} for n in yta.ANALYTICS_METRICS.split(",")],
            "rows": [[120, 30, 21, 64.5, 2]]})
    return httpx.Response(404)


def test_refresh_stores_only_returned_metrics(tmp_path):
    meta = make_job(tmp_path, [
        {"start": 0, "end": 20, "youtube": {"video_id": "A", "finished_at": "2026-11-20T00:00:00+00:00"}},
        {"start": 0, "end": 50, "youtube": {"video_id": "B"}},
        {"start": 0, "end": 30},
    ])
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert yta.refresh(str(tmp_path), http) == 2
    shorts = json.loads(meta.read_text())["shorts"]
    a = shorts[0]["youtube"]["metrics"]
    assert a["views"] == 120 and a["likes"] == 9 and a["comments"] is None
    assert a["analytics"]["averageViewPercentage"] == 64.5
    b = shorts[1]["youtube"]["metrics"]
    assert "analytics" not in b and b["views"] is None       # unavailable, not invented
    assert "youtube" not in shorts[2]


def test_observations_report_sample_size_and_no_causation():
    def clip(dur, pct, final=False):
        return {"start": 0, "end": dur, "final": {"file": "f"} if final else {},
                "score_breakdown": {"hook": 9, "payoff": 5},
                "youtube": {"metrics": {"fetched_at": "2026-11-25T00:00:00+00:00",
                                        "analytics": {"averageViewPercentage": pct}}}}
    clips = [clip(20, 70), clip(25, 80), clip(22, 60), clip(50, 40, True),
             {"start": 0, "end": 20, "youtube": {"metrics": {"views": None}}}]
    r = yta.observations(clips)
    assert r["sample_size"] == 4
    band = r["groups"]["duration"]
    assert band["under 30 s"] == {"n": 3, "average": 70.0, "comparable": True}
    assert band["45-60 s"]["comparable"] is False
    assert "not why" in r["note"]
