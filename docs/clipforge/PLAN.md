# ClipForge Studio — Refined Implementation Plan

> Refined 2026-11-25 after a full read of the checked-out OpenShorts code. It supersedes
> `CLAUDE_CLIPFORGE_IMPLEMENTATION_PLAN.md` (the original brief), whose working rules
> (sections 0, 3 non-goals, 13 security) still apply. Where this document cites a
> file:line, it is the state of `upstream/main` at `29ccbf3`.

## End goal (from the owner)

1. **Find the genuinely best moments**, not random chunks: self-contained clips with a
   clear setup, a payoff and a natural ending, from podcasts and long-form talk.
2. **Enhance every clip** so it is a finished, high-effort piece: clean 1080×1920 video,
   broadcast-grade speech audio, professional captions, hook, branded intro/outro, and
   room for the creator's own commentary/context. "High effort" here means *real*
   editorial value added to the source — the app cannot and will not try to trick
   YouTube's reused-content or Content ID systems.
3. **One config file** holds every credential (LLM keys, ElevenLabs, fal.ai,
   Upload-Post, YouTube OAuth, S3) so the dashboard, CLI and MCP all work without
   pasting keys in the browser.
4. **Auto-upload to YouTube when enabled** in that config, safely (private/unlisted or
   scheduled by default; public only when the owner explicitly configures it and the
   source's rights are recorded).
5. All work on the owner's fork `MitulRanpariya/openshorts`, branch
   `feat/clipforge-studio`. Never push to `mutonby/openshorts`.

## What the audit found (drives the phase order)

| Area | Current state | Gap that blocks the goal |
|---|---|---|
| Clip discovery | 90 s fixed windows → score pass (global top-N) → detail pass → ±1.5 s word snap → 25 % overlap dedupe (`main.get_viral_clips`, `clip_selection.py`) | Detail prompt gets window text **without timestamps** (`main.py:2758`), so start/end are guessed; snap only reaches ±1.5 s. Clips can't leave their window (`gemini_worker.py:303`). One uncalibrated score, `reason` discarded. No whole-source summary, topic map, sentence-boundary or dangling-opener check, no semantic dedupe. |
| Render quality | x264 CRF 18/22, `delivery_size` upscales to ≥1080 wide with bicubic | 4K source ships 1216×2160; no lanczos/sharpen; 3–5 lossy generations; x264 path doesn't force `yuv420p`/profile/bt709; v1 fallback emits **yuv444p**; VFR not normalised. |
| Audio | single-pass `loudnorm I=-14` at the cut only | Output is **96 kHz** (no `-ar`), default AAC bitrate, no highpass/denoise/compression, loudnorm applied twice on recut, no fades at joins. |
| QC | output ≥1 KB, file exists | No ffprobe/blackdetect/freezedetect/ebur128 gate; captions/hook/reframe fail open silently. |
| Config | `os.environ` everywhere + browser localStorage keys | Dashboard demands browser Gemini + Upload-Post keys even if server has them (`App.jsx:1022-1023`); ElevenLabs/fal only via headers; no single config file. |
| Publishing | Upload-Post only; YouTube `privacyStatus` **hard-coded public** (`social_platforms.py`, `app.py:10717`) | No direct YouTube Data API; no private/unlisted/scheduled-private; CLI/MCP publish fail on self-host (no profile). |
| Persistence | jobs in memory + `output/`, purged after 24 h | Review decisions and clips vanish after a day. |
| Speakers/topics | none (visual mouth activity only, for layout) | Q→A structure invisible to the picker. |

Existing strengths to reuse, not replace: layout picker + TRACK/SPLIT/SCREENCAST reframing,
word-level ASR with Parakeet/Whisper fallback, caption presets (ASS karaoke), hook
overlays, clip editor/recut (`recut.py`, `/api/clip/rerender`), pro mode, punch-in, MCP/CLI.

## Phase order (re-prioritised for the goal)

Each phase: implement → tests (`pytest tests/ -q`, `npm run lint && npm run build`) →
update `IMPLEMENTATION_STATUS.md` → one or more logical commits on the feature branch.

### Phase 0 — Fork, branch, baseline ✅ (this session)
- Fork + clone, repo-local git identity for `MitulRanpariya` (global `cxmitul` untouched),
  `upstream` push URL disabled, branch `feat/clipforge-studio`.
- Baseline: 2531 passed / 119 skipped (Postgres/GPU-only), dashboard lint + build green.
- `BASELINE_AUDIT.md`, this plan, `IMPLEMENTATION_STATUS.md`.

### Phase 1 — Single config file + self-host UX
1. `clipforge.config.yaml` (git-ignored) + tracked `clipforge.config.example.yaml`.
   Loader `clipforge_config.py`: reads YAML at startup (path override
   `CLIPFORGE_CONFIG`), maps known keys onto the env var names the code already uses
   (`GEMINI_API_KEY`, `LLM_BASE_URL`, `UPLOAD_POST_API_KEY`, `AWS_*`, …). **A real env
   var always wins** so Docker/CI keep working. Unknown keys → warning, not crash.
   Secrets never logged (redacted `describe()` for `/api/config`).
2. Server-side fallbacks where the code only reads headers today: ElevenLabs, fal.ai,
   Upload-Post profile (`upload_post.profile`) — header still wins.
3. `/api/config` reports which providers are configured server-side (booleans only);
   dashboard stops demanding browser keys that the server already has and no longer
   requires an Upload-Post key just to clip.
4. Self-host defaults from config: `JOB_RETENTION_SECONDS` longer (e.g. 30 days) so
   reviewed clips survive.
5. Tests: precedence, type coercion, redaction, missing file, malformed YAML.

### Phase 2 — HD render + audio + QC gate (the "perfect HD file")
1. **Delivery preset** `quality_preset` (config + per job): default `shorts_1080`:
   exactly 1080×1920 (lanczos scale; downscale >1080 sources, upscale smaller with a
   logged/flagged "upscaled" warning, mild `unsharp` only when upscaling), `yuv420p`,
   H.264 High@4.2, bt709 tags, CFR at source fps (VFR → CFR, capped at 60), `+faststart`,
   GOP ≈ 2 s. AAC-LC 48 kHz stereo 192–256 kbps. Keep the old behaviour reachable
   (`quality_preset: legacy`) for regression tests.
2. **Fewer generations**: intermediates (cut, reframe segments) at visually lossless
   CRF (≈12, fast preset) and one final delivery encode at CRF ≈17 slow/medium. Respect
   the GPU single-pass path; CPU path stays default.
3. **Speech audio chain** (configurable, on by default for talk content): `highpass=80`,
   light `afftdn`, gentle `acompressor`, `loudnorm` two-pass-measured to −14 LUFS /
   −1.5 dBTP, `aresample=48000`. Applied once (fix the double loudnorm on recut), with
   10 ms fades at recut joins. Revisit the repo's earlier alimiter/two-pass rejection with
   the 48 kHz fix and document the result.
4. Fix v1 fallback `-pix_fmt yuv420p`.
5. **QC gate** `render_qc.py`: ffprobe (streams, WxH, pix_fmt, profile, fps, duration vs
   expected, audio rate), `blackdetect`, `freezedetect`, `silencedetect`, `ebur128`
   (integrated LUFS, true peak), decode test. PASS/WARN/FAIL per check, report saved as
   `<clip>.qc.json` beside the clip and surfaced in `/api/status` + clip card. FAIL =
   not deliverable / not auto-uploaded. Captions/hook failing open becomes a WARN.
6. Tests: synthetic lavfi fixtures (valid, silent, black, frozen, wrong size, 96 kHz).

### Phase 3 — Smarter clip discovery
Keep the invariants in root `CLAUDE.md` (score every window, global top-N, `min_clips`
floor in code) and the existing schemas (old metadata keeps loading).
1. **Source map pass** (one cheap LLM call over a compressed transcript): summary,
   participants, topic segments with timestamps, notable Q→A spans. Stored as
   `source_map` in metadata; passed as context to score/detail prompts. Optional — any
   failure falls back to today's flow.
2. **Timestamped detail input**: send words/sentences with times (`compact_words`, today
   unused) so the model picks real boundaries; allow the detail pass to extend up to N s
   beyond its window for setup/payoff.
3. **Deterministic boundary refinement** (`clip_boundaries.py`): snap start to a
   sentence/segment start and end to a sentence end (punctuation + pause length) within
   ±6 s; flag dangling openers ("and", "so", "but", "he/she/that", answers without
   their question) and pull the start back to include the setup when cheap.
4. **Score dimensions** in the detail schema: hook, self-contained, payoff, information,
   emotion/humour, specificity, ending — each 0–10 with one-line evidence; composite is a
   weighted sum (weights in config). Keep `predicted_score` (0–100) for UI/back-compat.
   Validate ranges, `start<end`, inside source, known `source_window_id`.
5. **Semantic dedupe**: same topic segment + high token overlap → keep best; enforce
   topic spread in the final list.
6. Deterministic model settings (temperature low, thinking config for the score pass
   where the provider supports it).
7. Benchmark harness `tools/clip_benchmark.py`: run selection on stored transcripts
   with human labels → precision@K, context-failure rate, mid-sentence rate. No
   copyrighted media committed.

### Phase 4 — Enhancement studio (real added value)
1. Review state per clip (`keep` / `reject` / `pin`, notes) persisted in metadata;
   dashboard buttons + filter. Persisted across restart.
2. **Brand kit** in config: logo/watermark corner, intro card (title + 1–2 s), outro/CTA
   card, font + colours; applied in the final encode pass (safe filtergraph from
   numeric/known values only).
3. **Context card / chapter title** auto-drafted from the source map (editable), shown in
   the first seconds when the clip needs setup.
4. **Creator voiceover/commentary**: upload an audio (or audio+camera) file and place
   it as intro, outro or over a segment with ducking of source audio; optional
   ElevenLabs TTS of a creator-written script (labelled as TTS).
5. **Visual emphasis**: reuse `punch_in.py` for speaker punch-ins on emphasis words;
   keyword highlight in captions; optional user-supplied B-roll/image inserts with
   asset records. No random stock, no auto "fake reactions".
6. Source attribution line in description and optional on-screen credit.

### Phase 5 — Metadata + direct YouTube upload (auto when enabled)
1. Metadata generation from transcript + source map: 3–5 titles, description with
   attribution and chapters-free short copy, restrained hashtags; editable.
2. `youtube_publisher.py` using YouTube Data API v3 (resumable upload, `videos.insert`,
   scope `youtube.upload` only; `youtube.readonly` later for analytics). OAuth
   installed-app flow via a CLI command (`python -m clipforge youtube-auth`) that stores the
   refresh token under the git-ignored config dir. Tokens never sent to the browser.
3. Settings in config: `youtube.auto_upload` (default false), `privacy`
   (`private`|`unlisted`|`public`, default `private`), `schedule` (publishAt slots,
   e.g. daily 18:00 IST), `max_per_day` (API quota: ~1,600 units/upload of 10,000/day →
   ≤ 6/day), `min_score`, `require_review` (only `keep`-ed clips).
4. Gates before any upload: QC != FAIL; for `public` (or scheduled public): source
   rights recorded (`own` / `licensed` / `cc` with attribution) — a compact rights
   record on the job (category, evidence note, attribution) rather than a heavy
   workflow. Over-60 s third-party Shorts show the Content-ID warning.
5. Idempotency: publish record (`<clip>.publish.json`: key = job+clip+recipe hash, video
   id, state) so retries never duplicate. Upload-Post stays available as an alternative;
   remove its hard-coded `public` in favour of the configured privacy.
6. Tests with a mocked API client (quota error, token expiry/refresh, retry idempotency).

### Phase 6 — Analytics + feedback loop
- YouTube Analytics API (views, engaged views, avg view duration/percentage, likes,
  subs gained) per uploaded clip, with date range and fetch time; unavailable metrics
  shown as unavailable.
- Join with score dimensions / topic / duration / hook style; show observations with
  sample size; weights changes are manual and versioned.

### Phase 7 — Hardening, docs, release
- Security pass over new code (path guards, filtergraph allowlist, SSRF rules, secret
  redaction), CPU-only concurrency defaults for this machine (12 threads, 16 GB RAM, no
  GPU → `MAX_CONCURRENT_JOBS=1`, `CLIP_WORKERS=2`).
- Docs: `ARCHITECTURE.md`, `QUALITY_CHECKS.md`, `RIGHTS_AND_PUBLISHING.md`, README section.
- Manual E2E on a source the owner has rights to; open a PR on the fork (never upstream).

## Owner inputs needed (asked when the phase needs them)
- Phase 1/3: Gemini API key (or a local LLM URL) in `clipforge.config.yaml`.
- Phase 2+: running the full ML stack locally (venv with `requirements-cpu.lock`, ~5 GB)
  or via Docker — Docker is already installed.
- Phase 5: a Google Cloud project with YouTube Data API v3 enabled and an OAuth client
  (Desktop app) JSON; a test channel or private uploads for the first run.
- A short podcast clip you own/are licensed to use for the manual E2E test.
