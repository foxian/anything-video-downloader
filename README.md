# anything-video-downloader

English | [中文](README.zh-CN.md)

A skill package that downloads videos from web player pages to mp4. It ships three engines and a built-in site knowledge base.

- **HLS (default)**: m3u8 streaming sites, TS or fMP4 segments, automatic AES-128 decryption
- **DASH**: Bilibili dual streams (video `.m4s` + audio `.m4s`), fresh stream URLs resolved at runtime
- **Direct**: direct mp4 links (e.g., Douyin), single-file download

When you encounter a new site, analyze it with the generic workflow and save a site doc: public, lawful sites go to `references/sites/`; adult or piracy sites go to `references/sites-private/` (excluded from the repo).

## Repository layout

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

## Path conventions

- This README lives at the repository root, so `anything-video-downloader/scripts/...` in commands is a **repo-relative path**.
- After installing the skill, the **skill root is `anything-video-downloader/`** (the directory containing `SKILL.md`). Inside that directory you can use `scripts/...`; elsewhere, write `<skill-root>/scripts/...` and substitute an absolute path.
- `${CLAUDE_SKILL_DIR}` is guaranteed only in Claude Code (and mainly for bash injection commands). Other hosts do not guarantee it; cross-host skills should use absolute-path placeholders instead. Scripts locate their own directory with `Path(__file__).resolve().parent`.
- Use forward slashes everywhere; do not write `scripts\...`.
- Replace `<download-dir>` in examples with an absolute path, e.g. `$env:USERPROFILE/Downloads/video-downloader`. Do not hardcode paths from a specific machine.
- `out` / `workdir` / `log` in job JSON must be absolute. `make_job.py` converts them; if you hand-write a job, relative values resolve against the **job file's directory** (not the cwd).

## Requirements

| Dependency | Notes |
|------------|-------|
| Python 3.8+ | `python` on PATH; AES-128 decryption needs `cryptography` |
| aria2c | On PATH; concurrent segmented downloads and resume |
| ffmpeg / ffprobe | On PATH; merging and output verification (the `direct` engine needs only ffprobe) |
| curl | Fetch playlists/keys and probe direct links; Windows 10/11 ships `curl.exe` |
| Browser tooling | **chrome-devtools MCP** recommended: pass Cloudflare checks and read network requests for media URLs |

Check before downloading:

```text
python anything-video-downloader/scripts/check_deps.py
```

It reports `[OK]` / `[missing]` per item and prints install commands for Windows, macOS, and Linux. Full setup steps and **chrome-devtools MCP configuration** are in [references/setup.md](anything-video-downloader/references/setup.md).

## Quick start

### 0. Check dependencies

```text
python anything-video-downloader/scripts/check_deps.py
```

Continue only when the exit code is `0`. If ffmpeg or aria2c is missing, install it per [references/setup.md](anything-video-downloader/references/setup.md) and reopen the terminal. `start_hls_windows.py` also pre-checks dependencies: when something is missing it reports in the current terminal instead of opening a download window.

### 1. Extract the media URL

Open the player page in a browser, wait for any challenge and the player to finish loading, then follow the site doc:

- HLS: filter network requests for `.m3u8` (preferred), or read the player config variable
- Bilibili: use `ep_id` / `bvid` / `aid` directly, no browser needed
- Direct: read `<video>.currentSrc` (e.g., Douyin)

Site doc lookup order: `references/sites/` under the skill root (public), then `references/sites-private/` (local only), then the generic workflow in `SKILL.md`.

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

Run that command to open the download console. `start_hls_windows.py` dispatches to the right script based on the job's `engine`.

### 3. Batch multiple episodes

Write a JSON array (each item: `{title, m3u8|url|ep_id, page, episode?, video_id?}`), then:

```text
python anything-video-downloader/scripts/make_job.py --batch "<download-dir>/batch.json" --strip "<site suffix>" --root "<download-dir>"
```

Job files go to `<download-dir>/_jobs/`.

## Output and naming

The rules live in `build_filename` in `make_job.py`:

1. Take `document.title` and strip the site suffix with `--strip`
2. Remove Windows-illegal characters and reserved names; fall back to `--video-id` when empty
3. Single episode/movie: `<name>.mp4`; add `--episode N` for series: `<name>_第<NN>集.mp4`
4. Use `--name` to set the base name directly

## Progress and logs

- The download console has its own progress bar; keep it the only window
- Read `<out>.log` for results: `[完成]` means success, `[错误]` includes the reason
- Resume: rerunning the same command skips completed segments
- Small gaps (<=5% or one segment) are retried automatically; larger gaps usually mean expired URLs, so extract again
- Run only when the user asks for progress: `python anything-video-downloader/scripts/hls_progress.py --root "<download-dir>"` (HLS only)

## Compliance boundaries

- Download only content you own or are authorized to access
- The public repository contains no adult-site docs; site docs are split into public and private tiers
- Do not bypass DRM or paywalls; no finished media or piracy site lists are provided
- For personal study and backup only; respect copyright, do not redistribute or use commercially

## Notes

- `anything-video-downloader/scripts/...` in this README is repo-relative and must be run from the repository root; elsewhere use the absolute path of the skill directory
- Always use absolute paths for output directories and job JSON to avoid miscounting directory levels
- Never put URLs containing `&` directly on a PowerShell command line; always go through job JSON
- Bilibili stream URLs expire after roughly 2 hours and are re-resolved at runtime; expired Douyin signed URLs must be extracted again in the browser
- Before publishing or packaging, confirm `references/sites-private/` is not in the artifact, especially with release tools that pack the whole working tree
- For personal study and backup only; respect copyright, do not redistribute or use commercially

See [SKILL.md](anything-video-downloader/SKILL.md) for the full workflow, parameter tables, and per-engine script behavior.
