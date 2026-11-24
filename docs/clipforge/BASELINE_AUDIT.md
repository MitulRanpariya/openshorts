# Baseline audit (2026-11-25)

## Repository
- Fork: `origin` = `MitulRanpariya/openshorts`; `upstream` = `mutonby/openshorts`
  (fetch only — push URL disabled locally).
- Branch: `feat/clipforge-studio` from `main` at `29ccbf3`. No uncommitted changes at start.
- Git identity is repo-local (`MitulRanpariya`, GitHub noreply email); global config untouched.
- License: MIT (`LICENSE`), `cloud/` has its own exception. Neither will be modified.

## Machine
- Intel i7-10850H, 12 threads, 15 GiB RAM, no NVIDIA GPU (CPU-only path).
- ffmpeg/ffprobe 8.0.1, Docker 29.1, Node 22.22 / npm 9.2, system Python 3.14,
  `uv` 0.13 → project venv `.venv` on Python 3.11 (matches CI and the Dockerfile).

## Baseline checks
| Check | Result |
|---|---|
| `pytest tests/ -q` (CI dependency set, no ML stack) | **2531 passed, 119 skipped** (Postgres/GPU-gated) in 93 s |
| `npm ci && npm run lint` (dashboard) | pass |
| `npm run build` | pass (chunk-size warning only) |
| Full app start | not yet — needs the ML stack (`requirements-cpu.lock`) or Docker; recorded as Phase 1/2 setup step |

## Architecture as implemented
- `app.py` (FastAPI, ~11.6k lines): `/api/process` creates `output/<job>/`, writes
  `.owner` + `.resume.json`, enqueues; `process_queue` runs `main.py` as a child process
  per job (`MAX_CONCURRENT_JOBS`), parses `CLIP_READY` lines, `/api/status/{id}` polls.
  On restart: completed jobs recovered from `*_metadata.json`, interrupted ones resumed
  from `.resume.json`. Self-host retention 24 h.
- `main.py`: download/upload → early ASR → layout picker → transcript → `get_viral_clips`
  (or `get_visual_clips`) → `<title>_metadata.json` → per clip: cut → `render_clip`
  (reframe v2 / GPU / v1 fallback) → hook grounding → pro mode → hook + captions.
- LLM: Gemini (google-genai, pydantic schemas) or any OpenAI-compatible server
  (`llm_backend.py`, `LLM_BASE_URL`) for the transcript passes; vision stages Gemini-only.
- Config: plain env vars (`load_dotenv`) + browser localStorage keys sent as headers.
  With `BILLING_ENABLED` unset: no login, no DB.
- Publishing: Upload-Post only; YouTube privacy hard-coded public. No YouTube Data API.

## Extension points chosen
| Need | Where |
|---|---|
| Config file | new `clipforge_config.py`, called before the existing `load_dotenv()` in `app.py` / `main.py` |
| Delivery quality | `ffmpeg_utils.py` (encoder tiers, `audio_encode_args`), `reframe_v2.delivery_size`, final pass in `main._process_one_clip` |
| QC | new `render_qc.py`, invoked after the last encode per clip; report beside the clip |
| Discovery | `main.get_viral_clips`, `gemini_worker` schemas/prompts, `clip_selection.py` helpers |
| Review/enhancement | `shorts[i]` in metadata, existing `/api/clip/*` endpoints, `ResultCard.jsx`, `ClipEditor.jsx` |
| YouTube | new `youtube_publisher.py`; existing `/api/social/post` stays for Upload-Post |

Full gap table and phase order: `PLAN.md`.

## Risks and boundaries
- `app.py`/`main.py` are very large; changes stay small and covered by tests.
- Upstream `CLAUDE.md` hard rules (filter allowlist, job-dir data rules, clip-selection
  invariants, GPU path) are followed; upstream `CLAUDE.md` is not edited.
- Cannot be automated: OAuth consent for YouTube, source rights decisions, judging
  "best clip" quality without human labels, GPU equivalence tests (no GPU here).
- No guarantee of reach, monetisation or absence of Content ID claims.
