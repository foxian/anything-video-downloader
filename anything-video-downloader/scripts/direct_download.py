#!/usr/bin/env python3
"""Direct media URL -> mp4: probe, aria2 single-file download, ffprobe verify.

For sites whose player hands out a plain media file (mp4/m4a/...) instead of an
m3u8 playlist or a DASH pair — e.g. 抖音 (douyin) web codecs player exposes the
file via `video.currentSrc`. The job carries the direct `url`; unlike bilibili
there is no public API to re-resolve it, so the URL is baked in and the agent
must extract it fresh from the browser and start the download promptly (signed
URLs expire).

步骤（3 步）：探测直链(含 Range) -> aria2 下载 -> ffprobe 校验。
复用 hls_download.py 的日志(Tee)/进度条/错误输出，保证各引擎的控制台与 <out>.log 行为一致。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from hls_download import (
    Tee,
    UA,
    close_log,
    die,
    fmt_hms,
    format_bar,
    open_log,
    preflight,
    setup_console,
    wait_enter,
)


def probe_total(url: str, page: str, extra: list[str]) -> tuple[int | None, str]:
    """Range 0-0 探测：验证链接可用 + 拿总字节数与 content-type。"""
    cmd = ["curl.exe", "-s", "-L", "--max-time", "25", "-r", "0-0", "-D", "-", "-o", "NUL",
           url, "-H", f"Referer: {page}", "-H", f"User-Agent: {UA}"]
    for h in extra:
        if h:
            cmd += ["-H", h]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        die("直链探测超时，链接可能过期：回浏览器重新提取后重试")
    if r.returncode != 0:
        die(f"直链探测失败（curl {r.returncode}），链接可能过期：回浏览器重新提取后重试")
    head = r.stdout or ""
    codes = re.findall(r"HTTP/\S+\s+(\d+)", head)
    final = codes[-1] if codes else "?"
    if final not in ("200", "206"):
        die(f"直链返回 {final}，链接可能过期或被拒：回浏览器重新提取后重试")
    ct = ""
    m = re.search(r"Content-Type:\s*([^\r\n;]+)", head, re.I)
    if m:
        ct = m.group(1).strip().lower()
    m = re.search(r"Content-Range:\s*bytes\s+\d+-\d+/(\d+)", head, re.I)
    total = int(m.group(1)) if m else None
    return total, ct


def ext_from(content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if "mp4" in ct:
        return ".mp4"
    if "webm" in ct:
        return ".webm"
    if "audio" in ct:
        return ".m4a"
    if "mpegurl" in ct or "m3u8" in ct:
        die("该地址是 m3u8 播放列表，不是直链文件；请用 hls 引擎（--m3u8）")
    path = re.sub(r"\?.*$", "", url)
    m = re.search(r"\.(mp4|m4v|mov|webm|mkv|flv|m4a|mp3|aac|ogg|ts)$", path, re.I)
    if m:
        return "." + m.group(1).lower()
    return ".mp4"


def looks_media(path: Path) -> bool:
    """挡下 HTML 错误页等坏文件：查常见容器魔数。"""
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if len(head) >= 8 and head[4:8] == b"ftyp":        # mp4/mov
        return True
    if head[:4] == b"\x1a\x45\xdf\xa3":                 # matroska/webm
        return True
    if head[:4] == b"RIFF":                             # avi/webp
        return True
    if head[:4] == b"OggS":                             # ogg/opus
        return True
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3"):  # mp3
        return True
    if head[:1] == b"\x47":                             # MPEG-TS
        return True
    return False


def short_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def run(args: argparse.Namespace) -> None:
    preflight("direct", args.aria2, args.ffmpeg, args.ffprobe)
    out_file = Path(args.out).resolve()
    workdir = (Path(args.workdir).resolve() if args.workdir
               else out_file.with_suffix("").parent / (out_file.stem + "_ts"))
    extra = list(args.extra_header or [])
    out_file.parent.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    total_steps = 3
    print(f"影片：{out_file.stem}")

    # [1/3] 探测直链
    total, content_type = probe_total(args.url, args.page, extra)
    ext = ext_from(content_type, args.url)
    media = workdir / ("media" + ext)

    def mb(n: int | None) -> str:
        return f"{n / (1024 * 1024):.1f}" if n else "?"

    print(f"[1/{total_steps}] 探测直链：{short_url(args.url)}")
    print(f"      类型 {content_type or '?'}，大小 {mb(total)} MB")

    # 本地完成状态（断点续传）
    ctrl = Path(str(media) + ".aria2")
    done = (media.exists() and not ctrl.exists() and media.stat().st_size > 0
            and (total is None or media.stat().st_size == total) and looks_media(media))

    input_file = workdir / "aria2.input.txt"

    def build_input() -> int:
        if done:
            return 0
        if media.exists() and not ctrl.exists():
            media.unlink(missing_ok=True)   # 残留坏文件，删掉重下
        lines = [args.url, f"  out={media.name}",
                 f"  header=Referer: {args.page}", f"  header=User-Agent: {UA}"]
        for h in extra:
            if h:
                lines.append(f"  header={h}")
        input_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    def run_aria2(attempt: int) -> int:
        aria_log = workdir / "aria2.log"
        aria_err = workdir / "aria2.log.err"
        aria2 = shutil.which(args.aria2) or args.aria2
        aria_cmd = [
            aria2, f"-x{args.parallel}", f"-s{args.parallel}", "--min-split-size=4M",
            "--max-tries=5", "--retry-wait=2", "--connect-timeout=15", "--timeout=60",
            "--continue=true", "--file-allocation=none", "--auto-file-renaming=false",
            "-d", str(workdir), "-i", str(input_file),
        ]
        mode = "w" if attempt == 1 else "a"
        with aria_log.open(mode, encoding="utf-8", errors="replace") as out_log, \
                aria_err.open(mode, encoding="utf-8", errors="replace") as err_log:
            p = subprocess.Popen(aria_cmd, stdout=out_log, stderr=err_log)
            t0 = time.time()
            last_bytes, last_t, rate, last_log_t = 0, t0, 0.0, t0
            while p.poll() is None:
                time.sleep(0.6)
                cur = media.stat().st_size if media.exists() else 0
                now = time.time()
                dt = now - last_t
                if dt >= 1.5:
                    inst = max(0.0, (cur - last_bytes) / dt)
                    rate = inst if rate <= 0 else rate * 0.7 + inst * 0.3
                    last_bytes, last_t = cur, now
                pct = int(100 * cur / total) if total else 0
                eta = (fmt_hms((total - cur) / rate) if rate > 0 and total and cur < total else "--:--")
                print(f"\r  [{format_bar(pct)}] {pct:3d}%  {mb(cur)}/{mb(total)} MB"
                      f"  {rate / (1024 * 1024):5.1f} MB/秒  剩余 {eta}   ", end="", flush=True)
                if now - last_log_t >= 5:
                    sys.stdout.write("\r" + " " * 90 + "\r")
                    sys.stdout.write(f"      [进度] {mb(cur)}/{mb(total)} MB {pct}% 剩余 {eta}\n")
                    sys.stdout.flush()
                    last_log_t = now
        print()
        return p.returncode

    # [2/3] 下载
    if build_input() > 0:
        print(f"[2/{total_steps}] 下载直链：每文件并发 {args.parallel}")
        rc = run_aria2(1)
        bad = (not media.exists() or media.stat().st_size <= 0
               or (total is not None and media.stat().st_size != total)
               or not looks_media(media))
        if bad:
            print("      未完成，自动补跑一轮 ...")
            if build_input() > 0:
                run_aria2(2)
                bad = (not media.exists() or media.stat().st_size <= 0
                       or (total is not None and media.stat().st_size != total)
                       or not looks_media(media))
        if bad:
            print(f"[错误] 下载不完整（aria2 exit {rc}）。aria2.log 尾部：")
            try:
                tail = (workdir / "aria2.log").read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                for line in tail:
                    print(f"  {line}")
            except OSError:
                pass
            die("下载不完整，重新运行同一命令即可断点续传（若一直失败，链接已过期：回浏览器重新提取）")
        if rc not in (0, None):
            print("      （aria2 退出码非 0，但文件已补齐，继续校验）")
    else:
        print(f"[2/{total_steps}] 下载直链：文件已存在（断点续传），跳过")

    # 落到最终输出路径
    if media.resolve() != out_file:
        out_file.unlink(missing_ok=True)
        shutil.move(str(media), str(out_file))

    # [3/3] 校验
    print(f"[{total_steps}/{total_steps}] 校验输出 ...")
    ffprobe = shutil.which(args.ffprobe) or args.ffprobe
    probe = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out_file)],
        text=True, encoding="utf-8", errors="replace",
    ).strip()
    try:
        out_dur = float(probe)
    except ValueError:
        out_dur = 0.0
    size_mb = out_file.stat().st_size / (1024 * 1024)
    codecs = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(out_file)],
        text=True, encoding="utf-8", errors="replace",
    )
    kinds = [k.strip() for k in codecs.splitlines() if k.strip()]
    has_video = "video" in kinds
    has_audio = "audio" in kinds
    track_note = f"流：{'视频' if has_video else '无视频'}/{'音频' if has_audio else '无音频'}"
    if not has_video:
        print("      [警告] 输出没有视频流，下载可能不完整")
    if not has_audio:
        print("      [警告] 输出没有音频流（静音或源本身无音轨）")
    print(f"[完成] {out_file}")
    print(f"       大小 {size_mb:,.0f} MB，时长 {fmt_hms(out_dur)}，{track_note}")

    if args.cleanup:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[清理] 已删除临时目录 {workdir}")


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Download a direct media URL to mp4")
    ap.add_argument("--url", required=True, help="直链媒体地址（mp4/m4a 等，非 m3u8）")
    ap.add_argument("--page", required=True, help="播放页 URL（Referer）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parallel", type=int, default=12, help="aria2 最大连接数")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--aria2", default="aria2c")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--extra-header", action="append", default=[],
                    help="可重复，如 'Cookie: ...'")
    ap.add_argument("--log", default="", help="同时把全部输出写入该文件（无人值守时回读结果）")
    ap.add_argument("--pause", action="store_true", help="wait for Enter before closing console")
    args = ap.parse_args()

    log_handle = open_log(args.log) if args.log else None
    if log_handle is not None:
        sys.stdout = Tee(sys.stdout, log_handle)
        sys.stderr = Tee(sys.stderr, log_handle)
        print(f"[日志] 输出同时写入 {Path(args.log).resolve()}")

    try:
        run(args)
    except FileNotFoundError as exc:
        print(f"[错误] 运行中缺少外部工具：{exc.filename or exc}", file=sys.stderr)
        import check_deps

        try:
            check_deps.recover("direct")
        except SystemExit:
            pass
        if args.pause:
            wait_enter()
        close_log(log_handle)
        return 1
    except SystemExit:
        if args.pause:
            wait_enter()
        close_log(log_handle)
        raise
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        if args.pause:
            wait_enter()
        close_log(log_handle)
        return 1
    if args.pause:
        wait_enter()
    close_log(log_handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
