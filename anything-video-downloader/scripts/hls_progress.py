#!/usr/bin/env python3
"""Show HLS segment-folder progress written by hls_download.py."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def setup_console() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fmt_hms(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_bar(pct: int, length: int = 24) -> str:
    pct = max(0, min(100, pct))
    filled = length * pct // 100
    return "#" * filled + "." * (length - filled)


def get_stats(dir_path: Path, total_override: int, max_idle_min: int) -> dict:
    files = list(dir_path.glob("*.ts"))
    expected = total_override
    total_file = dir_path / "total.txt"
    if not expected and total_file.exists():
        try:
            expected = int(total_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip())
        except Exception:
            expected = 0
    nbytes = sum(f.stat().st_size for f in files)
    mb = nbytes / (1024 * 1024)
    rate = 0.0
    age_min = float("inf")
    if len(files) >= 2:
        mtimes = [f.stat().st_mtime for f in files]
        t0, t1 = min(mtimes), max(mtimes)
        sec = t1 - t0
        if sec > 1:
            rate = len(files) / sec
        age_min = (time.time() - t1) / 60.0
    done_all = expected > 0 and len(files) >= expected
    if expected > 0:
        active = (not done_all) or (age_min <= max_idle_min)
    else:
        active = age_min <= max_idle_min
    return {
        "dir": dir_path.name,
        "done": len(files),
        "total": expected,
        "mb": mb,
        "rate": rate,
        "done_all": done_all,
        "active": active,
    }


def render(rows: list, watch: bool = False) -> None:
    print()
    for r in rows:
        pct = min(100, int(100 * r["done"] / r["total"])) if r["total"] > 0 else 0
        eta = "--:--"
        if (not r["done_all"]) and r["rate"] > 0 and r["total"] > 0:
            eta = fmt_hms((r["total"] - r["done"]) / r["rate"])
        if r["done_all"]:
            state = "DONE"
        elif r["total"] == 0:
            state = "?? total unknown"
        else:
            state = "ETA " + eta
        name = r["dir"]
        print("%-12s [%s] %4d/%-4s %3d%%  %7.1f MB  %s" % (
            name[:12], format_bar(pct), r["done"], r["total"], pct, r["mb"], state
        ))
    now = datetime.now().strftime("%H:%M:%S")
    print("")
    if watch:
        print("(updated %s  - Ctrl+C or close window to stop)" % now)
    else:
        print("(查询于 %s)" % now)


def active_dirs(root: Path) -> list:
    out = []
    if not root.exists():
        return out
    for d in root.iterdir():
        if d.is_dir() and d.name.endswith("_ts") and any(d.glob("*.ts")):
            out.append(d)
    return out


def show_once(args: argparse.Namespace) -> None:
    if args.workdir:
        dirs = [Path(args.workdir)]
    else:
        dirs = active_dirs(Path(args.root))
    if not dirs:
        print("no *_ts segment folders with .ts files under %s yet ..." % args.root)
        print("(download already finished or failed? read <out>.log next to the mp4 for [完成]/[错误])")
        return
    rows = [get_stats(d, args.total, args.max_idle_min) for d in dirs]
    if (not args.workdir) and (not args.all):
        rows = [r for r in rows if r["active"]]
    if not rows:
        print("no active downloads (finished/idle dirs hidden; use --all to show)")
        return
    render(rows, watch=args.watch)


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="")
    ap.add_argument("--root", default=".")
    ap.add_argument("--total", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-idle-min", type=int, default=10)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=2)
    args = ap.parse_args()
    if not args.watch:
        show_once(args)
        return 0
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print("=== HLS download progress ===")
            show_once(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
