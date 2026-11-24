# ClipForge Studio — User Guide

ClipForge is your self-hosted podcast clipping studio, built on OpenShorts. It:

1. reads the **whole** episode: transcript, a summary, topic segments and question/answer spans;
2. picks the strongest **self-contained** moments, cut on whole sentences, each with a score you can understand;
3. renders finished **1080×1920** Shorts: face-aware framing, captions, a hook, cleaned-up speech audio at −14 LUFS;
4. checks every file automatically (PASS / WARN / FAIL);
5. lets you add **your own value**: an intro or context card, your voiceover, your logo, a source credit and an outro;
6. uploads to **your YouTube channel**, by hand or automatically, private by default;
7. shows how your uploaded clips perform, using only real metrics from YouTube.

> Honest limits: no tool can guarantee views, monetisation or freedom from Content ID claims. The
> parts that genuinely help a channel are picking strong moments and adding your own commentary
> or context. ClipForge makes both fast; the editorial judgement stays yours.

---

## 1. First-time setup (once)

Requirements:
- Linux or macOS
- Python via [`uv`](https://docs.astral.sh/uv/)
- `ffmpeg` (with libass)
- Node.js 20+
- about 6 GB of disk space

```bash
cd /home/mitul/clipper/openshorts
./clipforge.sh setup
```

`setup` does the following:
- creates `.venv` with Python 3.11;
- installs the CPU builds of the ML stack (Whisper, YOLO, MediaPipe, TransNetV2);
- installs `deno`, which yt-dlp needs for YouTube;
- installs the dashboard packages;
- creates `clipforge.config.yaml` from the example.

## 2. The one config file: `clipforge.config.yaml`

Every key and setting lives in this file.
- It is git-ignored and only you can read it (permissions 600). Never commit it.
- `clipforge.config.example.yaml` documents every key.
- A real environment variable or `.env` with the same meaning overrides the file.

The minimum you need:

```yaml
llm:
  gemini:
    api_key: "your-gemini-key"     # https://aistudio.google.com/app/apikey
```

The most useful settings:

| Section | Key | What it does |
|---|---|---|
| `llm.openai_compatible` | `base_url`, `model`, `api_key` | Use OpenAI, OpenRouter or a local Ollama for clip picking instead of Gemini (layout and vision still use Gemini) |
| `render` | `quality_preset: hd` | Fixed 1080×1920, high-quality encode, 48 kHz audio. `legacy` gives the original OpenShorts output |
| `render` | `speech_cleanup`, `loudness_normalize` | Rumble filter, light denoise, gentle compression, −14 LUFS |
| `render` | `quality_checks` | Automatic QC report for every clip |
| `clips` | `min_seconds`, `max_seconds` | Clip length band (default 15–60 s; Shorts allow up to 180) |
| `clips` | `source_map`, `sentence_boundaries` | Whole-episode context and sentence-exact cuts (both on) |
| `branding` | `logo_path`, `outro_text`, `credit_text`, `accent_color` | Defaults for the "finish short" packaging |
| `branding` | `auto_package`, `auto_intro_card` | Package every clip automatically |
| `processing` | `max_concurrent_jobs: 1`, `clip_workers: 2` | Right for a CPU-only laptop |
| `processing` | `job_retention_seconds` | How long jobs stay on disk (30 days is set) |
| `youtube` | see §7 | Direct upload and auto-upload |
| `rights` | `default_category` | Your standing declaration for sources (see §6) |
| `elevenlabs`, `fal`, `upload_post` | `api_key` | Optional: dubbing, AI Shorts, posting through Upload-Post |

Restart after editing: `./clipforge.sh restart`.

## 3. Start, stop, status

```bash
./clipforge.sh start      # prints the URL; open  http://127.0.0.1:<port>/#app
./clipforge.sh status     # ports, PIDs, YouTube connection
./clipforge.sh logs       # follow the backend log
./clipforge.sh stop       # stops only what the script started
```

The default ports are 8000 (backend) and 5175 (dashboard). If a port is busy (another project), the
next free one is used; nothing else is touched. To choose ports yourself:
`BACKEND_PORT=18765 FRONTEND_PORT=15195 ./clipforge.sh start`.

## 4. Making clips

1. Open the dashboard at the `#app` URL and go to **Clip Generator**.
2. Paste a YouTube link (or upload a file), choose options if you like (clip count, length, hook style, layout) and start.
3. Processing time on this laptop (CPU): about 20–25 minutes for a 30-minute podcast (download, transcription, picking, rendering). Progress shows live.

How the clips are chosen:
1. One extra AI call builds a **source map** of the whole episode: summary, participants, topics and Q&A.
2. Every 90-second window is **scored**, and the best ones are shortlisted across the whole episode.
3. The **detail pass** sees timed sentences and picks exact clips. It rates each clip 0–10 on six things: hook, stands alone, payoff, value, emotion and ending.
4. **Deterministic clean-up** (no AI involved):
   - edges move to whole sentences;
   - a question is kept together with its answer;
   - a clip that opens on "and / so / he…" gets its setup sentence;
   - repeated stories and overlaps are removed;
   - whatever still looks wrong is flagged.

## 5. Reviewing and finishing a clip (the clip card)

Each card shows:
- **viral NN/100**: the model's estimate blended with the six score dimensions (the breakdown is shown under the title);
- **orange flags** such as "ends mid-sentence" or "may start mid-thought": check these before publishing;
- **qc PASS / WARN / FAIL**: hover to see the reasons. A FAIL clip is never auto-uploaded;
- **keep / reject / pin**: your decision, saved with the job;
- **edit clip, reframing, subtitles, edit hook, pro mode, dub voice**: the existing OpenShorts editor tools.

**Finish short**, which is where your own value goes in:
- an intro or context card (title plus a context line) over a blurred frame;
- your voiceover, placed one of three ways:
  - **Intro**: plays over the card;
  - **Over clip**: starts at a set second, with the podcast audio ducked under your voice;
  - **Outro**: plays at the end;
- your logo in a corner;
- a source credit line;
- an outro / call-to-action card.

Finish short creates a separate `final_…mp4` file with its own QC report. The original clip stays editable.
Use **download** for the plain clip, or **final short** for the packaged one.

## 6. Source rights (needed before public uploads)

In the YouTube dialog, set **source rights** once per video. This is your own declaration; nothing is checked automatically.

| Category | Meaning | Public / scheduled upload |
|---|---|---|
| I own it | your own podcast or recording | allowed |
| Licensed / permission | written permission from the rights holder | allowed |
| Creative Commons | give the licence (e.g. "CC BY 4.0") and the attribution text | allowed (NC/ND refused for monetised use; attribution required) |
| Public domain | | allowed |
| Not sure | | private / unlisted only |

`rights.default_category` in the config sets a standing default for jobs without a record. For example, `own` if you only ever clip your own show.
The attribution text is added to every description automatically.

Know these limits:
- Permission does **not** prevent a Content ID claim.
- For third-party Shorts **over 60 s**, a claim blocks the Short worldwide. The dialog warns you.
- Reused-content monetisation rules still apply. Your intro, commentary and voiceover are what make an edit your own.

## 7. YouTube upload

### 7.1 Create your OAuth client (once, about 10 minutes)
1. Go to <https://console.cloud.google.com/> and create a project, e.g. "ClipForge".
2. In **APIs & Services → Library**, enable **YouTube Data API v3** and **YouTube Analytics API**.
3. Under **Google Auth Platform → Branding**, set the app name and your e-mail. Under **Audience**, choose **External**, then add your own Google account under **Test users**.
4. Under **Clients → Create client → Desktop app**, click **Download JSON**.
5. Save the file as `/home/mitul/clipper/openshorts/.clipforge/youtube_client.json`. The `.clipforge/` folder is git-ignored.
6. In `clipforge.config.yaml`:
   ```yaml
   youtube:
     oauth_client_file: .clipforge/youtube_client.json
   ```
7. Connect:
   ```bash
   ./clipforge.sh youtube-auth
   ```
   A browser opens. Sign in with the account that owns the channel. You may see "Google hasn't verified this app": choose **Advanced → Go to ClipForge**. Then allow access.
   The token is stored in `.clipforge/youtube_token.json` (mode 600) and is never sent to the browser.

Two Google rules to know before you rely on uploads:
- **Testing-mode tokens expire after 7 days.** While the app's publishing status is "Testing", the connection lasts 7 days and you rerun `youtube-auth`. Switch **Audience → Publish app** to "In production". An unverified personal app is fine for your own account, and then the token keeps working.
- **New API projects upload as private only, until audited.** YouTube locks videos uploaded by an API project that hasn't been audited to private, however you set them. To publish publicly from ClipForge, request the free **YouTube API Services audit** (Google's "YouTube API Services – Audit and Quota Extension" form) for your project. Until then:
  - upload as private and switch to public in YouTube Studio; or
  - use the Upload-Post route (`upload_post.api_key`), which the original OpenShorts already supports.

### 7.2 Uploading by hand
On a clip card, click **youtube**. In the dialog:
1. The dialog drafts 4 title options, a description (with your credit) and hashtags from the clip's own transcript. Edit them as you like.
2. Choose privacy (default private), or pick a date and time to schedule.
3. Click **upload**.

Retrying never creates a duplicate: each file has a publish record. A broken upload resumes where it stopped.

### 7.3 Auto-upload
```yaml
youtube:
  auto_upload: true
  privacy: private              # or unlisted / public (public needs rights, see §6)
  schedule_times: ["18:00"]     # optional: one slot per clip, day by day
  timezone: Asia/Kolkata
  max_per_job: 3                # best N clips per video
  min_score: 60
  require_keep: false           # true = only clips you mark keep/pin, uploaded when you click
  use_final: true               # prefer the packaged final short
  max_per_day: 6                # quota guard: an upload costs ~1,600 of 10,000 daily API units
```

When a job finishes, the best clips are uploaded:
- rejected clips and QC FAILs are skipped;
- pinned clips go first.

Scheduled or public publishing only happens when the job's rights allow it; otherwise clips go up private. The result appears in the job log and on the clip card.

## 8. Performance

**Settings → YouTube · ClipForge** shows:
- the connection state and today's upload count;
- a **refresh metrics** button.

Metrics come only from the official APIs: views, likes, comments, average view duration, average % viewed and subscribers gained. A metric YouTube does not return is shown as **unavailable**.

The groups (duration band, strongest score dimension, packaged or plain, flagged or clean) are averages over your own uploads, with sample sizes. They describe your channel; they don't prove why a clip did well.
Analytics normally arrive 1–2 days after upload.

## 9. Testing without API keys

```bash
.venv/bin/python tools/stub_llm_server.py --port 18766 &
CLIPFORGE_CONFIG=/nonexistent LLM_BASE_URL=http://127.0.0.1:18766/v1 LLM_MODEL=stub \
RENDER_PRESET=hd AUTO_HOOK=1 .venv/bin/python -u main.py -i some_video.mp4 -o /tmp/out
./clipforge.sh test          # backend tests + dashboard lint + build
```

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "Gemini API Key Missing" banner | `llm.gemini.api_key` in the config, then `./clipforge.sh restart` |
| YouTube link download fails | `deno --version` must work; for age-restricted or members-only videos, add `youtube.download_cookies_file` (only for content you may use) |
| Very slow | CPU-only is slow: ~1–2 min per clip plus transcription. Keep `max_concurrent_jobs: 1`. An NVIDIA GPU (README §5) is ~8× faster |
| QC WARN "loudness" | A quiet or very dynamic source: check the clip; `speech_cleanup` is on by default |
| QC FAIL | The file is broken (wrong size, black, silent, decode errors): re-render the clip (reframing → save, or subtitles → apply) |
| YouTube "not connected" after a week | The consent screen is still in Testing: publish it (§7.1) and run `youtube-auth` again |
| Uploads always private | The API project isn't audited yet (§7.1) |
| Port already in use | Nothing to do: `start` picks the next free port and prints it |

Where things are:
- Jobs: `output/<job_id>/`. Each holds `<title>_metadata.json` (clips, scores, source map, rights, review, uploads), the clips, `final_*` files and `*.qc.json` reports.
- Logs: `.clipforge/run/backend.log`.
