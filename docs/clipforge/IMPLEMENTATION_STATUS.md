# Implementation status

Branch `feat/clipforge-studio` on `MitulRanpariya/openshorts` (fork of `mutonby/openshorts`,
nothing pushed upstream). Plan: `PLAN.md`. How to use: `USER_GUIDE.md`.

| Phase | State | Delivered |
|---|---|---|
| 0 Fork, branch, baseline | ✅ | `BASELINE_AUDIT.md`; repo-local git identity |
| 1 Config file + self-host UX | ✅ | `clipforge_config.py`, example YAML, server-held keys honoured by API and dashboard |
| 2 HD render, audio, QC | ✅ | `RENDER_PRESET=hd`, speech chain, `render_qc.py`, QC badge |
| 3 Clip discovery | ✅ | `source_map.py`, `clip_boundaries.py`, timed sentences, score dimensions, content dedupe |
| 4 Enhancement studio | ✅ | `short_packager.py`, asset upload, finish modal, keep/reject/pin, branding config, auto-package |
| 5 Metadata + YouTube | ✅ code / ⏳ owner setup | `youtube_publisher.py`, `publish_metadata.py`, rights record, auto-upload; needs the owner's OAuth client |
| 6 Analytics | ✅ code / ⏳ data | `youtube_analytics.py`, insights card; needs uploaded clips |
| 7 Hardening, docs | ✅ | launcher `clipforge.sh`, docs, security review of new code |

## Verification (2026-11-25)
- **Automated**
  - pytest: **2815+ passed, 43 skipped** (GPU and Postgres tests skipped: no GPU or Postgres here).
  - New tests (155+):
    - `test_clipforge_config`, `test_render_preset`, `test_render_qc`
    - `test_clip_boundaries`, `test_source_map`, `test_short_packager`
    - `test_clipforge_endpoints`, `test_youtube_publisher`, `test_youtube_analytics`
    - additions in `test_clip_selection`
  - Dashboard: `npm run lint` and `npm run build` pass.
  - `tests/test_queue_saturation.py::TestVolume` measures event-loop latency in wall-clock time. It fails under heavy host load on untouched upstream code too, and passes when the machine is idle.
- **Keyless E2E**: synthetic 76 s flite-speech source with `tools/stub_llm_server.py` gave 1 clip at 1080×1920 H.264 High yuv420p, AAC 48 kHz, −14.0 LUFS, QC PASS.
- **Real E2E with Gemini** (`gemini-3.1-flash-lite`): a 27-minute CC BY podcast by URL through the API.
  - Download, transcription and render completed in 1,364 s on CPU.
  - 5 clips, all QC PASS (1080×1920, −14.2 LUFS, −2.0 dBTP). Model cost about $0.01.
- **Selection re-run with the source map** on the same transcript:
  - 8 topics and an accurate summary.
  - 6 clips across 6 different topics (before: 3 of 5 clips from minutes 3–7).
  - 2 clips honestly flagged. Cost about $0.015.
- **Live API through the dashboard proxy**: review, rights and packaging on a real clip gave `final_` with QC PASS in about 23 s. Jobs survive a backend restart.

## Not verified / needs the owner
- **YouTube OAuth and a real upload.** Needs the Google Cloud OAuth client (USER_GUIDE §7.1). Unaudited API projects upload as private only.
- **Analytics on real uploads.** Needs uploaded clips plus 1–2 days.
- **GPU path.** No GPU here; the hd preset adds `-sws_flags` to NVENC args but GPU equivalence tests were not run.
- **Benchmark of selection quality.** Needs human labels on several episodes (precision@K, context-failure rate). Not built; suggested next step.
- **Browser click-through of the new dashboard modals.** They are built and lint-clean, and their endpoints are tested, but no interactive browser session was run here.

## Next steps (suggested)
1. Connect YouTube, upload a few private clips, check the descriptions and credit.
2. Request the YouTube API audit if public auto-upload is wanted.
3. Label 3–5 episodes (keep/reject in the dashboard is stored per clip) and tune
   `clip_selection.SCORE_DIMENSIONS` from what you keep.
4. Optional: put the launcher behind systemd for always-on auto-upload.
