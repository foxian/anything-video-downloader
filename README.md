# anything-video-downloader

English | [中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Engines](https://img.shields.io/badge/engines-HLS%20%7C%20DASH%20%7C%20Direct-success)
![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-purple)

**Point it at a player page. Get a clean mp4. Keep the knowledge for the next site.**

An Agent Skill with three download engines, automatic encrypted-stream handling, and a site knowledge base that grows every time you use it. No URL logger, no "one site per tool" limit — just a repeatable workflow that gets faster as it learns.

## Why it stands out

- **Learns every new site.** The first time you meet a site, the generic workflow figures out how its player delivers video and writes a reusable doc to `references/sites/<domain>.md`. The next request for that site starts from the doc instead of re-analyzing from zero.
- **Covers the three delivery types you actually run into.** HLS/m3u8 streams, Bilibili-style DASH dual streams, and plain direct mp4 links.
- **Handles the annoying parts.** AES-128 HLS decryption, fMP4 init segments, split audio/video, Windows-illegal filenames, expired signed URLs.
- **Built for real downloads, not demos.** Concurrent segmented download, resume, automatic gap repair, ffprobe verification, and a per-download log.
- **Batch-friendly.** Turn a season's worth of episodes into jobs in one command.
- **Deterministic paths.** Absolute output paths, no cwd surprises, no hardcoded machine paths.

## What it can download

| Engine | Typical source | What is handled |
|--------|----------------|-----------------|
| `hls` (default) | Streaming sites using `.m3u8` | TS or fMP4 segments, `EXT-X-MAP` init, AES-128 keys, resume per segment |
| `dash` | Bilibili video pages | Video `.m4s` + audio `.m4s`, quality selection, fresh stream URLs resolved at runtime |
| `direct` | Sites exposing a direct mp4 (e.g. Douyin) | Signed URL probing, single-file download, size and duration check |

Anything the workflow can reach without breaking DRM or a paywall is in scope: episode pages, course players, self-hosted HLS, and public video platforms. Adult and piracy sources are intentionally out of scope for this public repository.

## The knowledge base compounds

```text
new site
   |
   v
open player  ->  inspect network / player config  ->  identify engine
   |                                                   |
   |                                                   v
   |                                        download, verify, log
   |                                                   |
   +---------------------<----------------------------+
                         |
                         v
              write references/sites/<domain>.md
                         |
                         v
     next request: read the doc, skip the analysis, download
```

Lookup order: public `references/sites/` -> local `references/sites-private/` -> generic workflow in `SKILL.md`. A site doc records matching URLs, the player type, how to extract the media URL, encryption, CDN quirks, expiration behavior, and a verified command example.

## Quick start

### 0. Check dependencies

```text
python anything-video-downloader/scripts/check_deps.py
```

Each item reports `[OK]` or `[missing]`, with install commands for Windows, macOS, and Linux. Continue only when the exit code is `0`.

### 1. Extract the media URL

Open the player page, let any challenge and the player finish loading, then follow the site doc:

- **HLS**: filter network requests for `.m3u8` (preferred), or read the player config variable
- **Bilibili**: use `ep_id` / `bvid` / `aid` directly — no browser needed
- **Direct**: read `<video>.currentSrc`

### 2. Build a job and start

HLS:

```text
python anything-video-downloader/scripts/make_job.py --page "<player URL>" --m3u8 "<m3u8 URL>" --title "<raw document.title>" --strip "<site title suffix>" --root "<download-dir>"
```

Bilibili:

```text
python anything-video-downloader/scripts/make_job.py --bvid "BV1xx411c7mD" --quality 1080p --root "<download-dir>"
```

Direct (Douyin and similar):

```text
python anything-video-downloader/scripts/make_job.py --url "<video.currentSrc>" --page "<player URL>" --root "<download-dir>"
```

`make_job.py` prints the cleaned filename, output path, job JSON path, and an absolute-path launch command:

```text
python "<skill-root>/scripts/start_hls_windows.py" --args-json "<download-dir>/_job.json"
```

Run it to open the download console; the launcher dispatches to the right engine.

### 3. Batch a whole season

Write a JSON array (each item: `{title, m3u8|url|ep_id, page, episode?, video_id?}`), then:

```text
python anything-video-downloader/scripts/make_job.py --batch "<download-dir>/batch.json" --strip "<site suffix>" --root "<download-dir>"
```

Job files land in `<download-dir>/_jobs/`. Start them one by one; keep about three concurrent downloads for the same site.

## Built-in safeguards

| Situation | What happens |
|-----------|--------------|
| Download interrupted | Rerun the same command; completed segments are skipped |
| A few segments failed | Gaps <= 5% or one segment are retried automatically |
| Most of the stream failed | Reported as expired URL; extract a fresh one instead of retrying forever |
| Audio/video split (Bilibili) | Both streams downloaded, then merged and verified |
| Odd title / reserved name | Illegal characters stripped, `--video-id` fallback, episode numbering added |
| Need proof of success | `ffprobe` checks the final file; `<out>.log` ends with `[完成]` |

## Output and naming

1. Take `document.title` and strip the site suffix with `--strip`
2. Remove Windows-illegal characters and reserved names; fall back to `--video-id` when empty
3. Single episode/movie: `<name>.mp4`; series with `--episode N`: `<name>_第<NN>集.mp4`
4. Use `--name` to set the base name directly

Progress on demand (HLS only):

```text
python anything-video-downloader/scripts/hls_progress.py --root "<download-dir>"
```

## Requirements

| Dependency | Notes |
|------------|-------|
| Python 3.8+ | `python` on PATH; AES-128 decryption needs `cryptography` |
| aria2c | On PATH; concurrent segmented downloads and resume |
| ffmpeg / ffprobe | On PATH; merging and verification (the `direct` engine needs only ffprobe) |
| curl | Fetch playlists/keys and probe direct links; Windows 10/11 ships `curl.exe` |
| Browser tooling | **chrome-devtools MCP** recommended: pass Cloudflare checks and read network requests for media URLs |

Full setup, including chrome-devtools MCP configuration: [references/setup.md](anything-video-downloader/references/setup.md).

<details>
<summary><b>Repository layout</b></summary>

```text
.
├── README.md
├── README.zh-CN.md
└── anything-video-downloader/
    ├── SKILL.md                  # Main skill doc: full workflow and parameters
    ├── references/setup.md       # Dependency install and chrome-devtools MCP setup
    ├── references/sites/         # Public site knowledge base (filename = primary domain)
    │   ├── _TEMPLATE.md          # Template for new site docs
    │   ├── bilibili.md           # Bilibili DASH
    │   └── douyin.md             # Douyin direct links
    ├── references/sites-private/ # Local private site docs (.gitignore, never committed)
    └── scripts/
        ├── check_deps.py         # Dependency check: aria2c / ffmpeg / ffprobe / curl / cryptography
        ├── make_job.py           # Build job JSON and launch command (recommended entry point)
        ├── start_hls_windows.py  # Opens the download console and dispatches by engine
        ├── hls_download.py       # HLS: parse -> download -> decrypt -> merge -> verify
        ├── bilibili_download.py  # DASH: resolve streams -> download both -> merge -> verify
        ├── direct_download.py    # Direct: probe -> download -> verify
        └── hls_progress.py       # On-demand segment progress
```

</details>

<details>
<summary><b>Path conventions</b></summary>

- This README lives at the repository root, so `anything-video-downloader/scripts/...` in commands is a **repo-relative path**.
- After installing the skill, the **skill root is `anything-video-downloader/`** (the directory containing `SKILL.md`). Inside that directory you can use `scripts/...`; elsewhere, write `<skill-root>/scripts/...` and substitute an absolute path.
- `${CLAUDE_SKILL_DIR}` is guaranteed only in Claude Code (and mainly for bash injection commands). Other hosts do not guarantee it; cross-host skills should use absolute-path placeholders instead. Scripts locate their own directory with `Path(__file__).resolve().parent`.
- Use forward slashes everywhere; do not write `scripts\...`.
- Replace `<download-dir>` with an absolute path, e.g. `$env:USERPROFILE/Downloads/video-downloader`. Do not hardcode paths from a specific machine.
- `out` / `workdir` / `log` in job JSON must be absolute. `make_job.py` converts them; if you hand-write a job, relative values resolve against the **job file's directory** (not the cwd).

</details>

## Compliance boundaries

- Download only content you own or are authorized to access
- No DRM or paywall bypass; no finished media or piracy site lists are provided
- Site docs are split into public and private tiers; the public repository contains no adult-site docs
- For personal study and backup only; respect copyright, do not redistribute or use commercially

## Notes

- URLs containing `&` should never go directly on a PowerShell command line — always use job JSON
- Bilibili stream URLs expire after roughly 2 hours and are re-resolved at runtime; expired Douyin signed URLs must be extracted again in the browser
- Before publishing or packaging, confirm `references/sites-private/` is not in the artifact, especially with release tools that pack the whole working tree

Full workflow, parameter tables, and per-engine behavior: [SKILL.md](anything-video-downloader/SKILL.md).
