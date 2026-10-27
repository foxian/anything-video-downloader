# anything-video-downloader

English | [中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Engines](https://img.shields.io/badge/engines-HLS%20%7C%20DASH%20%7C%20Direct-success)
![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-purple)

**Point it at a player page. Get a clean mp4. Keep a doc for the next visit.**

An Agent Skill with three download engines, automatic encrypted-stream handling, and a set of per-site docs that makes repeat downloads faster. No URL logger, no "one site per tool" limit — just a repeatable workflow that improves with use.

## Why it stands out

- **Learns every new site.** The first time you meet a site, the generic workflow figures out how its player delivers video and writes a reusable doc to `references/sites/<domain>.md`. The next request for that site starts from the doc instead of re-analyzing from zero.
- **Covers the three delivery types you actually run into.** HLS/m3u8 streams, Bilibili-style DASH dual streams, and plain direct mp4 links.
- **Handles the annoying parts.** AES-128 HLS decryption, fMP4 init segments, split audio/video, Windows-illegal filenames, expired signed URLs.
- **Built for real downloads, not demos.** Concurrent segmented download, resume, automatic gap repair, final-file verification, and a per-download log.
- **Batch-friendly.** Ask for several episodes at once.
- **Deterministic paths.** Absolute output paths, no cwd surprises, no hardcoded machine paths.

## What it can download

| Engine | Typical source | What is handled |
|--------|----------------|-----------------|
| `hls` (default) | Streaming sites using `.m3u8` | TS or fMP4 segments, `EXT-X-MAP` init, AES-128 keys, resume per segment |
| `dash` | Bilibili video pages | Video `.m4s` + audio `.m4s`, quality selection, fresh stream URLs resolved at runtime |
| `direct` | Sites exposing a direct mp4 (e.g. Douyin) | Signed URL probing, single-file download, size and duration check |

Anything the workflow can reach without breaking DRM or a paywall is in scope: episode pages, course players, self-hosted HLS, and public video platforms. Adult and piracy sources are intentionally out of scope for this public repository.

## Site docs, reused next time

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

**Give the skill a video page URL. That's it.**

```text
Download the video on this page: https://example.com/watch/123
```

Want a specific folder or quality? Just say so: "save it to D:/Downloads and use 1080p". Several episodes at once? "Download these three episodes: <URL1> <URL2> <URL3>". On multi-quality sites you'll pick a quality each time, with the site doc's default marked as recommended; a same-site batch asks only once.

The skill resolves the stream URL, picks the right engine, downloads, merges, verifies the file, and returns the output path.

## Before first use

One-time setup, two steps.

1. **Install the skill.** Copy the skill root folder (the directory containing `SKILL.md`) into your host's skills directory, then reload the host:
   - Claude Code: `~/.claude/skills/anything-video-downloader/`
   - Trae: `~/.trae-cn/skills/anything-video-downloader/`
2. **Check dependencies (once):**

```text
python "<skill-root>/scripts/check_deps.py"
```

All items `[OK]` and exit code `0` means you are ready. The skill records the result in `.deps-ok.json` and skips this check on later runs; only a download that hits a missing tool re-runs the full check, and it asks before installing anything. Install instructions and chrome-devtools MCP setup: [references/setup.md](anything-video-downloader/references/setup.md).

<details>
<summary><b>Dependencies</b></summary>

| Dependency | Notes |
|------------|-------|
| Python 3.8+ | `python` on PATH; AES-128 decryption needs `cryptography` |
| aria2c | On PATH; concurrent segmented downloads and resume |
| ffmpeg / ffprobe | On PATH; merging and verification (the `direct` engine needs only ffprobe) |
| curl | Fetch playlists/keys and probe direct links; Windows 10/11 ships `curl.exe` |
| Browser tooling | **chrome-devtools MCP** recommended: pass Cloudflare checks and read network requests for media URLs |

</details>

<details>
<summary><b>Manual CLI (optional)</b></summary>

If you would rather run the pieces yourself, build a job and start it:

HLS:

```text
python "<skill-root>/scripts/make_job.py" --page "<player URL>" --m3u8 "<m3u8 URL>" --title "<raw document.title>" --strip "<site title suffix>" --root "<download-dir>"
```

Bilibili:

```text
python "<skill-root>/scripts/make_job.py" --bvid "BV1xx411c7mD" --quality 1080p --root "<download-dir>"
```

Direct (Douyin and similar):

```text
python "<skill-root>/scripts/make_job.py" --url "<video.currentSrc>" --page "<player URL>" --root "<download-dir>"
```

Then run the printed launch command:

```text
python "<skill-root>/scripts/start_hls_windows.py" --args-json "<download-dir>/_job.json"
```

Progress on demand (HLS only): `python "<skill-root>/scripts/hls_progress.py" --root "<download-dir>"`.

</details>

## Built-in safeguards

| Situation | What happens |
|-----------|--------------|
| Download interrupted | Rerun the same request; completed segments are skipped |
| A few segments failed | Gaps <= 5% or one segment are retried automatically |
| Most of the stream failed | Reported as expired URL; a fresh URL is extracted instead of retrying forever |
| Audio/video split (Bilibili) | Both streams downloaded, then merged and verified |
| Odd title / reserved name | Illegal characters stripped, fallback name used, episode numbering added |
| Need proof of success | The final file is checked automatically; `<out>.log` ends with `[完成]` |

## File naming rules

<details>
<summary><b>Reference</b></summary>

1. Take `document.title` and strip the site suffix with `--strip`
2. Remove Windows-illegal characters and reserved names; fall back to `--video-id` when empty
3. Single episode/movie: `<name>.mp4`; series with `--episode N`: `<name>_第<NN>集.mp4`
4. Use `--name` to set the base name directly

</details>

<details>
<summary><b>Repository layout</b></summary>

```text
.
├── README.md
├── README.zh-CN.md
└── anything-video-downloader/
    ├── SKILL.md                  # Main skill doc: full workflow and parameters
    ├── references/setup.md       # Dependency install and chrome-devtools MCP setup
    ├── references/sites/         # Public site docs (filename = primary domain)
    │   ├── _TEMPLATE.md          # Template for new site docs
    │   ├── bilibili.md           # Bilibili DASH
    │   └── douyin.md             # Douyin direct links
    ├── references/sites-private/ # Local private site docs (.gitignore, never committed)
    └── scripts/
        ├── check_deps.py         # Dependency check: aria2c / ffmpeg / ffprobe / curl / cryptography
        ├── make_job.py           # Build job JSON and launch command
        ├── start_hls_windows.py  # Opens the download console and dispatches by engine
        ├── hls_download.py       # HLS: parse -> download -> decrypt -> merge -> verify
        ├── bilibili_download.py  # DASH: resolve streams -> download both -> merge -> verify
        ├── direct_download.py    # Direct: probe -> download -> verify
        └── hls_progress.py       # On-demand segment progress
```

</details>

<details>
<summary><b>Path conventions</b></summary>

- `<skill-root>` means the installed skill directory — the one containing `SKILL.md` (in this repo, `anything-video-downloader/`). Every command in this README uses `<skill-root>/scripts/...`; substitute its absolute path on your machine. The relative links in this README point at the repo copy, not your install path.
- Scripts locate their own resources with `Path(__file__).resolve().parent`, so they work regardless of cwd.
- `${CLAUDE_SKILL_DIR}` is guaranteed only in Claude Code (and mainly for bash injection commands). Other hosts do not guarantee it; cross-host skills should use absolute-path placeholders instead.
- Use forward slashes everywhere; do not write `scripts\...`.
- Replace `<download-dir>` with an absolute path, e.g. `$env:USERPROFILE/Downloads/video-downloader`. Do not hardcode paths from a specific machine.
- `out` / `workdir` / `log` in job JSON must be absolute. `make_job.py` converts them; if you hand-write a job, relative values resolve against the **job file's directory** (not the cwd).

</details>

## Compliance boundaries

- Download only content you own or are authorized to access
- No DRM or paywall bypass; no finished media or piracy site lists are provided
- Site docs are split into public and private tiers; the public repository contains no private "you know what" site docs
- For personal study and backup only; respect copyright, do not redistribute or use commercially

## Notes

- URLs containing `&` should never go directly on a PowerShell command line — always use job JSON
- Bilibili stream URLs expire after roughly 2 hours and are re-resolved at runtime; expired Douyin signed URLs must be extracted again in the browser
- Before publishing or packaging, confirm `references/sites-private/` is not in the artifact, especially with release tools that pack the whole working tree

Full workflow, parameter tables, and per-engine behavior: [SKILL.md](anything-video-downloader/SKILL.md).
