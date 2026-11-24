# Implementation status

| Phase | State | Notes |
|---|---|---|
| 0 Fork, branch, baseline | ✅ done | see `BASELINE_AUDIT.md` |
| 1 Config file + self-host UX | ✅ done | `clipforge_config.py`, example YAML, server-side key fallbacks, dashboard honours server keys |
| 2 HD render, audio, QC gate | ⏳ next | |
| 3 Clip discovery | — | |
| 4 Enhancement studio | — | |
| 5 Metadata + YouTube upload | — | needs OAuth client from owner |
| 6 Analytics | — | |
| 7 Hardening, docs, release | — | |

## Test log
- 2026-11-25 baseline: pytest 2531 passed / 119 skipped; dashboard lint + build pass.
- 2026-11-25 phase 1: pytest 2547 passed / 119 skipped; 18 new config tests. `TestVolume`
  timing tests in `test_queue_saturation.py` fail under host load (load avg ~9) on the
  untouched upstream code too — wall-clock flake, not a regression. Dashboard lint + build pass.

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
