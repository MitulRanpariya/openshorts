"""A deterministic OpenAI-compatible stand-in for the moment picker.

For local end-to-end runs of the real pipeline without any API key:

    python tools/stub_llm_server.py --port 18766
    LLM_BASE_URL=http://127.0.0.1:18766/v1 LLM_MODEL=stub python main.py -i src.mp4 -o out/

It reads the transcript windows out of the prompt and answers the score pass
(every window, descending scores) and the detail pass (one clip per window,
inside its bounds). It judges nothing: it exists to exercise ingestion,
transcription, cutting, reframing, captions, rendering and QC.
Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WINDOW = re.compile(r'"id":\s*"([^"]+)",\s*"start":\s*([\d.]+),\s*"end":\s*([\d.]+)')
DURATION = re.compile(r"Each clip must be ([\d.]+) to ([\d.]+) seconds")


def answer(prompt: str, schema_name: str) -> dict:
    if "sourcemap" in schema_name:
        marks = [float(x) for x in re.findall(r"^\[(\d+)\]", prompt, re.M)] or [0.0]
        end = max(marks) + 5
        half = round(end / 2, 1)
        return {"summary": "Stub summary of the episode.", "participants": ["host"],
                "topics": [{"start": 0, "end": half, "title": "First topic", "summary": "-"},
                           {"start": half, "end": end, "title": "Second topic", "summary": "-"}],
                "qa": []}
    windows = [(i, float(s), float(e)) for i, s, e in WINDOW.findall(prompt)]
    if "score" in schema_name:
        return {"windows": [
            {"id": wid, "start": s, "end": e, "score": max(5, 90 - 7 * n),
             "reason": "stub"} for n, (wid, s, e) in enumerate(windows)]}
    lo, hi = 15, 60
    match = DURATION.search(prompt)
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
    shorts = []
    for n, (wid, s, e) in enumerate(windows):
        length = min(max(lo + 5, 20), hi, e - s)
        if length < lo:
            continue
        start = s + min(1.0, (e - s - length) / 2)
        shorts.append({
            "start": round(start, 2), "end": round(start + length, 2),
            "source_window_id": wid, "predicted_score": max(10, 85 - 5 * n),
            "video_description_for_tiktok": "Stub clip #test",
            "video_description_for_instagram": "Stub clip #test",
            "video_title_for_youtube_short": f"Stub moment {n + 1}",
            "viral_hook_text": "Wait for this part", "why": "stub selection",
            "hook": 7, "stands_alone": 8, "payoff": 6, "value": 6, "emotion": 4,
            "ending": 8})
    return {"shorts": shorts}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — http.server API
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        prompt = (body.get("messages") or [{}])[-1].get("content", "")
        fmt = body.get("response_format") or {}
        name = ((fmt.get("json_schema") or {}).get("name") or "").lower()
        if not name:
            name = "scoreresponse" if "score" in prompt.lower()[:400] else "detailresponse"
        content = json.dumps(answer(prompt, name))
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}],
                          "usage": {"prompt_tokens": len(prompt) // 4,
                                    "completion_tokens": len(content) // 4}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
