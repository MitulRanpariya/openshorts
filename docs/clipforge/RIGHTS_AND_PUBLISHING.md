# Rights and publishing

## Rights record (per job)
`POST /api/job/rights` (dashboard: YouTube dialog → source rights) stores the creator's own
declaration. ClipForge does not verify it and does not give legal advice.

| category | public / scheduled upload |
|---|---|
| `own` | yes |
| `licensed` | yes; keep the permission in `evidence` |
| `cc` | yes if `license` and `attribution` are set and, for `intended_use: monetized`, the licence is not NC or ND |
| `public_domain` | yes |
| `unclear` / none | no: private or unlisted only |

- Every change is appended to `rights_log` (time, category, licence).
- `rights.default_category`, `default_license` and `default_attribution` in the config apply to jobs that have no record, marked `source: config default`.
- The attribution text is appended verbatim to every generated description.

## Publish gate (`/api/youtube/upload`, auto-upload)
1. YouTube must be connected (OAuth token present).
2. The file's QC must not be `FAIL`.
3. `public`, or any `publish_at`, requires `rights_allow_public` to be true.
4. `publish_at` must be at least 15 minutes in the future and carry a timezone.
5. The local daily limit (`youtube.max_per_day`) must not be reached.
6. The upload is idempotent: `<file>.publish.json` keyed by file name and size. A retry returns the existing video id (reported as "already uploaded"; nothing is sent again).
7. A broken upload resumes its session only for the same title, description, privacy and schedule. A changed request, or an expired session, starts a new upload, so a retry never publishes with settings you no longer asked for.
8. A packaged final made from an older version of the clip (re-subtitled, recut or re-hooked since) is not uploaded; the current clip is.
9. The attribution line is added to the description at upload time whenever it is missing.

A warning is returned for third-party clips over 60 s: a Content ID claim blocks a long Short worldwide.

## What ClipForge cannot promise
- Permission to use a source, Content ID status, and YouTube monetisation eligibility are three different questions.
- A licence does not prevent a Content ID claim.
- Reused-content rules judge the whole channel. Your own commentary, context and voiceover (finish short) are what add value.
- New, unaudited Google API projects upload only as private (see the user guide §7.1).
- No output is labelled copyright-free, claim-proof or monetisation-guaranteed.

## Auto-upload selection (`youtube_publisher.pick_clips`)
- Clips with `review.decision == reject` or QC `FAIL` are skipped.
- With `require_keep`, only `keep`/`pin` clips qualify, uploaded when you click keep or pin.
- Otherwise clips need `predicted_score ≥ min_score` (keep and pin are exempt).
- Pinned clips go first, then by score, up to `max_per_job`.
- Schedule slots: `schedule_times` in `timezone`, the next free slots, reserved in `.clipforge/youtube_schedule.json`.
