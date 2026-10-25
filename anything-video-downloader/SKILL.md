---
name: anything-video-downloader
description: 视频下载：把你有权访问的网页播放页视频保存为 mp4，含通用解析脚本与站点文档模板。支持 HLS（m3u8，含 AES-128）、B站 DASH 双流、直链 mp4（如抖音）。当用户提供播放页链接并要求下载、保存或抓取视频时使用；仅限本人拥有或已获授权的内容，不用于绕过付费墙或 DRM、不用于抓取普通网页内容或直播录制。
---

# 万能视频下载（HLS / DASH / 直链）

通用链路：**识别站点 → 提取媒体地址（按站点文档或通用分析）→ 运行 Python 脚本一键完成下载/合并/校验 → 新站点沉淀文档**。

**三类引擎**（job 的 `engine` 字段，省略时自动推断）：

| 引擎 | 适用 | 取址方式 | 脚本 |
|------|------|----------|------|
| `hls`（默认） | m3u8 影视站（TS 或 fMP4） | 网络请求筛 `.m3u8` | `hls_download.py` |
| `dash` | B站（无 m3u8，双流 `.m4s`） | 接口现取直链（`ep_id`/`bvid`/`aid`） | `bilibili_download.py` |
| `direct` | 直链 mp4（如抖音，播放器给单文件） | 读 `<video>.currentSrc` | `direct_download.py` |

网络请求里只有 `.m4s`/`.mpd` → 非 HLS，按 DASH/直链分析；只有 `video.currentSrc` 的 mp4 → 直链。详见各站点文档（[bilibili.md](references/sites/bilibili.md)、[douyin.md](references/sites/douyin.md)）。

主脚本已改为 Python，**不要再用 PowerShell 的 `Invoke-HlsDownload.ps1` / `Start-Process` 启动下载**。PowerShell 5.1 会踩：UTF-8 中文文件名乱码、`&` 被当成调用运算符、here-string/`-ArgumentList` 拆空格。

## 依赖

| 引擎 | aria2c | ffmpeg | ffprobe | curl | cryptography |
|------|--------|--------|---------|------|--------------|
| `hls` | 必需 | 必需 | 必需 | 必需 | 仅 AES-128 源 |
| `dash` | 必需 | 必需 | 必需 | 必需 | 不需要 |
| `direct` | 必需 | 不需要 | 必需 | 必需 | 不需要 |

- **Python 3.8+**：`python` 在 PATH 中。
- **aria2c**：并发分片下载与断点续传。
- **ffmpeg / ffprobe**：无损合并与成品校验。
- **curl**：拉播放列表/KEY、直链探测（Windows 自带 `curl.exe`）。
- **浏览器访问工具（必需）**：推荐安装 **chrome-devtools MCP**，用于过 Cloudflare、读网络请求取 `.m3u8` / `video.currentSrc`；安装与配置见 [references/setup.md](references/setup.md)。

**启动下载前先检查依赖**：

```text
python "<技能根目录>/scripts/check_deps.py" --engine hls
```

`--engine` 按任务选：`hls`（m3u8）/ `dash`（B站）/ `direct`（直链）；省略时按三个引擎的并集检查。

缺失时按 [references/setup.md](references/setup.md) 安装（含 Windows winget / macOS brew / Linux apt 命令），装完重开终端复核；**不要跳过预检直接启动下载**。`start_hls_windows.py` 会再预检一次：缺依赖时在当前终端报错并给安装命令，不弹下载窗。

> **网络环境提示**：本机 bash 沙箱可能拦掉部分站点主域（实测有站点直连超时），但**通常放行 CDN**。所以浏览器只用来过校验、取 m3u8 地址，下载脚本本身在命令行就能跑通——不必为了下载去绕沙箱。取到地址后先用 `curl.exe -sS -o /dev/null -w "%{http_code}"` 打一下 CDN，确认可达再启动下载。

## 路径约定

技能运行时由模型像浏览文件系统一样访问本目录，路径写错是最常见的失败原因。统一按下面规则：

- **技能根目录 = 本 `SKILL.md` 所在目录**。文中所有 `scripts/...`、`references/...` 都相对技能根目录，一律用正斜杠，不写 `scripts\...`。
- **命令统一写 `<技能根目录>/scripts/...`**，执行时替换成绝对路径。不要依赖 cwd：`cd` 到技能目录只对当前这一条命令有效，换终端或子进程就失效。
  - Claude Code 的 bash 注入命令可以用 `${CLAUDE_SKILL_DIR}`，但这是 Claude 生态专属变量，其他宿主不保证支持；跨宿主时一律用绝对路径占位符。
  - 脚本内部用 `Path(__file__).resolve().parent` 定位同目录资源，与 cwd 无关。
- **下载目录用绝对路径**。本文示例写 `<下载目录>`，实际执行时换成用户指定目录（如 `$env:USERPROFILE/Downloads/video-downloader`）；不要硬编码某台机器的磁盘路径。`make_job.py --root` 会把它 resolve 成绝对路径。
- **job JSON 的 `out` / `workdir` / `log` 用绝对路径**，由 `make_job.py` 生成；手写 job 时也写成绝对路径。`start_hls_windows.py` 遇到相对路径时按 **job 文件所在目录** 解析，不按 cwd。
- 文档中的命令都写成单行，避免 `^` / 反引号续行在 cmd 与 PowerShell 之间不通用。

## 步骤

### 1. 识别站点，查找站点文档

按顺序查找该站点的参考文档（文件名 = 主域名）：

1. 先查 [references/sites/](references/sites/)（随仓库公开，如 `bilibili.md`）
2. 再查 `references/sites-private/`（本地私有，`.gitignore` 排除，不入库）
3. 都没有才走通用分析

- **找到** → 按文档的「提取方式」（HLS 的「m3u8 提取方式」，或无 m3u8 站点的直链/DASH 提取方式，如 [bilibili.md](references/sites/bilibili.md)、[douyin.md](references/sites/douyin.md)）操作，直接进入步骤 3。
- **没有** → 走步骤 2 的通用分析，下载成功后必须按模板沉淀新文档（见「站点文档沉淀」）。

### 2. 通用 m3u8 分析（新站点）

> 若网络请求里**完全没有 `.m3u8`**：只有 `.m4s` 双流（如 B站）→ DASH；拿不到媒体请求、只有 `<video>.currentSrc` 的 mp4（如抖音 webcodecs）→ 直链。都别硬套本节，按站点文档走（[bilibili.md](references/sites/bilibili.md)、[douyin.md](references/sites/douyin.md)）。
>
> 反过来，**有 `.m3u8` 但分片是 `.m4s`**（playlist 含 `#EXT-X-MAP`）→ 仍是 HLS，只是 **fMP4** 分片，脚本已支持（见下方「脚本行为要点」），照常走本节。

1. 浏览器打开播放页，等待 Cloudflare 校验与播放器加载。
2. 按优先级提取 master m3u8 地址：
   - **网络请求里筛 `.m3u8`（首选）**：不少站的播放器会**自动加载**，不用点播放，`.ts` 与 `crypt.key` 也会一并出现。这是最省事、也最不容易踩时间戳过期坑的路径。
   - 页面 JS 播放器配置变量（常见名：`player_aaaa`、`player_data`、`videoUrl` 等，可在页面源码中搜 `m3u8` 定位变量名）
   - 以上都没有时，再考虑解 packed JS / 手工调播放器接口（见各站点文档的兜底写法）
3. 检查加密：
   - 无 `#EXT-X-KEY`，或 `METHOD=NONE`，或页面 `encrypt=0` → 未加密，脚本直下。
   - `#EXT-X-KEY:METHOD=AES-128` 且 KEY URI 可拉取 → **脚本支持**：下载后、合并前自动 AES-CBC + PKCS7 解密。IV 用标签值；缺省则用该片 media sequence 的 16 字节大端。
   - `SAMPLE-AES` / 密钥不可读 / 没有 KEY URI → 停止，不能继续。
4. **提取视频名称**（供输出文件命名）：优先 `document.title`，其次播放器配置里的名称字段（如 `player_aaaa.vod_data` 内的名称项）；清洗规则见步骤 3。
5. 快速验证链接：用 Python/`curl.exe -s -L`（不要用 PowerShell 的 `curl` 别名），带 Chrome UA 和播放页 Referer，确认返回 `#EXTM3U`。
6. 若返回含 `#EXT-X-STREAM-INF`（多档画质），**先问用户选画质**：选项按从高到低列出各档分辨率（标注最高档「推荐」），用户选择后写入 job JSON 的 `quality`；仅单档画质时直接下载，无需询问。

### 3. 运行下载脚本

启动器会先预检依赖（见「依赖」）；缺失时在当前终端报错并给出安装命令，不弹下载窗。依赖齐全后再按下面的方式启动。

[scripts/hls_download.py](scripts/hls_download.py) 覆盖「master 解析 → 分片下载 → 合并 → 校验」。正式下载必须走 [scripts/start_hls_windows.py](scripts/start_hls_windows.py)：先写 UTF-8 JSON，再弹出**一个**下载控制台。下载脚本自己会打进度条，不要再开进度窗。

**B站（DASH，无 m3u8）用 [scripts/bilibili_download.py](scripts/bilibili_download.py)**：`make_job.py` 以 `--ep-id`（课程 cheese）或 `--bvid`/`--aid`（普通视频）代替 `--m3u8`，生成 `engine=dash` 的 job，`start_hls_windows.py` 自动分派到该脚本。脚本**运行时现调 playview/playurl 取直链**（签名地址约 2h 过期，绝不烤进 job）→ aria2 双流下载（视频 m4s + 音频 m4s）→ ffmpeg 无损合并 → ffprobe 校验；日志、断点续传、弹窗行为与 HLS 引擎一致。`--quality` 支持 `1080p+`/`1080p`/`720p`/...（多档同样先问用户），`--codec avc|hev|av1`。接口与 CDN 均匿名可达，**不需要浏览器**；仅当你本人已登录且已购买/有权访问时，才把 Cookie 放进 `extra_headers`——未购买的内容接口不会返回流，也不应尝试绕过。详见 [references/sites/bilibili.md](references/sites/bilibili.md)。

**直链 mp4（如抖音）用 [scripts/direct_download.py](scripts/direct_download.py)**：`make_job.py` 以 `--url`（浏览器里取到的 `<video>.currentSrc`）代替 `--m3u8`，生成 `engine=direct` 的 job，`start_hls_windows.py` 自动分派。脚本流程：Range 探测直链（验可达 + 拿总大小/类型）→ aria2 单文件下载（断点续传、小缺口自动补跑）→ ffprobe 校验；**无合并步骤**（单文件已含音视频）。与 B站不同，抖音的签名直链**没有公开接口可重取**，所以必须烤进 job 并**取到后尽快下载**（过期 403 就回浏览器重提）。`--quality`/`--max-segments` 不适用。详见 [references/sites/douyin.md](references/sites/douyin.md)。

**推荐启动方式（用 [scripts/make_job.py](scripts/make_job.py) 生成 job）：**

命名规则（剥站点尾缀、清 Windows 非法字符、保留名、集数后缀）都收敛在 `make_job.py` 里，不要再手写一次性 `_prep_*.py`。尾缀 `--strip` 从站点文档读（如 `" - 抖音"` / `"_哔哩哔哩_bilibili"` / `" - HD国语 在线播放 |"`）；**尾缀本身以 `--` 开头时用等号形式** `--strip="--播放器"`。

```text
python "<技能根目录>/scripts/make_job.py" --page "<播放页 URL>" --m3u8 "<m3u8 URL>" --title "<document.title 原文>" --strip "<站点尾缀>" --root "<下载目录>" --episode 3 --video-id 421485
```

媒体来源三选一：`--m3u8`（HLS）/ `--url`（直链，如抖音）/ `--ep-id`、`--bvid`、`--aid`（B站 DASH）。

脚本会打印清洗后的名称、`out`、job 路径，以及一条**用绝对路径写的启动命令**：

```text
python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"
```

**多集批量**：写一个 JSON 数组（每项 `{title,m3u8,page,episode?,video_id?}`），用 `--batch` 一次生成多个 job 和启动命令：

```text
python "<技能根目录>/scripts/make_job.py" --batch "<下载目录>/batch.json" --strip "<站点尾缀>" --root "<下载目录>"
```

共享的 `--strip` / `--quality` / `--parallel` / `--cleanup` 从命令行给；`--page` / `--m3u8` / `--title` 可在命令行给默认值，由每项覆盖。job 落在 `<root>/_jobs/`。

**路径一律用绝对路径**（统一正斜杠）。`--args-json` 的相对路径按当前进程目录解析，找不到就直接报错，不再沿脚本目录向上猜同名文件——技能可能装在很深的目录里，猜错成本比报错高。启动失败时先怀疑路径，直接用 `make_job.py` 打印出的绝对路径命令。

**强制启动方式（避免把 URL 写进 PowerShell 命令行）：**

1. 用 `make_job.py`（或 Python 手写）产出 job JSON——路径、中文文件名、带 `&` 的 m3u8 都放 JSON 里。
2. `python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"`

job JSON 字段：

```json
{
  "m3u8": "<m3u8 URL>",
  "page": "<播放页 URL>",
  "out": "<下载目录>/<视频名称>.mp4",
  "parallel": 12,
  "cleanup": true,
  "quality": "highest",
  "max_segments": 0,
  "extra_headers": [],
  "log": "",
  "pause": false
}
```

可选字段 `workdir`（自定义分片临时目录，默认 `<out>` 同级的 `<名称>_ts`）。`max_segments` 冒烟测试填 `3`。`quality` 仅在用户已选画质时填 `1080p` / `720` 等。

B站 dash job 没有 `m3u8`，代之以下字段（`engine` 省略时由 `ep_id`/`bvid`/`aid` 存在与否自动推断；三者选一，`max_segments` 不适用）：

```json
{
  "engine": "dash",
  "ep_id": 720220,
  "quality": "1080p+",
  "codec": "avc"
}
```

其余 `page` / `out` / `parallel` / `cleanup` / `extra_headers` / `log` / `pause` 与上表一致；`extra_headers` 可放 `Cookie: ...`，但仅限你本人已登录且有权访问的内容；不要用它绕过付费墙。

直链 job（如抖音）没有 `m3u8`，代之 `url`（`engine` 省略时由 `url` 存在与否自动推断；`max_segments`/`quality` 不适用）：

```json
{
  "engine": "direct",
  "url": "https://v11-weba.douyinvod.com/.../....mp4?...签名..."
}
```

其余字段同上。签名直链会过期，取到后尽快启动下载。

`log` / `pause` 两个字段：

- `log` 不填时**默认写成 `<out>.log`**；填 `""` 关闭。下载跑的每一步（解析 → 下载 → 解密 → 合并 → 校验）以及 `[完成]` 行的「实测时长 vs 播放列表时长」都会落盘（进度条的 `\r` 残影已过滤，不会刷屏）。**启动下载后要回报结果或排查失败，直接读这个文件**——不要试图去读下载窗口。
- `pause` 默认 `false`：跑完自动关窗（不留下悬挂窗口）。需要人盯着屏幕看结果时才置 `true`。

直接调用（调试用，仍建议 JSON，不要在 PowerShell 里裸拼带 `&` 的 URL）：

```text
python -u "<技能根目录>/scripts/hls_download.py" --m3u8 <m3u8> --page <播放页> --out "<下载目录>/<名称>.mp4" --parallel 12 --cleanup --log "<下载目录>/<名称>.mp4.log"
python -u "<技能根目录>/scripts/hls_download.py" --m3u8 <m3u8> --page <播放页> --out "<下载目录>/test.mp4" --max-segments 3 --cleanup
```

关键参数：

| 参数 | 说明 |
|------|------|
| `--m3u8` | master 或 media m3u8 地址（master 会自动解析） |
| `--page` | 播放页 URL，用作 Referer |
| `--out` | 输出 mp4 路径，**以页面视频名称命名**（规则见下） |
| `--parallel` | aria2 并发分片数，默认 12 |
| `--quality` | `highest`（默认）或高度如 `1080p`/`720` |
| `--max-segments N` | 只下前 N 个分片（冒烟） |
| `--cleanup` | 成功后删除分片临时目录 |
| `--extra-header` | 可重复，如 `x-cdn-auth: ...` |
| `--log PATH` | 把全部输出同时写进文件（无人值守时回读 `[完成]`/报错）；`start_hls_windows.py` 默认传 `<out>.log` |
| `--pause` | 跑完等回车再关窗；默认不加，控制台跑完即关 |

**命名规则（视频名称 → 文件名）：**

规则实现在 [scripts/make_job.py](scripts/make_job.py) 的 `build_filename`，直接调它即可，不必手工复刻：

1. 名称来源：播放页 `document.title`（或站点文档指定的字段）。剥站点尾缀用 `--strip`，如 `示例视频 - HD国语 在线播放` 配 `--strip " - HD国语 在线播放"` → `示例视频`。
2. 清洗 Windows 非法字符 `\ / : * ? " < > |`、控制字符与首尾点/空格；命中 Windows 保留名（`CON`/`NUL`/`COM1`…）时前置下划线；清洗后为空则回退 `--video-id`。
3. 电影/单集：`<名称>.mp4`；多集：`--episode N` → `<名称>_第<NN>集.mp4`。可直接用 `--name` 指定基名覆盖 title 命名。

脚本行为要点：

- 控制台全程中文提示：解析 → 下载进度条 → 解密（如有）→ 合并 → 校验。**必须用 `start_hls_windows.py` 弹可见窗口**给用户看进度；而窗口本身不进 agent 上下文，所以结果**一律读 `<out>.log`**，不要试图去读窗口。
- 日志文件里 `[完成]` 行自带校验结论（实测时长 vs 播放列表时长），排查失败时看同一文件的 `[错误]` / aria2 日志尾即可。
- **断点续传**：重跑同一命令，跳过已完成分片（文件存在且无 `.aria2` 控制文件）；分片齐了直接合并。
- **小缺口自动补跑**：若一轮 aria2 后只差 ≤5%（或 1 片），脚本会自动补跑一轮再合并；缺口更大则报错退出（多为链接过期），回步骤 1/2 重提。
- 分片 302 由 curl/aria2 跟随。CDN 换主机的 retry 日志无害。
- AES-128 时步数为 6：解析 → 下载 → 下载完成 → 解密 → 合并 → 校验。已是明文（首字节 `0x47`）的分片会跳过。
- **fMP4（`#EXT-X-MAP`）也支持**：分片是 `.m4s`（init = `ftyp`+`moov`，媒体段 = `styp`+`moof`+`mdat`）。脚本先下 init，合并时**二进制拼接 init + 分片**再无损重封装成 mp4（不走 TS 的 concat 解复用器，否则裸分片无法解析）；日志会打「检测到 fMP4」。TS 分片仍走原 concat 路径。
- 输出前用 ffprobe 比对 `#EXTINF` 总时长，并确认存在视频/音频流，打印在 `[完成]` 行；缺音频会打 `[警告]`。
- aria2 非 0 且分片不齐会报错并打日志尾；优先怀疑链接过期，回步骤 1/2 重提。个别缺失则重跑续传。
- **B站 dash 引擎步数为 4**：解析直链（含 Range 探测）→ 下载双流 → 合并 → 校验；日志每 5 秒落一条 `[进度]`（视频/音频各自字节数），直链过期会明确提示「重新生成 job」（重跑即自动换新直链）。
- **直链 direct 引擎步数为 3**：探测直链（Range，验可达+总大小）→ 下载单文件 → 校验；无合并。同样每 5 秒一条 `[进度]`，小缺口自动补跑一轮；直链过期（403/非 206）会提示回浏览器重新提取。

### 4. 进度查询（用户问进度时才跑）

下载窗里已经有实时进度条，**默认只开这一个窗口**。不要再弹 `hls_progress.py --watch`。

用户问「进度/结束了吗」时，**先看 `<out>.log`**：出现 `[完成]` 就是成了，出现 `[错误]` 就是挂了（含原因）。日志没结论时，再在当前会话跑一次单次查询，不要猜：

```text
python -u "<技能根目录>/scripts/hls_progress.py" --root "<下载目录>"
```

- 进度来自「`.ts` 数量 vs `total.txt`」。
- 可加 `--workdir <某_ts目录>` 或 `--total N`（旧目录没有 total.txt 时）。
- `--watch` 仅在用户明确要求单独盯进度时才用。
- **B站 dash 任务不适用 `hls_progress.py`**（没有 `.ts` 分片）：先读 `<out>.log`（每 5 秒一条 `[进度]`），或看 workdir 里 `video.m4s` / `audio.m4s` 的字节数对比日志里的总大小。
- **直链 direct 任务同样不适用 `hls_progress.py`**：读 `<out>.log`（每 5 秒一条 `[进度]`），或看 workdir 里 `media.*` 的字节数对比探测到的总大小。

### 5. 站点文档沉淀（新站点必做）

下载成功后，按 [references/sites/_TEMPLATE.md](references/sites/_TEMPLATE.md) 模板新建站点文档，按内容分流：

- 主流平台、公开合法站点 → `references/sites/<主域名>.md`（随仓库公开）
- 成人、盗版或其他不适合公开的内容 → `references/sites-private/<主域名>.md`，并在文档开头注明「不得提交到公开仓库」

记录：

- 匹配特征（域名、播放页 URL 模式、集数在 URL 中的编码方式）
- 提取方式（优先「读网络请求筛 `.m3u8`」，兜底再写 JS 变量/packed JS；注明 encrypt 判定、是否需浏览器）。**无 m3u8 的站点按实际流程写**：DASH（接口地址/参数/响应结构/选流规则，参考 [bilibili.md](references/sites/bilibili.md)）或直链（取址方式如 `<video>.currentSrc`、校验方式，参考 [douyin.md](references/sites/douyin.md)）
- 视频名称提取方式（`document.title` 的格式与**给 `make_job.py --strip` 用的尾缀字符串**）
- 播放列表结构（master / media / 分片 各层域名与路径模式）
- 请求头要求（Referer 规则、UA）
- 时效性与过期现象
- 已验证的脚本调用示例（`make_job.py` 命令行 + 打印出的 `start_hls_windows.py` 绝对路径命令）

下次遇到同站点请求时直接按文档执行，跳过通用分析。

## 注意事项

- 多集用 `make_job.py --batch` 一次生成所有 job，再逐个执行启动命令；同站点建议 ≤3 集同时（并行多个 `start_hls_windows.py` 实例）。
- 依赖缺失时不要反复重试下载：先跑 `check_deps.py` 按 [references/setup.md](references/setup.md) 装好 ffmpeg/aria2c，重开终端再下。
- 公开仓库不收录色情、盗版站点文档；此类文档只放 `references/sites-private/`（`.gitignore` 排除）。
- 本技能只用于下载本人拥有或已获授权的内容；不提供成品资源，不绕过 DRM / 付费墙。
- 仅用于个人学习或备份，提醒用户注意版权，勿传播或商用。
