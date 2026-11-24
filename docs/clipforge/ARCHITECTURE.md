# ClipForge architecture (as implemented)

ClipForge extends the OpenShorts pipeline. There is no second queue or renderer. Every stage
below runs inside the existing job lifecycle (`app.py` queue → `main.py` child process →
delivery steps).

```
/api/process ─► main.py (child, filtered env: no credentials)
   download / upload ─► transcription (word timestamps)
   get_viral_clips
     ├─ clip_boundaries.sentences(words)
     ├─ source_map.build ........ 1 LLM call: summary, participants, topics, Q&A   [SOURCE_MAP]
     ├─ score pass (global top-N, upstream invariant) ── context prepended
     ├─ detail pass ............. timed sentences + 6 score dimensions per clip
     ├─ apply_score_breakdown ... blended predicted_score (model score kept)
     ├─ valid_proposal ........... drop impossible ranges / invented window ids      [CLIP_REFINE]
     ├─ refine_all ............... sentence edges, Q→A, dangling openers, context_flags
     ├─ snap_clip_to_words ...... (upstream) pauses between words
     ├─ dedupe_overlapping ...... (upstream) temporal
     └─ dedupe_similar .......... same story told twice
   per clip: cut ─► reframe ─► hook + captions (upstream)
     ├─ render encode args ...... RENDER_PRESET=hd (ffmpeg_utils)
     ├─ clip_quality_report ..... render_qc → <clip>.qc.json + clip['qc']               [RENDER_QC]
     └─ auto_package_clip ....... short_packager → final_*.mp4 + clip['final']          [BRAND_AUTO_PACKAGE]
app.py delivery steps: settle, archive, unmark, autopilot, **youtube** (background auto-upload), webhook, …
```

## Modules

| Module | Responsibility |
|---|---|
| `clipforge_config.py` | `clipforge.config.yaml` → env vars (env and `.env` win). Loaded by the API process only. |
| `clip_boundaries.py` | Sentence segmentation, boundary refinement, context flags, content dedupe, proposal validation (pure). |
| `source_map.py` | Whole-episode map, prompt context, topic per clip (optional, validated, clamped). |
| `clip_selection.py` (+) | `SCORE_DIMENSIONS`, `apply_score_breakdown`. |
| `ffmpeg_utils.py` (+) | `RENDER_PRESET`, hd video args, speech chain, `hd_audio_args`, join fades. |
| `reframe_v2.py` (+) | `hd_delivery_size`: fixed output sizes. |
| `render_qc.py` | ffprobe + one decode pass → PASS/WARN/FAIL report. |
| `short_packager.py` | Intro/outro cards, logo, credit, voiceover (intro/over/outro, ducking). |
| `publish_metadata.py` | Grounded titles/description/hashtags + verbatim credit. |
| `youtube_publisher.py` | OAuth (PKCE, loopback), resumable upload, publish records, rights gate, quota, schedule slots, CLI. |
| `youtube_analytics.py` | Official metrics refresh, per-feature observations with sample sizes. |
| `tools/stub_llm_server.py` | Deterministic OpenAI-compatible stand-in for keyless E2E runs. |
| `clipforge.sh` | setup / start / stop / status / test / youtube-auth, with free-port selection. |

## Data contracts (in `<job>/<title>_metadata.json`)

All data is additive, so old jobs and old clients keep working.

| Key | Shape |
|---|---|
| `source_map` | `{summary, participants[], topics[{start,end,title,summary}], qa[{question_start,answer_end,question}], version}` |
| `shorts[i].proposed` | the model's raw `[start, end]` (upstream) |
| `shorts[i].score_breakdown` | `{hook, stands_alone, payoff, value, emotion, ending}` 0–10 |
| `shorts[i].editorial_score`, `predicted_score_model` | weighted dimensions ×10; the model's own estimate |
| `shorts[i].context_flags` | subset of `starts_mid_thought, starts_mid_sentence, ends_mid_sentence, ends_on_question` |
| `shorts[i].topic` | title of the source-map topic holding the clip |
| `shorts[i].qc` | `{status, checks[{name,status,detail}]}` |
| `shorts[i].review` | `{decision: keep|reject|pin, note, at}` |
| `shorts[i].final` | `{file, url, recipe{durations, elements, excerpt_range, options}, qc, at}` |
| `shorts[i].youtube` | `{video_id, url, privacy, publish_at, title, state, …, metrics{…}}` |
| `rights`, `rights_log` | `{category, license, attribution, evidence, notes, intended_use, at}`, audit list |

Files beside each clip:
- `<file>.qc.json`: the QC report;
- `<file>.publish.json`: the publish record (holds the resumable session while uploading);
- `asset_voiceover_*` and `asset_logo_*`: creator uploads.

Local state lives in `.clipforge/` (mode 700):
- `youtube_token.json`;
- `youtube_quota.json`;
- `youtube_schedule.json`;
- `run/` (launcher PIDs and logs).

## Endpoints added

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/clip/review` | keep / reject / pin (+ note) |
| POST | `/api/clip/asset` | upload voiceover (≤60 MB, ≤5 min) or logo (≤8 MB), validated |
| POST | `/api/clip/package` | render `final_*` with QC |
| GET/POST | `/api/job/{id}/rights`, `/api/job/rights` | read / record source rights |
| GET | `/api/youtube/status` | connection, channel, quota (no tokens) |
| POST | `/api/youtube/metadata` | draft titles/description/hashtags |
| POST | `/api/youtube/upload` | gated upload (QC, rights, schedule) |
| POST | `/api/youtube/analytics/refresh` | fetch official metrics |
| GET | `/api/youtube/insights` | grouped observations with sample sizes |
| GET | `/api/config` (+`serverKeys`) | which credentials the server holds (booleans) |

## Safety properties kept
- Pipeline children never read the config file: the upstream secret denylist holds, and a test enforces it.
- No transcript, model or user text enters an ffmpeg filter graph. Cards and credits are PIL-rendered PNGs, and asset names are regex-checked basenames in the job directory.
- Transcript text is marked as data in every prompt, and model output is schema-validated, clamped and checked.
- YouTube tokens never leave the server. Uploads are private unless rights allow more, and they are idempotent.
- Upstream `CLAUDE.md` invariants are unchanged: score every window with a global top-N, the `min_clips` floor is enforced in code, the GPU graph reader is untouched (`-sws_flags`, not graph text), and the clean canonical files stay clean.
