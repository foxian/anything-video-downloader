#!/usr/bin/env python3
"""依赖预检：确认 aria2c / ffmpeg / ffprobe / curl（以及按需的 cryptography）。

用法（技能根目录下）：
  python scripts/check_deps.py                     # 三个引擎的并集
  python scripts/check_deps.py --engine direct     # 只看直链引擎
  python scripts/check_deps.py --cryptography      # 额外要求 AES-128 所需库
  python scripts/check_deps.py --force             # 忽略已通过的标记，强制重查
  python scripts/check_deps.py --forget            # 删除已通过的标记后退出

退出码：0 全部必需项就绪；2 有缺失。
只检测、只打印安装命令，不会自动安装任何东西。

检查通过后会在技能根目录写 `.deps-ok.json`；启动器与下载脚本据此静默跳过
重复预检。某次运行中因缺少工具失败时，下载脚本会调用 recover() 清标记并
做一次全引擎检查。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 各引擎的必需工具（按顺序输出）。
ENGINE_REQUIRED = {
    "hls": ["aria2c", "ffmpeg", "ffprobe", "curl"],
    "dash": ["aria2c", "ffmpeg", "ffprobe", "curl"],
    "direct": ["aria2c", "ffprobe", "curl"],
}
ALL_TOOLS = ["aria2c", "ffmpeg", "ffprobe", "curl"]

# 工具名 -> 命令行参数名（--aria2 / --ffmpeg / --ffprobe / --curl 的键）。
OVERRIDE_KEY = {"aria2c": "aria2", "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "curl": "curl"}

# 本技能脚本里硬编码的是 curl.exe；其他平台用 curl。
CURL_BIN = "curl.exe" if sys.platform == "win32" else "curl"

# 检查通过标记：技能根目录（SKILL.md 同目录）。只记录实查通过的工具。
MARKER_VERSION = 1
MARKER_PATH = Path(__file__).resolve().parent.parent / ".deps-ok.json"

# 脚本参数默认值；用户传入其他路径时不使用标记（必须实查）。
DEFAULT_VALUE = {"aria2": "aria2c", "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "curl": CURL_BIN}

PURPOSE = {
    "aria2c": "并发分片下载 + 断点续传",
    "ffmpeg": "无损合并音视频",
    "ffprobe": "成品时长/流校验",
    "curl": "拉取播放列表 / KEY / 探测直链",
}


def display_name(tool: str) -> str:
    return CURL_BIN if tool == "curl" else tool


def find_tool(name: str) -> str | None:
    """返回可执行文件路径；name 自带路径（用户传了绝对路径）时按存在性判断。"""
    looks_like_path = "/" in name or "\\" in name or (sys.platform == "win32" and ":" in name)
    if looks_like_path:
        p = Path(name)
        return str(p) if p.is_file() else None
    return shutil.which(name)


def version_of(tool: str, path: str) -> str:
    """取版本首行；跑不起来时返回空串（工具仍算可用）。"""
    flag = "--version" if tool in ("aria2c", "curl") else "-version"
    try:
        r = subprocess.run([path, flag], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (r.stdout or r.stderr or "").strip().splitlines()
    return lines[0].strip()[:80] if lines else ""


def has_cryptography() -> bool:
    return importlib.util.find_spec("cryptography") is not None


def missing_for(engine: str, **overrides) -> list[str]:
    """返回该引擎缺失的必需工具名。

    overrides 支持 aria2= / ffmpeg= / ffprobe= / curl=，值是命令行实际传入的名称
    或绝对路径（尊重脚本的 --aria2/--ffmpeg/--ffprobe 参数）。
    """
    required = ENGINE_REQUIRED.get(engine, ALL_TOOLS)
    missing: list[str] = []
    for tool in required:
        value = overrides.get(OVERRIDE_KEY.get(tool, tool)) or display_name(tool)
        if find_tool(value) is None:
            missing.append(tool)
    return missing


def required_tools(engine: str | None) -> list[str]:
    """该引擎的必需工具；engine 为空时返回三个引擎的并集。"""
    engines = [engine] if engine else list(ENGINE_REQUIRED)
    required: list[str] = []
    for item in engines:
        for tool in ENGINE_REQUIRED[item]:
            if tool not in required:
                required.append(tool)
    return required


def load_marker() -> dict:
    """读取标记；不存在、损坏或版本不符时返回空 dict。"""
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != MARKER_VERSION:
        return {}
    if not isinstance(data.get("verified"), dict):
        return {}
    return data


def save_marker(verified: dict) -> None:
    """原子写入标记；写失败只警告，不影响下载。"""
    now = datetime.now().isoformat(timespec="seconds")
    payload = {"version": MARKER_VERSION, "checked_at": now, "verified": dict(verified)}
    tmp = MARKER_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(MARKER_PATH)
    except OSError as e:
        print(f"[警告] 无法写入依赖标记 {MARKER_PATH}：{e}", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def forget_marker() -> bool:
    """删除标记，返回是否真的删掉了。"""
    try:
        MARKER_PATH.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def marker_covers(engine: str | None, **overrides) -> bool:
    """标记是否已覆盖该引擎的必需工具；自定义工具路径一律视为未覆盖。"""
    for key, value in overrides.items():
        if value and value != DEFAULT_VALUE.get(key):
            return False
    verified = load_marker().get("verified") or {}
    return all(tool in verified for tool in required_tools(engine))


def ensure(engine: str | None = None, exit_code: int = 1, **overrides) -> None:
    """标记覆盖则静默返回；否则实查，缺失时报错退出，齐全时写标记。"""
    if marker_covers(engine, **overrides):
        return
    missing = missing_for(engine or "", **overrides)
    if missing:
        forget_marker()
        names = "、".join(display_name(t) for t in missing)
        lines = [f"[错误] 缺少必需依赖：{names}", ""]
        lines += hint_lines(missing)
        lines += ["", "安装与浏览器工具配置详见 references/setup.md"]
        lines.append(f"复核：python \"{Path(__file__).resolve()}\""
                     + (f" --engine {engine}" if engine else "") + " --force")
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(exit_code)
    verified = load_marker().get("verified") or {}
    now = datetime.now().isoformat(timespec="seconds")
    for tool in required_tools(engine):
        verified[tool] = now
    save_marker(verified)


def recover(engine: str | None = None) -> None:
    """运行中缺工具时调用：清标记，做一次全引擎完整检查。"""
    forget_marker()
    where = f"（引擎 {engine}）" if engine else ""
    print(f"[依赖] 运行中缺少外部工具{where}，重新做一次完整依赖检查。", file=sys.stderr)
    ensure(None)
    print("[依赖] 检查通过，可以重试。", file=sys.stderr)


def hint_lines(missing: list[str], crypto: bool = False) -> list[str]:
    """按平台给出安装命令（只打印，不执行）。"""
    lines: list[str] = []
    need_ffmpeg = any(t in ("ffmpeg", "ffprobe") for t in missing)
    need_aria2 = "aria2c" in missing
    need_curl = "curl" in missing
    pkgs = [p for p, need in (("ffmpeg", need_ffmpeg), ("aria2", need_aria2)) if need]

    if pkgs:
        if sys.platform == "win32":
            lines.append("安装命令（Windows，任选一种包管理器）：")
            if need_ffmpeg:
                lines.append("  winget install --id Gyan.FFmpeg -e        # ffmpeg / ffprobe")
            if need_aria2:
                lines.append("  winget install --id aria2.aria2 -e       # aria2c")
            names = " ".join(pkgs)
            lines.append(f"  备选：choco install {names} -y   或   scoop install {names}")
        elif sys.platform == "darwin":
            lines.append(f"安装命令（macOS）：brew install {' '.join(pkgs)}")
        else:
            lines.append(f"安装命令（Linux）：sudo apt install {' '.join(pkgs)}")
            lines.append(f"  备选：sudo dnf install {' '.join(pkgs)}   或   sudo pacman -S {' '.join(pkgs)}")
        lines.append("装完请重开终端（刷新 PATH），再运行本命令复核。")

    if need_curl:
        if sys.platform == "win32":
            lines.append("curl.exe 为 Windows 10/11 自带；若缺失，从 https://curl.se/windows/ "
                         "下载后把所在目录加入 PATH。")
        else:
            lines.append("安装 curl：sudo apt install curl（或 dnf/pacman 对应写法）。")

    if crypto:
        lines.append("安装 cryptography：python -m pip install cryptography")
        lines.append("  只有 AES-128 加密的 HLS 源需要；未安装时明文源仍可正常下载。")

    if lines:
        lines.append("")
        lines.append("安装会改动系统，请先确认再执行（Agent 应先征得用户同意）。")
    return lines


def check_required(tools: list[str], overrides: dict | None = None) -> list[str]:
    """按给定工具清单逐个解析，打印状态并返回缺失项。"""
    overrides = overrides or {}
    missing: list[str] = []
    for tool in tools:
        value = overrides.get(OVERRIDE_KEY.get(tool, tool)) or display_name(tool)
        path = find_tool(value)
        name = display_name(tool)
        if path is None:
            missing.append(tool)
            print(f"[缺失] {name:<8} —— {PURPOSE.get(tool, '')}")
            continue
        ver = version_of(tool, path)
        if ver:
            print(f"[OK]   {name:<8} {ver}")
        else:
            print(f"[OK]   {name:<8} 已找到（{path}），版本未知")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="检查 anything-video-downloader 的依赖")
    ap.add_argument("--engine", choices=sorted(ENGINE_REQUIRED), default="",
                    help="只检查该引擎的必需项；省略时检查三个引擎的并集")
    ap.add_argument("--cryptography", action="store_true",
                    help="把 cryptography 也算入必需项（AES-128 解密用）")
    ap.add_argument("--force", action="store_true", help="忽略已通过的标记，强制重查")
    ap.add_argument("--forget", action="store_true", help="删除已通过的标记后退出（不检查）")
    args = ap.parse_args()

    if args.forget:
        removed = forget_marker()
        print(f"[标记] {'已删除' if removed else '不存在'}：{MARKER_PATH}")
        return 0

    required = required_tools(args.engine or None)

    if not args.force:
        marker = load_marker()
        verified = marker.get("verified") or {}
        covered = all(tool in verified for tool in required)
        if args.cryptography:
            covered = covered and "cryptography" in verified
        if covered:
            print(f"[跳过] 此前已检查通过（{marker.get('checked_at', '时间未知')}）；"
                  "如需重查加 --force。")
            return 0

    print(f"[检查] 引擎：{args.engine or '全部'}    平台：{platform.system()} {platform.release()}")
    print(f"[检查] Python {platform.python_version()}（{sys.executable}）")
    if sys.version_info < (3, 8):
        print("[错误] 需要 Python 3.8 及以上")
        return 2
    print()

    missing = check_required(required)

    crypto_missing = False
    crypto_required = bool(args.cryptography)
    if crypto_required or not args.engine:
        crypto_missing = not has_cryptography()
        if crypto_required:
            mark, note = ("[缺失]", "必需（--cryptography）") if crypto_missing else ("[OK]  ", "必需（--cryptography）")
        else:
            mark, note = ("[提示]", "按需：仅 AES-128 加密源需要") if crypto_missing else ("[OK]  ", "按需：仅 AES-128 加密源需要")
        print(f"{mark} cryptography {note}")

    if not missing and not (crypto_required and crypto_missing):
        verified = load_marker().get("verified") or {}
        now = datetime.now().isoformat(timespec="seconds")
        for tool in required:
            verified[tool] = now
        if not crypto_missing and (crypto_required or not args.engine):
            verified["cryptography"] = now
        save_marker(verified)
        print(f"\n[完成] 依赖齐全，可以开始下载。已记录到 {MARKER_PATH}")
        return 0

    forget_marker()
    print()
    for line in hint_lines(missing, crypto=crypto_missing):
        print(line)
    print(f"\n复核：python \"{Path(__file__).resolve()}\""
          + (f" --engine {args.engine}" if args.engine else "") + " --force")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
