# Quality checks and export presets

## Export preset `hd` (`render.quality_preset: hd` → `RENDER_PRESET=hd`)

| Property | Value |
|---|---|
| Size | 1080×1920 (9:16), 1080×1080 (1:1), 1920×1080 (16:9), whatever the source size |
| Scaling | `-sws_flags lanczos+accurate_rnd+full_chroma_int` on every encode |
| Video | H.264 High, yuv420p, source frame rate |
| Quality | x264 CRF 14 (fast) for intermediates, CRF 17 (medium) for delivered encodes; NVENC tiers unchanged + lanczos |
| Audio | AAC-LC 48 kHz, 192 kb/s |
| Speech chain | `highpass=f=70`, `afftdn=nr=10:nf=-50`, `acompressor` 2.5:1 @ −20 dB, then `loudnorm I=-14:TP=-2:LRA=11` (`SPEECH_CLEANUP=0` keeps only loudnorm) |
| Recut | the fast path never re-processes finished audio; 12 ms fades at every join |

The `legacy` preset (the default when `RENDER_PRESET` is unset) keeps upstream OpenShorts byte-for-byte. One fix applies
to every preset: the v1 OpenCV fallback now forces yuv420p (it produced High 4:4:4).

## QC report (`render_qc.py`, `<clip>.qc.json`, `clip['qc']`)

| Check | PASS | WARN | FAIL |
|---|---|---|---|
| file | readable by ffprobe | — | missing, empty, unreadable |
| streams | video + audio | — | no video; no audio |
| duration | within 0.6 s of expected | longer than expected | shorter than expected, ≤0.5 s |
| resolution | expected size, even | upscaled from a smaller source | wrong size, odd/zero |
| codec | h264 yuv420p | — | other codec or pixel format |
| frame_rate | 15–61 fps | outside | — |
| audio_format | aac (48 kHz with hd) | other rate/codec | — |
| decode | no decoder errors | — | errors / corrupt frames |
| black_frames | none ≥0.5 s | any | ≥30 % of the clip |
| frozen_frames | none ≥3 s | any | ≥90 % of the clip |
| loudness | −16.5…−11.5 LUFS | outside | below −50 LUFS (silent) |
| true_peak | ≤ −0.5 dBTP | above | — |
| silence | <40 % | ≥40 % | — |

- The overall status is the worst single check.
- QC runs on the served file (the clean file when a hook is still lazy), on packaged `final_` files, and when a clip is packaged through the API.
- A FAIL clip is never auto-uploaded, and the upload endpoint refuses it.
- Rights are a separate gate (`RIGHTS_AND_PUBLISHING.md`), never part of the QC score.
- `RENDER_QC=1/0` forces QC on or off. It is on by default with the hd preset and costs one decode pass per clip.

## Editorial checks (selection, `clip_boundaries.py`)
Stored as `context_flags`, shown on the card, and each lowers `predicted_score`:
- `starts_mid_thought` (−6)
- `starts_mid_sentence` (−6)
- `ends_mid_sentence` (−8)
- `ends_on_question` (−5)

The model's own score stays in `predicted_score_model`.
