# Implementation status

| Phase | State | Notes |
|---|---|---|
| 0 Fork, branch, baseline | ✅ done | see `BASELINE_AUDIT.md` |
| 1 Config file + self-host UX | ✅ done | `clipforge_config.py`, example YAML, server-side key fallbacks, dashboard honours server keys |
| 2 HD render, audio, QC gate | ✅ done | `RENDER_PRESET=hd`, speech chain, `render_qc.py`, QC badge; E2E verified |
| 3 Clip discovery | 🔄 in progress | done: sentence boundaries, question/answer pull-in, context flags + penalty, content dedupe, proposal validation, timed sentences in detail prompt. next: source map, score dimensions, benchmark |
| 4 Enhancement studio | — | |
| 5 Metadata + YouTube upload | — | needs OAuth client from owner |
| 6 Analytics | — | |
| 7 Hardening, docs, release | — | |

## Test log
- 2026-11-25 baseline: pytest 2531 passed / 119 skipped; dashboard lint + build pass.
- 2026-11-25 phase 1: pytest 2547 passed / 119 skipped; 18 new config tests. `TestVolume`
  timing tests in `test_queue_saturation.py` fail under host load (load avg ~9) on the
  untouched upstream code too — wall-clock flake, not a regression. Dashboard lint + build pass.

- 2026-11-25 phase 2/3a: full ML stack (CPU torch 2.11) installed; pytest **2762 passed /
  43 skipped**. E2E on a synthetic 76 s flite-speech 1080p source with `tools/stub_llm_server.py`
  (no keys): 1 clip, 1080x1920 H.264 High yuv420p 30 fps, AAC 48 kHz, -14.0 LUFS / -2.0 dBTP,
  QC PASS on all 12 checks; refine moved 1.0-21.0 s to the sentence-complete 0-25.72 s.
  Wall 331 s on CPU (transcription 62 s, reframe 58 s).

## Local end-to-end run (no API keys)
```bash
python tools/stub_llm_server.py --port 18766 &
CLIPFORGE_CONFIG=/nonexistent LLM_BASE_URL=http://127.0.0.1:18766/v1 LLM_MODEL=stub \
RENDER_PRESET=hd CLIP_WORKERS=2 AUTO_HOOK=1 AUTO_LAYOUT=0 \
python -u main.py -i <source.mp4> -o <out_dir>
```
ML stack install (CPU, mirrors the Dockerfile):
`uv pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 torchvision==0.26.0`
then `uv pip install --no-deps -r requirements-cpu.lock`.

## Phase 2 notes
- `RENDER_PRESET` legacy (default when unset) | hd (set in the example config). hd: fixed
  1080x1920/1080x1080/1920x1080, `-sws_flags lanczos+accurate_rnd+full_chroma_int`, x264 CRF
  14 intermediates / 17 delivered, yuv420p High, AAC 48 kHz 192k, speech chain
  (`SPEECH_CLEANUP`), recut fast path never reprocesses audio, 12 ms join fades.
- QC (`RENDER_QC`, default on with hd): `<clip>.qc.json` + `clip['qc']`; FAIL = do not deliver/upload.
- Fixed for all presets: v1 OpenCV fallback now yuv420p (was High 4:4:4).

## Phase 3 notes (so far)
- `clip_boundaries.py` (`CLIP_REFINE=0` disables): sentences from punctuation/pauses; edges move
  to sentence starts/ends within 8 s; a preceding question within 12 s is included; dangling
  English openers pull the start back one sentence; remaining flags (`starts_mid_thought`,
  `starts_mid_sentence`, `ends_mid_sentence`, `ends_on_question`) are stored as
  `context_flags` and lower `predicted_score` (original in `predicted_score_model`);
  content-word dedupe drops a repeated story; malformed proposals dropped.
- Detail prompt windows carry `sentences: [[start, text]]` instead of untimed prose.

## Phase 1 notes
- Config file: `clipforge.config.yaml` (git-ignored), template `clipforge.config.example.yaml`.
  Precedence: real env > `.env` > config file. Loaded only by the API process (`app.py`);
  job children inherit the filtered env (`child_env`) and never read the file, so the
  upstream secret denylist still holds (`test_pipeline_code_reads_none_of_the_dropped_secrets`).
- `/api/config.serverKeys` (self-host only): booleans for gemini/localLlm/uploadPost/
  uploadPostProfile/elevenlabs/fal. Values never leave the server.
- Server fallbacks (self-host only, header still wins): ElevenLabs, fal.ai, Upload-Post profile.
- Dashboard: no longer demands an Upload-Post key to clip; accepts a server-held Gemini key.
- Self-host app URL is `#app` (landing page left as upstream).
