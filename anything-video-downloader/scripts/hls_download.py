#!/usr/bin/env python3
"""HLS (m3u8) -> mp4: resolve playlist, aria2 fetch, AES-128 decrypt, ffmpeg merge."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


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


class Tee:
    """Write to the console and a log file at once, so an unattended run stays readable.

    Progress bars are emitted with a leading \\r so they overwrite in a terminal.
    A log file can't overwrite, so those chunks are dropped from the log to keep it
    readable; the console still receives everything.
    """

    def __init__(self, stream, log_file) -> None:
        self._stream = stream
        self._log = log_file

    def write(self, data: str) -> int:
        # Keep the log free of carriage-return progress churn.
        if data and not data.startswith("\r"):
            self._log.write(data)
            self._log.flush()
        return self._stream.write(data)

    def flush(self) -> None:
        self._stream.flush()
        self._log.flush()

    def reconfigure(self, **kwargs) -> None:
        try:
            self._stream.reconfigure(**kwargs)
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def open_log(path: str):
    """Open path for the Tee; warn and return None when it is not writable."""
    log_path = Path(path).resolve()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path.open("w", encoding="utf-8", errors="replace", buffering=1)
    except OSError as e:
        print(f"[警告] 无法写入日志 {log_path}：{e}", file=sys.stderr)
        return None


def die(msg: str, code: int = 1) -> None:
    print(f"[错误] {msg}", file=sys.stderr)
    raise SystemExit(code)


def preflight(engine: str, aria2: str = "aria2c", ffmpeg: str = "ffmpeg",
              ffprobe: str = "ffprobe") -> None:
    """确认必需的外部工具存在；已通过检查的引擎由标记跳过。

    engine 决定必需项：hls/dash 需要 aria2c + ffmpeg + ffprobe，direct 只需 aria2c + ffprobe。
    标记不存在或未覆盖时实查，缺失则给安装命令并退出；齐全时记录标记。
    """
    import check_deps

    check_deps.ensure(engine, exit_code=1, aria2=aria2, ffmpeg=ffmpeg, ffprobe=ffprobe)


def fmt_hms(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_bar(pct: int, length: int = 24) -> str:
    pct = max(0, min(100, pct))
    filled = length * pct // 100
    return "#" * filled + "." * (length - filled)


def curl_header_args(page: str, extra_headers: list[str]) -> list[str]:
    args = ["-H", f"Referer: {page}", "-H", f"User-Agent: {UA}"]
    for h in extra_headers:
        if h:
            args += ["-H", h]
    return args


def fetch_to(url: str, out_path: Path, page: str, extra_headers: list[str]) -> None:
    cmd = ["curl.exe", "-s", "-L", url, *curl_header_args(page, extra_headers), "-o", str(out_path)]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        die(f"curl failed (exit {r.returncode}) for {url}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        die(f"empty response for {url} (link may be expired or referer rejected)")


def fetch_text(url: str, workdir: Path, page: str, extra_headers: list[str]) -> str:
    tmp = workdir / "fetch.tmp"
    fetch_to(url, tmp, page, extra_headers)
    text = tmp.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        die(f"empty response for {url} (link may be expired or referer rejected)")
    return text


def resolve_uri(uri: str, base_url: str) -> str:
    if re.match(r"^https?://", uri, re.I):
        return uri
    return base_url[: base_url.rfind("/") + 1] + uri


def hex_to_bytes(hex_s: str) -> bytes:
    hex_s = hex_s.strip()
    if len(hex_s) % 2:
        die(f"odd-length hex: {hex_s}")
    return bytes.fromhex(hex_s)


def sequence_iv(seq: int) -> bytes:
    n = seq & ((1 << 64) - 1)
    iv = bytearray(16)
    for i in range(15, 7, -1):
        iv[i] = n & 0xFF
        n >>= 8
    return bytes(iv)


def parse_ext_x_key(line: str) -> dict:
    rest = re.sub(r"^#EXT-X-KEY:", "", line)
    method = None
    uri = None
    iv = None
    m = re.search(r"METHOD=([^,]+)", rest)
    if m:
        method = m.group(1).strip().upper()
    m = re.search(r'URI="([^"]+)"', rest)
    if m:
        uri = m.group(1)
    else:
        m = re.search(r"URI=([^,]+)", rest)
        if m:
            uri = m.group(1).strip().strip('"')
    m = re.search(r"IV=0x([0-9A-Fa-f]+)", rest)
    if m:
        hx = m.group(1)
        if len(hx) != 32:
            die(f"EXT-X-KEY IV must be 16 bytes (32 hex digits), got {len(hx)}")
        iv = hex_to_bytes(hx)
    return {"method": method, "uri": uri, "iv": iv}


def aes128_key_bytes(raw: bytes, url: str) -> bytes:
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        try:
            s = raw.decode("ascii")
        except UnicodeDecodeError:
            s = ""
        if re.fullmatch(r"[0-9A-Fa-f]{32}", s):
            return hex_to_bytes(s)
    die(f"AES-128 key length {len(raw)} from {url} is invalid; need 16 binary bytes or 32 hex chars")
    return b""


def decrypt_segment(cipher: bytes, key: bytes, iv: bytes) -> bytes:
    if len(cipher) == 0 or len(cipher) % 16 != 0:
        raise ValueError(f"ciphertext length {len(cipher)} is not a multiple of 16")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except ImportError:
        die("未安装 cryptography（AES-128 解密需要）：python -m pip install cryptography")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(cipher) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def parse_segments(lines: list[str]) -> list[dict]:
    seq = 0
    cur = None
    out: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(re.sub(r"^#EXT-X-MEDIA-SEQUENCE:\s*", "", line))
        elif line.startswith("#EXT-X-KEY:"):
            k = parse_ext_x_key(line)
            cur = None if k["method"] == "NONE" else k
        elif line and not line.startswith("#"):
            method = cur["method"] if cur else "NONE"
            key_uri = cur["uri"] if cur else None
            iv = None
            if cur:
                iv = cur["iv"] if cur["iv"] is not None else sequence_iv(seq)
            out.append({"uri": line, "seq": seq, "method": method, "key_uri": key_uri, "iv": iv})
            seq += 1
    return out


def parse_map_uri(lines: list[str]) -> str | None:
    """EXT-X-MAP 初始化段 URI（fMP4 HLS）；无则返回 None（普通 TS HLS）。"""
    for raw in lines:
        line = raw.strip()
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                return m.group(1)
    return None


# fMP4 盒类型（偏移 4 起的 4 字节）；用于判断分片是否已明文（免重复解密）
_FMP4_BOXES = {b"ftyp", b"moov", b"styp", b"moof", b"mdat", b"sidx", b"free", b"skip", b"emsg"}


def looks_plaintext(data: bytes) -> bool:
    """TS 首字节 0x47，或 fMP4 的已知盒类型 -> 视为已解密。"""
    if not data:
        return False
    if data[0] == 0x47:
        return True
    return len(data) >= 8 and data[4:8] in _FMP4_BOXES


def get_media_playlist(url: str, quality: str, workdir: Path, page: str, extra_headers: list[str], depth: int, total_steps: int) -> dict:
    if depth > 3:
        die("playlist nesting deeper than 3 levels")
    text = fetch_text(url, workdir, page, extra_headers)
    lines = text.splitlines()
    if not any(l.startswith("#EXTM3U") for l in lines):
        die(f"not an m3u8: {url}")
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        variants = []
        i = 0
        while i < len(lines):
            if lines[i].startswith("#EXT-X-STREAM-INF"):
                bw = 0
                m = re.search(r"BANDWIDTH=(\d+)", lines[i])
                if m:
                    bw = int(m.group(1))
                res = ""
                m = re.search(r"RESOLUTION=(\d+x\d+)", lines[i])
                if m:
                    res = m.group(1)
                j = i + 1
                while j < len(lines) and (lines[j] == "" or lines[j].startswith("#")):
                    j += 1
                if j < len(lines):
                    variants.append({"bw": bw, "res": res, "uri": lines[j].strip()})
                i = j
            i += 1
        if not variants:
            die("no variant URI found in master playlist")
        labels = [re.sub(r"^\d+x", "", v["res"]) + "p" for v in variants]
        chosen = None
        if quality and quality != "highest":
            want = int(re.sub(r"[^0-9]", "", quality) or "0")
            matched = []
            for v in variants:
                h = 0
                m = re.search(r"x(\d+)", v["res"])
                if m:
                    h = int(m.group(1))
                if h == want:
                    matched.append(v)
            if not matched:
                available = sorted(set(labels), key=lambda s: int(re.sub(r"[^0-9]", "", s) or 0), reverse=True)
                die(f"quality {want}p not found; available: {'/'.join(available)}")
            chosen = max(matched, key=lambda v: v["bw"])
        else:
            chosen = sorted(variants, key=lambda v: v["bw"])[-1]
        print(f"[1/{total_steps}] 主列表含 {len(variants)} 档画质（{'/'.join(labels)}），本次选择 {chosen['res']} ...")
        return get_media_playlist(resolve_uri(chosen["uri"], url), quality, workdir, page, extra_headers, depth + 1, total_steps)
    return {"url": url, "text": text}


def count_ts(workdir: Path) -> tuple[int, int]:
    files = list(workdir.glob("*.ts"))
    return len(files), sum(f.stat().st_size for f in files)


def close_log(handle) -> None:
    if handle is None:
        return
    # Unwrap the Tee first: if it is still installed on sys.stdout/stderr when the
    # file closes, the interpreter's shutdown flush hits a closed handle and Python
    # exits with status 120.
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, Tee):
            setattr(sys, name, stream._stream)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        handle.close()
    except Exception:
        pass


def wait_enter() -> None:
    try:
        input("按回车关闭窗口...")
    except Exception:
        time.sleep(8)


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Download HLS to mp4")
    ap.add_argument("--m3u8", required=True)
    ap.add_argument("--page", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parallel", type=int, default=12)
    ap.add_argument("--quality", default="highest")
    ap.add_argument("--max-segments", type=int, default=0)
    ap.add_argument("--workdir", default="")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--aria2", default="aria2c")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--extra-header", action="append", default=[])
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
            check_deps.recover("hls")
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


def run(args: argparse.Namespace) -> None:
    preflight("hls", args.aria2, args.ffmpeg, args.ffprobe)
    out_file = Path(args.out).resolve()
    workdir = Path(args.workdir).resolve() if args.workdir else out_file.with_suffix("").parent / (out_file.stem + "_ts")
    extra = list(args.extra_header or [])
    out_file.parent.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    total_steps = 5
    print(f"影片：{out_file.stem}")
    media = get_media_playlist(args.m3u8, args.quality, workdir, args.page, extra, 0, total_steps)
    media_url = media["url"]
    all_lines = media["text"].splitlines()
    segs = parse_segments(all_lines)
    map_uri_raw = parse_map_uri(all_lines)
    map_uri = resolve_uri(map_uri_raw, media_url) if map_uri_raw else None
    is_fmp4 = map_uri is not None
    total_sec = 0.0
    for line in all_lines:
        if line.startswith("#EXTINF"):
            m = re.match(r"^#EXTINF:\s*([\d.]+)", line)
            if m:
                total_sec += float(m.group(1))
    if not segs:
        die("no segments found in media playlist")
    if args.max_segments > 0:
        segs = segs[: args.max_segments]

    methods = {s["method"] for s in segs}
    unsupported = methods - {"NONE", "AES-128"}
    if unsupported:
        die(f"playlist uses {'/'.join(sorted(unsupported))}; SAMPLE-AES / unreadable keys are not supported")
    need_decrypt = "AES-128" in methods
    if need_decrypt:
        if any(s["method"] == "AES-128" and not s["key_uri"] for s in segs):
            die("AES-128 segments have no KEY URI")
        total_steps = 6

    print(f"[1/{total_steps}] 解析播放列表：{args.m3u8}")
    print(f"      媒体列表：{media_url}")
    print(f"      共 {len(segs)} 个分片，视频时长 {fmt_hms(total_sec)}")
    if is_fmp4:
        print("      检测到 fMP4（EXT-X-MAP 初始化段），合并走二进制拼接 + 无损重封装")
    if need_decrypt:
        print("      检测到 AES-128，将在下载后用播放列表 KEY/IV 解密（PKCS7）")

    (workdir / "total.txt").write_text(str(len(segs)), encoding="utf-8")

    input_file = workdir / "aria2.input.txt"

    def build_input() -> tuple[int, int]:
        """(Re)write aria2 input listing only the segments still missing."""
        lines_out: list[str] = []
        needed_local = 0
        skipped_local = 0
        # fMP4: 初始化段（EXT-X-MAP）先下，合并时拼在媒体分片之前
        if is_fmp4:
            init_target = workdir / "init.mp4"
            init_ctrl = Path(str(init_target) + ".aria2")
            if not (init_target.exists() and not init_ctrl.exists() and init_target.stat().st_size > 0):
                lines_out.append(map_uri)
                lines_out.append("  out=init.mp4")
                lines_out.append(f"  header=Referer: {args.page}")
                lines_out.append(f"  header=User-Agent: {UA}")
                for h in extra:
                    if h:
                        lines_out.append(f"  header={h}")
        for i, seg in enumerate(segs):
            out_name = f"{i:06d}.ts"
            target = workdir / out_name
            ctrl = Path(str(target) + ".aria2")
            have = target.exists() and not ctrl.exists() and target.stat().st_size > 0
            if have:
                skipped_local += 1
                continue
            lines_out.append(resolve_uri(seg["uri"], media_url))
            lines_out.append(f"  out={out_name}")
            lines_out.append(f"  header=Referer: {args.page}")
            lines_out.append(f"  header=User-Agent: {UA}")
            for h in extra:
                if h:
                    lines_out.append(f"  header={h}")
            needed_local += 1
        input_file.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
        return needed_local, skipped_local

    def run_aria2(needed_now: int, attempt: int) -> int:
        aria_log = workdir / "aria2.log"
        aria_err = workdir / "aria2.log.err"
        aria2 = shutil.which(args.aria2) or args.aria2
        aria_cmd = [
            aria2,
            f"-j{args.parallel}",
            "-x1",
            "-s1",
            "--max-tries=5",
            "--retry-wait=2",
            "--connect-timeout=15",
            "--timeout=30",
            "--continue=true",
            "--file-allocation=none",
            "--auto-file-renaming=false",
            "-d",
            str(workdir),
            "-i",
            str(input_file),
        ]
        mode = "w" if attempt == 1 else "a"
        with aria_log.open(mode, encoding="utf-8", errors="replace") as out_log, aria_err.open(
            mode, encoding="utf-8", errors="replace"
        ) as err_log:
            p = subprocess.Popen(aria_cmd, stdout=out_log, stderr=err_log)
            t0 = time.time()
            prev = -1
            while p.poll() is None:
                time.sleep(0.8)
                cur, nbytes = count_ts(workdir)
                if cur != prev:
                    pct = min(100, int(100 * cur / n)) if n else 0
                    elapsed = time.time() - t0
                    rate = (cur - (n - needed_now)) / elapsed if elapsed > 2 else 0.0
                    eta = fmt_hms((n - cur) / rate) if rate > 0 and cur < n else "--:--"
                    mb = nbytes / (1024 * 1024)
                    print(
                        f"\r  [{format_bar(pct)}] {cur}/{n}  {pct:3d}%  {mb:7.0f} MB  {rate:5.1f} 片/秒  预计剩余 {eta}   ",
                        end="",
                        flush=True,
                    )
                    prev = cur
        print()
        return p.returncode

    n = len(segs)
    needed, skipped = build_input()
    print(f"[2/{total_steps}] 下载分片：需下载 {needed}，已跳过 {skipped}，并发 {args.parallel}")

    if needed > 0:
        rc = run_aria2(needed, 1)
        got, _ = count_ts(workdir)
        # A transient hiccup usually leaves only a few segments missing. Retry the
        # remainder once automatically (gap <= 5%) before giving up — a stale link
        # fails the whole playlist, so re-fetching only makes sense for small gaps.
        gap = n - got
        if got < n and (gap <= max(1, n // 20)):
            print(f"\n      {got}/{n}，缺 {gap} 片（≤5%），自动补跑一轮 ...")
            need2, _ = build_input()
            if need2 > 0:
                run_aria2(need2, 2)
                got, _ = count_ts(workdir)
        if got < n:
            print(f"\n[错误] 分片不齐 {got}/{n}（aria2 exit {rc}）。日志尾部：")
            try:
                aria_log = workdir / "aria2.log"
                tail = aria_log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                for line in tail:
                    print(f"  {line}")
            except Exception:
                pass
            die(f"download incomplete ({got}/{n})，重新运行同一命令即可断点续传")
        if rc not in (0, None):
            print("      （aria2 退出码非 0，但分片已补齐，继续合并）")

    got, _ = count_ts(workdir)
    if got < n:
        die(f"segment count mismatch: {got}/{n}")
    print(f"[3/{total_steps}] 下载完成：{got}/{n} 个分片")

    if need_decrypt:
        print(f"[4/{total_steps}] AES-128 解密分片 ...")
        key_cache: dict[str, bytes] = {}
        decrypted = 0
        skipped_dec = 0
        for si, seg in enumerate(segs):
            if seg["method"] != "AES-128":
                continue
            target = workdir / f"{si:06d}.ts"
            cipher = target.read_bytes()
            if looks_plaintext(cipher):
                skipped_dec += 1
                continue
            key_url = resolve_uri(seg["key_uri"], media_url)
            if key_url not in key_cache:
                key_path = workdir / f"crypt_{len(key_cache)}.key"
                fetch_to(key_url, key_path, args.page, extra)
                raw_key = key_path.read_bytes()
                key_cache[key_url] = aes128_key_bytes(raw_key, key_url)
                print(f"      fetched key {key_url} ({len(raw_key)} bytes)")
            try:
                plain = decrypt_segment(cipher, key_cache[key_url], seg["iv"])
            except Exception as e:
                die(f"decrypt failed for {target.name}: {e}")
            tmp = Path(str(target) + ".dec.tmp")
            tmp.write_bytes(plain)
            tmp.replace(target)
            decrypted += 1
            if decrypted % 20 == 0 or si == len(segs) - 1:
                pct = int(100 * (si + 1) / len(segs))
                print(f"\r      [{format_bar(pct)}] {si + 1}/{len(segs)}  {pct:3d}%   ", end="", flush=True)
        print()
        print(f"      decrypt done: new {decrypted}, already plaintext {skipped_dec}")
        # fMP4 的初始化段同样受 EXT-X-KEY 保护，按首个 AES 分片的 KEY/IV 解密
        if is_fmp4:
            init_target = workdir / "init.mp4"
            if init_target.exists():
                data = init_target.read_bytes()
                if not looks_plaintext(data):
                    first = next((s for s in segs if s["method"] == "AES-128"), None)
                    if first:
                        key_url = resolve_uri(first["key_uri"], media_url)
                        if key_url not in key_cache:
                            key_path = workdir / f"crypt_{len(key_cache)}.key"
                            fetch_to(key_url, key_path, args.page, extra)
                            key_cache[key_url] = aes128_key_bytes(key_path.read_bytes(), key_url)
                        plain = decrypt_segment(data, key_cache[key_url], first["iv"])
                        tmp = Path(str(init_target) + ".dec.tmp")
                        tmp.write_bytes(plain)
                        tmp.replace(init_target)

    merge_step = 5 if need_decrypt else 4
    verify_step = 6 if need_decrypt else 5
    print(f"[{merge_step}/{total_steps}] 合并为 mp4（无损流复制，不重新编码）...")
    ffmpeg = shutil.which(args.ffmpeg) or args.ffmpeg
    ts_files = sorted(workdir.glob("*.ts"))
    if is_fmp4:
        # fMP4：二进制拼接 init(ftyp+moov) + 各分片(styp+moof+mdat) 得到合法 fragmented mp4，
        # 再无损重封装成普通 mp4（concat 解复用器不认裸分片，故不走它）。
        joined = workdir / "joined.mp4"
        with joined.open("wb") as w:
            w.write((workdir / "init.mp4").read_bytes())
            for p in ts_files:
                w.write(p.read_bytes())
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(out_file)]
        )
    else:
        concat_file = workdir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in ts_files) + "\n", encoding="utf-8")
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out_file)]
        )
    if r.returncode != 0:
        die(f"ffmpeg merge failed (exit {r.returncode})")
    print(f"      合并完成：{out_file}")

    print(f"[{verify_step}/{total_steps}] 校验输出 ...")
    ffprobe = shutil.which(args.ffprobe) or args.ffprobe
    probe = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_file)],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()
    out_dur = float(probe)
    size_mb = out_file.stat().st_size / (1024 * 1024)
    # Confirm the merged file actually carries an audio stream; a silent/broken
    # audio track otherwise only shows up when the user presses play.
    codecs = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out_file)],
        text=True,
        encoding="utf-8",
        errors="replace",
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
    print(f"       大小 {size_mb:,.0f} MB，时长 {fmt_hms(out_dur)}（播放列表 {fmt_hms(total_sec)}），{track_note}")

    if args.cleanup:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[清理] 已删除临时目录 {workdir}")


if __name__ == "__main__":
    raise SystemExit(main())
