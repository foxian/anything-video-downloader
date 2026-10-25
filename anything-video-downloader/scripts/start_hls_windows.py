#!/usr/bin/env python3
"""Start HLS/DASH download in one visible Windows console.

Write a UTF-8 JSON job file, then:
  python start_hls_windows.py --args-json job.json

Do not put m3u8 URLs on a PowerShell command line (ampersands/quotes break).
The download console already prints the live progress bar; do not open a second window.

Job fields, HLS engine (m3u8 present): m3u8, page, out (required);
parallel, quality, max_segments, cleanup, extra_headers, workdir, log, pause (optional).
Relative out/workdir/log values resolve against the job file's directory, not cwd.

Job fields, DASH engine (B站, "engine": "dash" or ep_id/bvid/aid present):
page, out (required); ep_id OR bvid OR aid, quality ("highest"/"1080p+"/qn),
codec ("avc"/"hev"), parallel, cleanup, extra_headers, workdir, log, pause (optional).
Fresh stream URLs are resolved by bilibili_download.py at run time (they expire ~2h).

Job fields, DIRECT engine (直链 mp4，如抖音, "engine": "direct" or url present without m3u8):
page, out, url (required); parallel, cleanup, extra_headers, workdir, log, pause (optional).
The signed url is baked into the job (no API to re-resolve); extract it fresh and start
promptly — it expires. direct_download.py: probe -> aria2 -> ffprobe verify.

  log   - tee the download's whole output into this file. Defaults to "<out>.log",
          so an unattended run stays readable after the console window closes.
          Pass "" to turn it off.
  pause - keep the console open until Enter. Defaults to false (closes when done);
          set true when a human is watching and wants to read the result on screen.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CREATE_NEW_CONSOLE = 0x00000010


def resolve_args_json(value: str) -> Path:
    """Resolve --args-json without guessing from unrelated directories.

    Absolute paths are used as-is; relative paths resolve against the current
    working directory. A missing file fails here with a clear message instead
    of silently matching a same-named file somewhere above the skill directory.
    """
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"[错误] 找不到 job 文件: {path}\n请用绝对路径传 --args-json。")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--args-json", required=True, help="UTF-8 JSON with m3u8/page/out/...")
    args = ap.parse_args()
    job_path = resolve_args_json(args.args_json)
    data = json.loads(job_path.read_text(encoding="utf-8"))
    job_dir = job_path.parent

    def job_path_field(value: str) -> str:
        """把 job 里的路径统一成绝对路径；相对路径按 job 文件所在目录解析。"""
        p = Path(value).expanduser()
        return str(p if p.is_absolute() else (job_dir / p).resolve())

    page = data["page"]
    out = job_path_field(data["out"])
    parallel = int(data.get("parallel", 12))
    quality = data.get("quality") or "highest"
    max_segments = int(data.get("max_segments", 0) or 0)
    cleanup = bool(data.get("cleanup", False))
    extra_headers = list(data.get("extra_headers") or [])
    workdir = job_path_field(data["workdir"]) if data.get("workdir") else ""
    log = data.get("log")
    if log is None:
        log = out + ".log"
    elif log:
        log = job_path_field(log)
    pause = bool(data.get("pause", False))

    engine = data.get("engine")
    if engine is None:
        if data.get("ep_id") or data.get("bvid") or data.get("aid"):
            engine = "dash"
        elif data.get("url") and not data.get("m3u8"):
            engine = "direct"
        else:
            engine = "hls"

    here = Path(__file__).resolve().parent
    py = sys.executable

    import check_deps

    missing = check_deps.missing_for(engine)
    if missing:
        names = "、".join(check_deps.display_name(t) for t in missing)
        print(f"[错误] 缺少必需依赖：{names}", file=sys.stderr)
        print("", file=sys.stderr)
        for line in check_deps.hint_lines(missing):
            print(line, file=sys.stderr)
        print(f"\n复核：python \"{here / 'check_deps.py'}\" --engine {engine}", file=sys.stderr)
        return 1

    if engine == "dash":
        if not (data.get("ep_id") or data.get("bvid") or data.get("aid")):
            print("dash job 需要 ep_id 或 bvid/aid", file=sys.stderr)
            return 1
        dl = here / "bilibili_download.py"
        dl_cmd = [
            py, "-u", str(dl),
            "--page", page,
            "--out", out,
            "--quality", quality,
            "--codec", str(data.get("codec") or "avc"),
            "--parallel", str(parallel),
        ]
        if data.get("ep_id"):
            dl_cmd += ["--ep-id", str(data["ep_id"])]
        if data.get("bvid"):
            dl_cmd += ["--bvid", str(data["bvid"])]
        if data.get("aid"):
            dl_cmd += ["--aid", str(data["aid"])]
    elif engine == "direct":
        if not data.get("url"):
            print("direct job 需要 url", file=sys.stderr)
            return 1
        dl = here / "direct_download.py"
        dl_cmd = [
            py, "-u", str(dl),
            "--url", data["url"],
            "--page", page,
            "--out", out,
            "--parallel", str(parallel),
        ]
    else:
        if not data.get("m3u8"):
            print("hls job 需要 m3u8", file=sys.stderr)
            return 1
        dl = here / "hls_download.py"
        dl_cmd = [
            py, "-u", str(dl),
            "--m3u8", data["m3u8"],
            "--page", page,
            "--out", out,
            "--parallel", str(parallel),
            "--quality", quality,
        ]
        if max_segments:
            dl_cmd += ["--max-segments", str(max_segments)]
    if workdir:
        dl_cmd += ["--workdir", workdir]
    if cleanup:
        dl_cmd.append("--cleanup")
    if log:
        dl_cmd += ["--log", log]
    if pause:
        dl_cmd.append("--pause")
    for h in extra_headers:
        if h:
            dl_cmd += ["--extra-header", h]

    flags = CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    p1 = subprocess.Popen(dl_cmd, creationflags=flags)
    print("started download pid %s" % p1.pid)
    print("out %s" % out)
    if log:
        print("log %s" % log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
