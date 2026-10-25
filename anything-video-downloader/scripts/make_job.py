#!/usr/bin/env python3
"""Build a UTF-8 job JSON for start_hls_windows.py from a video title.

The piece that used to be hand-written every time: turn a page title into a
clean mp4 filename (strip the site suffix, drop Windows-illegal chars, fall
back to the video id), then write the job JSON and print the launch command.

Pass the site's title suffix with --strip (read it from references/sites/<domain>.md),
e.g. --strip " - 抖音" or --strip "_哔哩哔哩_bilibili" or --strip " - HD国语 在线播放 |".
If the suffix itself starts with "--", use the equals form: --strip="--播放器".

Example:
  python make_job.py --page "https://example.com/play/123" --m3u8 "https://cdn.example.com/master.m3u8" --title "示例视频 - 在线播放" --strip " - 在线播放" --root "<下载目录>" --episode 3 --video-id 123

Prints the cleaned name, the out path, the job path, and the ready-to-run
start_hls_windows.py command (absolute --args-json path).

B站（DASH，无 m3u8）用 --ep-id（课程 cheese）或 --bvid/--aid（普通视频）代替 --m3u8，
生成 engine=dash 的 job；直链由 bilibili_download.py 运行时现取（约 2h 过期，不进 job）：

  python make_job.py --page "https://www.bilibili.com/cheese/play/ep720220" --ep-id 720220 --quality "1080p+" --codec avc --title "土豆逗严肃科普：消失吧，拖延症！_哔哩哔哩_bilibili" --strip "_哔哩哔哩_bilibili" --root "<下载目录>" --episode 1

直链站点（如抖音，播放器给 mp4 而非 m3u8）用 --url 代替 --m3u8，生成 engine=direct 的 job。
签名直链无法用 API 重取，只能烤进 job，需尽快下载（详见 references/sites/douyin.md）：

  python make_job.py --page "https://www.douyin.com/video/7235504140267441447" --url "https://v11-weba.douyinvod.com/....mp4?...签名..." --title "示例视频 - 抖音" --strip " - 抖音" --root "<下载目录>"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows-illegal filename chars plus control chars.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Windows reserved device names (case-insensitive), with or without extension.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def clean_name(raw: str) -> str:
    """Strip illegal chars and trailing dots/spaces; collapse nothing else."""
    name = _ILLEGAL.sub("", raw)
    # Windows: names cannot end in a dot or space.
    name = name.strip().rstrip(". ")
    return name.strip()


def build_filename(title: str, strip: str, video_id: str, episode: int) -> str:
    name = title
    if strip:
        # Cut from the first occurrence of the suffix to the end.
        idx = name.find(strip)
        if idx != -1:
            name = name[:idx]
    name = clean_name(name)
    if not name:
        name = "video_" + clean_name(video_id) if video_id else "video"
    # Reserved device names (CON, NUL, COM1, ...) break on Windows; prefix an underscore.
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    if episode > 0:
        return f"{name}_第{episode:02d}集.mp4"
    return name + ".mp4"


def make_job_dict(common: dict, page: str, m3u8: str, title: str, video_id: str,
                  episode: int, name: str, out_dir: Path, log,
                  dash_fields: dict | None = None, direct_url: str = "") -> tuple[dict, Path]:
    if name:
        fname = (f"{name}_第{episode:02d}集" if episode > 0 else name) + ".mp4"
    else:
        fname = build_filename(title, common["strip"], video_id, episode)
    out = out_dir / fname
    job: dict = {}
    if dash_fields:
        # DASH 引擎（B站）：不带直链，bilibili_download.py 运行时现取（直链 ~2h 过期）
        job["engine"] = "dash"
        job.update(dash_fields)
    elif direct_url:
        # 直链引擎（如抖音）：签名直链无法用 API 重取，只能烤进 job，需尽快下载
        job["engine"] = "direct"
        job["url"] = direct_url
    else:
        job["m3u8"] = m3u8
    job.update({
        "page": page,
        "out": str(out),
        "parallel": common["parallel"],
        "cleanup": common["cleanup"],
        "quality": common["quality"],
        "max_segments": common["max_segments"],
        "extra_headers": list(common["extra_headers"] or []),
    })
    if log is not None:
        job["log"] = log
    return job, out


def dash_fields_from(ep_id, bvid, aid, codec) -> dict:
    """dash 标识（--ep-id/--bvid/--aid）-> job 字段；一个都没有则返回 {}（非 dash 任务）。"""
    fields: dict = {}
    if ep_id:
        fields["ep_id"] = int(ep_id) if str(ep_id).isdigit() else ep_id
    if bvid:
        fields["bvid"] = str(bvid)
    if aid:
        fields["aid"] = int(aid) if str(aid).isdigit() else aid
    if not fields:
        return {}
    fields["codec"] = codec or "avc"
    return fields


def write_job(job: dict, job_path: Path) -> None:
    job_path.parent.mkdir(parents=True, exist_ok=True)
    # Write UTF-8 no-BOM so start_hls_windows.py reads it back identically.
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def launch_command(job_path: Path) -> str:
    starter = Path(__file__).resolve().parent / "start_hls_windows.py"
    return f'python "{starter}" --args-json "{job_path}"'


def run_batch(args, common: dict, root: Path) -> int:
    """Build one job JSON per entry in --batch, sharing the command-line defaults.

    --batch points at a JSON file: an array of entries, each with
    {title, m3u8, page, episode?, video_id?}. Per-entry values override the
    top-level --page/--m3u8/--title when present. --root/--strip/--quality/
    --parallel/--cleanup/--max-segments come from the command line.
    """
    batch_path = Path(args.batch).resolve()
    if not batch_path.is_file():
        print(f"[错误] 找不到 --batch 文件: {batch_path}", file=sys.stderr)
        return 1
    entries = json.loads(batch_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        print("[错误] --batch 文件应为非空数组", file=sys.stderr)
        return 1
    job_dir = root / "_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, e in enumerate(entries, 1):
        title = e.get("title", "")
        m3u8 = e.get("m3u8", args.m3u8 or "")
        url = e.get("url", args.url or "")
        page = e.get("page", args.page or "")
        video_id = e.get("video_id", "")
        episode = int(e.get("episode", i))
        name = e.get("name", "")
        dash = dash_fields_from(e.get("ep_id", args.ep_id or ""), e.get("bvid", args.bvid or ""),
                                e.get("aid", args.aid or ""), e.get("codec", args.codec))
        if sum(bool(x) for x in (m3u8, dash, url)) > 1:
            print(f"[跳过] 第 {i} 项同时给了 m3u8 / url / ep_id(bvid,aid)，三选一", file=sys.stderr)
            continue
        if not (title or name) or not page or not (m3u8 or dash or url):
            print(f"[跳过] 第 {i} 项缺 title/name、page 或媒体标识（m3u8 / url / ep_id-bvid-aid）", file=sys.stderr)
            continue
        job, out = make_job_dict(common, page, m3u8, title, video_id, episode, name, root, args.log,
                                 dash_fields=dash or None, direct_url=url)
        job_path = job_dir / f"_job_{episode:02d}.json"
        write_job(job, job_path)
        print(f"[{episode:02d}] {out.name}")
        print(f'     {launch_command(job_path)}')
        ok += 1
    print(f"\n共生成 {ok} 个 job，位于 {job_dir}")
    print("多集并行：同站点建议 ≤3 集同时，逐个执行上面的启动命令即可。")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a job JSON for start_hls_windows.py")
    ap.add_argument("--page", default="", help="播放页 URL（作为 Referer）")
    ap.add_argument("--m3u8", default="", help="master 或 media m3u8 地址（HLS 站点）")
    ap.add_argument("--url", default="", help="直链媒体地址，如抖音 mp4（生成 direct job）")
    ap.add_argument("--ep-id", default="", help="B站课程(cheese)分集 id，如 720220（生成 dash job）")
    ap.add_argument("--bvid", default="", help="B站普通视频 BV 号（生成 dash job）")
    ap.add_argument("--aid", default="", help="B站普通视频 av 号（生成 dash job）")
    ap.add_argument("--codec", default="avc", help="dash 编码：avc(H.264，默认) / hev(HEVC) / av1")
    ap.add_argument("--title", default="", help="播放页 document.title（或站点文档指定的名称字段）")
    ap.add_argument("--strip", default="", help='站点标题尾缀，如 " - 抖音" / "_哔哩哔哩_bilibili"；留空则不剥离。'
                                                '尾缀以 -- 开头时用等号形式：--strip="--播放器"')
    ap.add_argument("--root", required=True, help="下载输出目录（建议绝对路径，如 <下载目录>）")
    ap.add_argument("--video-id", default="", help="清洗后为空时的回退 ID")
    ap.add_argument("--episode", type=int, default=0, help="集数（>0 时输出 _第NN集）")
    ap.add_argument("--quality", default="highest", help="highest 或 1080p/720 等（用户已选画质时填）")
    ap.add_argument("--parallel", type=int, default=12)
    ap.add_argument("--cleanup", dest="cleanup", action="store_true", default=True)
    ap.add_argument("--no-cleanup", dest="cleanup", action="store_false")
    ap.add_argument("--max-segments", type=int, default=0, help="冒烟测试填 3")
    ap.add_argument("--extra-header", action="append", default=[], help="可重复，如 'x-cdn-auth: ...'")
    ap.add_argument("--name", default="", help="直接指定输出基名（覆盖 title 命名），不带扩展名")
    ap.add_argument("--job", default="", help="job JSON 输出路径；默认 <root>/_job.json")
    ap.add_argument("--log", default=None, help="日志路径；默认 <out>.log，传空串关闭")
    ap.add_argument("--batch", default="", help="多集批量：JSON 文件（数组），每项 {title,m3u8|url|ep_id,page,episode?,video_id?}")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    common = {
        "strip": args.strip,
        "parallel": args.parallel,
        "cleanup": args.cleanup,
        "quality": args.quality,
        "max_segments": args.max_segments,
        "extra_headers": args.extra_header,
    }

    if args.batch:
        return run_batch(args, common, root)

    dash = dash_fields_from(args.ep_id, args.bvid, args.aid, args.codec)
    sources = sum(bool(x) for x in (args.m3u8, dash, args.url))
    if sources > 1:
        print("[错误] --m3u8 / --url / --ep-id(--bvid,--aid) 三选一"
              "（HLS / 直链 / B站 DASH）", file=sys.stderr)
        return 1
    if not (args.title or args.name) or not args.page or not sources:
        print("[错误] 单集模式需要 --title（或 --name）、--page，以及 --m3u8（HLS）"
              "或 --url（直链，如抖音）或 --ep-id/--bvid/--aid（B站 DASH）", file=sys.stderr)
        return 1

    job, out = make_job_dict(common, args.page, args.m3u8, args.title, args.video_id,
                             args.episode, args.name, root, args.log,
                             dash_fields=dash or None, direct_url=args.url)
    job_path = Path(args.job).resolve() if args.job else root / "_job.json"
    write_job(job, job_path)

    engine = "dash（B站）" if dash else ("direct（直链）" if args.url else "hls")
    print("引擎 =", engine)
    print("名称 =", out.stem)
    print("out  =", out)
    print("job  =", job_path)
    print()
    print("启动命令（用绝对路径，避免相对路径踩坑）：")
    print("  " + launch_command(job_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
