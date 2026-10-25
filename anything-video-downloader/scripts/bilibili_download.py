#!/usr/bin/env python3
"""Bilibili DASH -> mp4: 取 playview/playurl 直链，aria2 下载视频+音频双流，ffmpeg 合并。

B 站没有 m3u8——视频/音频是两条独立的整段 fMP4 流（.m4s）。job 只带 ep_id/bvid/aid +
画质/编码，直链在运行时现取：playview/playurl 给出的签名地址约 2 小时过期，不要烤进 job。

步骤（4 步）：解析直链(含 Range 探测) -> aria2 下载双流 -> ffmpeg 合并 -> ffprobe 校验。
复用 hls_download.py 的日志(Tee)/进度条/错误输出，保证两个引擎的控制台与 <out>.log 行为一致。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
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

API = "https://api.bilibili.com"

# 画质标签 -> bilibili qn。数字串直接当 qn（<100 或 112 这类）；四位数当分辨率。
QN_LABELS = {"1080p+": 112, "1080p": 80, "720p": 64, "480p": 32, "360p": 16}
RES_TO_QN = {1080: 80, 720: 64, 480: 32, 360: 16, 2160: 127}
QN_TEXT = {112: "1080P+ 高码率", 80: "1080P 高清", 64: "720P 准高清", 32: "480P 标清", 16: "360P 流畅"}
# --codec 别名 -> dash codecs 前缀（avc1.../hev1.../av01...）
CODEC_PREFIX = {"avc": "avc", "h264": "avc", "h.264": "avc", "avc1": "avc",
                "hev": "hev", "hevc": "hev", "h265": "hev", "h.265": "hev",
                "av1": "av0"}


def api_get(url: str, extra_headers: list[str], page: str) -> dict:
    headers = {"User-Agent": UA, "Referer": page or "https://www.bilibili.com/",
               "Accept": "application/json"}
    for h in extra_headers:
        if not h:
            continue
        name, _, val = h.partition(":")
        if name.strip():
            headers[name.strip()] = val.strip()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as e:
        die(f"接口请求失败 {url}: {e}")
    return {}


def check_api(j: dict, what: str) -> dict:
    if j.get("code") != 0:
        hint = ""
        code = j.get("code")
        if code in (-404, -403, -10403, 62002, 62004, 87007):
            hint = "（若你本人已登录且有权访问，可把浏览器 Cookie 加进 job 的 extra_headers）"
        die(f"{what} 返回 {code}: {j.get('message')}{hint}")
    return j.get("data") or {}


def quality_to_qn(quality: str) -> int:
    q = (quality or "").strip().lower().replace(" ", "")
    if q in ("", "highest", "best"):
        return 0
    if q in QN_LABELS:
        return QN_LABELS[q]
    if q.isdigit():
        n = int(q)
        if n < 100:            # 直接给的 qn（16/32/64/80...）
            return n
        if n in QN_TEXT:       # 112 等 qn
            return n
        if n in RES_TO_QN:     # 1080/720/480/360/2160 当分辨率
            return RES_TO_QN[n]
    m = re.fullmatch(r"(\d{3,4})p(\+?)", q)
    if m:
        key = m.group(1) + "p" + m.group(2)
        if key in QN_LABELS:
            return QN_LABELS[key]
    die(f"无法识别画质 {quality!r}；可用：highest / 1080p+ / 1080p / 720p / 480p / 360p 或 qn 数字")
    return 0


def pick_streams(dash: dict, qn: int, codec: str) -> tuple[dict, dict, int]:
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    if not videos:
        die("playurl 没有 dash.video（内容可能需要登录/大会员）")
    if not audios:
        die("playview 没有 dash.audio")
    available = sorted({int(v["id"]) for v in videos}, reverse=True)
    if qn == 0:
        qn = available[0]
    if qn not in available:
        labels = "/".join(QN_TEXT.get(i, str(i)) for i in available)
        die(f"画质 {qn} 不可用；本集提供：{labels}")
    prefix = CODEC_PREFIX.get(codec.strip().lower())
    if prefix is None:
        die(f"未知编码 {codec!r}；可用：avc(H.264) / hev(HEVC) / av1")
    same_qn = [v for v in videos if int(v["id"]) == qn]
    chosen = [v for v in same_qn if str(v.get("codecs", "")).startswith(prefix)]
    if not chosen:
        other = (same_qn[0].get("codecs", "?").split(".")[0]) if same_qn else "?"
        print(f"[警告] 画质 {qn} 没有 {codec} 编码，改用 {other}")
        chosen = same_qn
    video = sorted(chosen, key=lambda v: int(v.get("bandwidth") or 0))[-1]
    # 取码率最高的音轨（30280/192k 优于 30232/100k、30216/46k HE-AAC）
    audio = sorted(audios, key=lambda a: int(a.get("bandwidth") or 0))[-1]
    return video, audio, qn


def resolve(args: argparse.Namespace, extra: list[str]) -> tuple[dict, dict, float, int]:
    """按 job 字段选接口拿 dash 结构，返回 (video, audio, 时长秒, 实际qn)。"""
    qn = quality_to_qn(args.quality)
    if args.ep_id:
        url = (f"{API}/ogv/player/playview?biz_type=pugv&ep_id={args.ep_id}"
               f"&qn={qn}&fnver=0&fnval=4048&app_id=100")
        data = check_api(api_get(url, extra, args.page), "playview")
        vi = data.get("video_info") or data
        dash = vi.get("dash") or {}
        if not dash:
            die("playview 没有 dash（若你本人已登录且有权访问，可把浏览器 Cookie 加进 job 的 extra_headers）")
        duration = float(dash.get("duration") or 0) or float(vi.get("timelength") or 0) / 1000.0
    elif args.bvid or args.aid:
        vid_param = f"bvid={args.bvid}" if args.bvid else f"aid={args.aid}"
        data = check_api(api_get(f"{API}/x/web-interface/view?{vid_param}", extra, args.page), "view")
        cid, aid = data.get("cid"), data.get("aid")
        if not cid or not aid:
            die("view 接口没有返回 aid/cid")
        url = (f"{API}/x/player/playurl?avid={aid}&cid={cid}&qn={qn}"
               f"&fnver=0&fnval=4048&fourk=1")
        data = check_api(api_get(url, extra, args.page), "playurl")
        dash = data.get("dash") or {}
        if not dash:
            die("playurl 没有 dash（内容可能需要登录/大会员）")
        duration = float(dash.get("duration") or 0) or float(data.get("timelength") or 0) / 1000.0
    else:
        die("dash 任务需要 --ep-id（课程 cheese）或 --bvid/--aid（普通视频）")
        return {}, {}, 0.0, 0
    video, audio, qn_used = pick_streams(dash, qn, args.codec)
    return video, audio, duration, qn_used


def probe_total(url: str, page: str, extra: list[str], label: str) -> int | None:
    """Range 0-0 探测：顺带验证链接可用 + 拿总字节数（给进度条和完整性校验）。"""
    cmd = ["curl.exe", "-s", "-L", "--max-time", "25", "-r", "0-0", "-D", "-", "-o", "NUL",
           url, "-H", f"Referer: {page}", "-H", f"User-Agent: {UA}"]
    for h in extra:
        if h:
            cmd += ["-H", h]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        die(f"{label} 链接探测超时，链接可能过期：重新生成 job 再试")
    if r.returncode != 0:
        die(f"{label} 链接探测失败（curl {r.returncode}），链接可能过期：重新生成 job 再试")
    head = r.stdout or ""
    codes = re.findall(r"HTTP/\S+\s+(\d+)", head)
    final = codes[-1] if codes else "?"
    if final not in ("200", "206"):
        die(f"{label} 返回 {final}，链接可能过期或被拒：重新生成 job 再试")
    m = re.search(r"Content-Range:\s*bytes\s+\d+-\d+/(\d+)", head, re.I)
    return int(m.group(1)) if m else None


def short_url(url: str) -> str:
    """去掉签名查询串，只留 域名+路径 供打印。"""
    return re.sub(r"\?.*$", "", url)


def is_m4s(path: Path) -> bool:
    """fMP4 首盒应为 ftyp/styp；抓回 HTML 错误页等坏文件在这里拦下。"""
    try:
        with path.open("rb") as f:
            head = f.read(12)
        return len(head) >= 8 and head[4:8] in (b"ftyp", b"styp")
    except OSError:
        return False


def run(args: argparse.Namespace) -> None:
    preflight("dash", args.aria2, args.ffmpeg, args.ffprobe)
    out_file = Path(args.out).resolve()
    workdir = (Path(args.workdir).resolve() if args.workdir
               else out_file.with_suffix("").parent / (out_file.stem + "_ts"))
    extra = list(args.extra_header or [])
    out_file.parent.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    total_steps = 4
    print(f"影片：{out_file.stem}")

    # [1/4] 解析直链
    video, audio, duration, qn_used = resolve(args, extra)
    v_url = video.get("base_url") or video.get("baseUrl")
    a_url = audio.get("base_url") or audio.get("baseUrl")
    if not v_url or not a_url:
        die("playurl 响应缺少 base_url")
    streams = [("video.m4s", v_url), ("audio.m4s", a_url)]

    # 本地完成状态（断点续传判断）；只对需要下载的流做网络探测
    totals: dict[str, int] = {}
    state: dict[str, bool] = {}
    for name, _ in streams:
        target = workdir / name
        ctrl = Path(str(target) + ".aria2")
        local_ok = (target.exists() and not ctrl.exists()
                    and target.stat().st_size > 0 and is_m4s(target))
        state[name] = local_ok
    for name, url in streams:
        if state[name]:
            totals[name] = (workdir / name).stat().st_size
            continue
        total = probe_total(url, args.page, extra, name)
        if total is None:
            print(f"      [警告] {name} 未返回总长度，进度只显示已下载字节")
        else:
            totals[name] = total

    v_disp = short_url(v_url)
    a_disp = short_url(a_url)
    q_text = QN_TEXT.get(qn_used, str(qn_used))
    codec_text = str(args.codec)
    def mb(n: int | None) -> str:
        return f"{n / (1024 * 1024):.1f}" if n else "?"
    print(f"[1/{total_steps}] 解析直链：{'课程 ep' + str(args.ep_id) if args.ep_id else (args.bvid or 'av' + str(args.aid))}"
          f"（{q_text} / {codec_text}）")
    print(f"      视频 {v_disp}")
    print(f"            {mb(totals.get('video.m4s'))} MB")
    print(f"      音频 {a_disp}")
    print(f"            {mb(totals.get('audio.m4s'))} MB")
    if duration:
        print(f"      时长 {fmt_hms(duration)}")

    # [2/4] 下载双流（aria2 断点续传）
    input_file = workdir / "aria2.input.txt"

    def build_input() -> tuple[int, int]:
        lines_out: list[str] = []
        needed_local = 0
        skipped_local = 0
        for name, url in streams:
            target = workdir / name
            ctrl = Path(str(target) + ".aria2")
            done = (target.exists() and not ctrl.exists() and target.stat().st_size > 0
                    and is_m4s(target)
                    and (name not in totals or target.stat().st_size == totals[name]))
            if done:
                skipped_local += 1
                continue
            if target.exists() and not ctrl.exists():
                # 没有控制文件却大小/魔数不对 -> 残留坏文件，删掉重下（aria2 不接受覆盖）
                target.unlink(missing_ok=True)
            lines_out.append(url)
            lines_out.append(f"  out={name}")
            lines_out.append(f"  header=Referer: {args.page}")
            lines_out.append(f"  header=User-Agent: {UA}")
            for h in extra:
                if h:
                    lines_out.append(f"  header={h}")
            needed_local += 1
        input_file.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
        return needed_local, skipped_local

    def remaining() -> list[str]:
        bad = []
        for name, _ in streams:
            target = workdir / name
            ctrl = Path(str(target) + ".aria2")
            if ctrl.exists() or not target.exists() or target.stat().st_size <= 0:
                bad.append(name)
                continue
            sz = target.stat().st_size
            if name in totals and sz != totals[name]:
                bad.append(name)
            elif not is_m4s(target):
                bad.append(name)
        return bad

    def run_aria2(needed_now: int, attempt: int) -> int:
        aria_log = workdir / "aria2.log"
        aria_err = workdir / "aria2.log.err"
        aria2 = shutil.which(args.aria2) or args.aria2
        aria_cmd = [
            aria2,
            "-j2",
            f"-x{args.parallel}",
            f"-s{args.parallel}",
            "--min-split-size=4M",
            "--max-tries=5",
            "--retry-wait=2",
            "--connect-timeout=15",
            "--timeout=60",
            "--continue=true",
            "--file-allocation=none",
            "--auto-file-renaming=false",
            "-d", str(workdir),
            "-i", str(input_file),
        ]
        mode = "w" if attempt == 1 else "a"
        with aria_log.open(mode, encoding="utf-8", errors="replace") as out_log, \
                aria_err.open(mode, encoding="utf-8", errors="replace") as err_log:
            p = subprocess.Popen(aria_cmd, stdout=out_log, stderr=err_log)
            t0 = time.time()
            last_bytes, last_t = 0, t0
            rate = 0.0
            last_log_t = t0
            expected = sum(totals.values()) if totals else 0
            while p.poll() is None:
                time.sleep(0.6)
                got = {name: ((workdir / name).stat().st_size
                              if (workdir / name).exists() else 0)
                       for name, _ in streams}
                cur_bytes = sum(got.values())
                now = time.time()
                dt = now - last_t
                if dt >= 1.5:
                    inst = max(0.0, (cur_bytes - last_bytes) / dt)
                    rate = inst if rate <= 0 else rate * 0.7 + inst * 0.3
                    last_bytes, last_t = cur_bytes, now
                pct = int(100 * cur_bytes / expected) if expected else 0
                eta = (fmt_hms((expected - cur_bytes) / rate)
                       if rate > 0 and expected and cur_bytes < expected else "--:--")
                v_sz, a_sz = got.get("video.m4s", 0), got.get("audio.m4s", 0)
                v_tot = totals.get("video.m4s")
                a_tot = totals.get("audio.m4s")
                bar = format_bar(pct)
                print(f"\r  [{bar}] {pct:3d}%  视频 {mb(v_sz)}/{mb(v_tot)} MB"
                      f"  音频 {mb(a_sz)}/{mb(a_tot)} MB  {rate / (1024 * 1024):5.1f} MB/秒  剩余 {eta}   ",
                      end="", flush=True)
                if now - last_log_t >= 5:
                    # 先清掉当前进度条行（以 \r 开头的写入不会进日志），再落一条可回读的进度
                    sys.stdout.write("\r" + " " * 100 + "\r")
                    sys.stdout.write(f"      [进度] 视频 {mb(v_sz)}/{mb(v_tot)} MB"
                                     f" 音频 {mb(a_sz)}/{mb(a_tot)} MB"
                                     f" {pct}% 剩余 {eta}\n")
                    sys.stdout.flush()
                    last_log_t = now
        print()
        return p.returncode

    needed, skipped = build_input()
    if needed > 0:
        print(f"[2/{total_steps}] 下载双流：需下载 {needed}，已跳过 {skipped}"
              f"，每文件并发 {args.parallel}")
        rc = run_aria2(needed, 1)
        bad = remaining()
        if bad:
            print(f"      未完成：{'、'.join(bad)}，自动补跑一轮 ...")
            need2, _ = build_input()
            if need2 > 0:
                run_aria2(need2, 2)
                bad = remaining()
        if bad:
            print(f"[错误] 下载不完整（{'、'.join(bad)}，aria2 exit {rc}）。aria2.log 尾部：")
            try:
                tail = (workdir / "aria2.log").read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                for line in tail:
                    print(f"  {line}")
            except OSError:
                pass
            die("下载不完整，重新运行同一命令即可断点续传（若一直失败，链接已过期：重新生成 job）")
        if rc not in (0, None):
            print("      （aria2 退出码非 0，但双流已补齐，继续合并）")
    else:
        print(f"[2/{total_steps}] 下载双流：文件已齐（断点续传），跳过")

    print(f"[3/{total_steps}] 合并视频+音频（无损流复制，不重新编码）...")
    ffmpeg = shutil.which(args.ffmpeg) or args.ffmpeg
    r = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(workdir / "video.m4s"),
         "-i", str(workdir / "audio.m4s"), "-c", "copy",
         "-movflags", "+faststart", str(out_file)]
    )
    if r.returncode != 0:
        die(f"ffmpeg 合并失败（exit {r.returncode}）")
    print(f"      合并完成：{out_file}")

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
        print("      [警告] 输出没有音频流（静音或音频轨道合并失败）")
    print(f"[完成] {out_file}")
    dur_note = f"，时长 {fmt_hms(out_dur)}（播放列表 {fmt_hms(duration)}）" if duration else f"，时长 {fmt_hms(out_dur)}"
    print(f"       大小 {size_mb:,.0f} MB{dur_note}，{track_note}")

    if args.cleanup:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[清理] 已删除临时目录 {workdir}")


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Download bilibili DASH to mp4")
    ap.add_argument("--ep-id", default="", help="课程(cheese) 分集 id，如 720220")
    ap.add_argument("--bvid", default="", help="普通视频 BV 号")
    ap.add_argument("--aid", default="", help="普通视频 av 号")
    ap.add_argument("--page", required=True, help="播放页 URL（Referer）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quality", default="highest",
                    help="highest / 1080p+ / 1080p / 720p / 480p / 360p 或 qn 数字")
    ap.add_argument("--codec", default="avc", help="avc(H.264，默认) / hev(HEVC) / av1")
    ap.add_argument("--parallel", type=int, default=12, help="每文件 aria2 最大连接数")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--aria2", default="aria2c")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--extra-header", action="append", default=[],
                    help="可重复；仅限本人已登录且有权访问的内容，如 'Cookie: SESSDATA=...'")
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
