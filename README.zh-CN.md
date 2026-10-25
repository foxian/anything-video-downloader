# anything-video-downloader

[English](README.md) | 中文

把网页播放页里的视频下载为 mp4 的技能包，内置三种下载引擎和站点知识库。

- **HLS（默认）**：m3u8 流媒体站点，TS 或 fMP4 分片，自动识别并解密 AES-128
- **DASH**：B站双流（视频 `.m4s` + 音频 `.m4s`），运行时重新解析取流地址
- **直链**：直接给 mp4 地址的站点（如抖音），单文件下载

遇到新站点时，按通用流程分析并沉淀站点文档：公开合法站点写入 `references/sites/`，成人、盗版等不适合公开的站点写入 `references/sites-private/`（不入库）。

## 仓库结构

```text
.
├── README.md
├── README.zh-CN.md
└── anything-video-downloader/
    ├── SKILL.md                  # 技能主文档：完整流程与参数说明
    ├── references/setup.md       # 依赖安装与 chrome-devtools MCP 配置
    ├── references/sites/         # 公开站点解析知识库（文件名 = 主域名）
    │   ├── _TEMPLATE.md          # 新站点文档模板
    │   ├── bilibili.md           # B站 DASH
    │   └── douyin.md             # 抖音直链
    ├── references/sites-private/ # 本地私有站点文档（.gitignore 排除，不入库）
    └── scripts/
        ├── check_deps.py         # 依赖检查：aria2c / ffmpeg / ffprobe / curl / cryptography
        ├── make_job.py           # 生成 job JSON 与启动命令（推荐入口）
        ├── start_hls_windows.py  # 打开下载控制台，按引擎分发
        ├── hls_download.py       # HLS：解析 → 下载 → 解密 → 合并 → 校验
        ├── bilibili_download.py  # DASH：解析双流 → 下载 → 合并 → 校验
        ├── direct_download.py    # 直链：探测 → 下载 → 校验
        └── hls_progress.py       # 按需查看分片进度
```

## 路径约定

- 本 README 位于仓库根目录，命令里的 `anything-video-downloader/scripts/...` 是**仓库相对路径**。
- 安装后**技能根目录是 `anything-video-downloader/`**（`SKILL.md` 所在目录）。在该目录下可写 `scripts/...`；其他位置一律写 `<技能根目录>/scripts/...` 并替换为绝对路径。
- `${CLAUDE_SKILL_DIR}` 只在 Claude Code 中保证可用（主要面向 bash 注入命令），其他宿主不保证；跨宿主技能改用绝对路径占位符。脚本内用 `Path(__file__).resolve().parent` 定位自身。
- 统一使用正斜杠，不要写 `scripts\...`。
- 示例中的 `<下载目录>` 换成绝对路径，如 `$env:USERPROFILE/Downloads/video-downloader`；不要硬编码某台机器的路径。
- job JSON 的 `out` / `workdir` / `log` 必须是绝对路径，`make_job.py` 会转换；手写 job 时相对值按 **job 文件所在目录** 解析（不是 cwd）。

## 环境依赖

| 依赖 | 说明 |
|------|------|
| Python 3.8+ | `python` 在 PATH 中；AES-128 解密需要 `cryptography` |
| aria2c | 在 PATH 中；并发分段下载与续传 |
| ffmpeg / ffprobe | 在 PATH 中；合并与成品校验（`direct` 引擎只需 ffprobe） |
| curl | 取 playlist/密钥与直链探测；Windows 10/11 自带 `curl.exe` |
| 浏览器工具 | 推荐 **chrome-devtools MCP**：过 Cloudflare 校验并读网络请求取媒体地址 |

下载前检查：

```text
python anything-video-downloader/scripts/check_deps.py
```

逐项输出 `[OK]` / `[缺失]`，并给出 Windows / macOS / Linux 的安装命令。完整安装步骤与 **chrome-devtools MCP 配置**见 [references/setup.md](anything-video-downloader/references/setup.md)。

## 快速开始

### 0. 检查依赖

```text
python anything-video-downloader/scripts/check_deps.py
```

退出码 `0` 再继续。缺 ffmpeg/aria2c 时按 [references/setup.md](anything-video-downloader/references/setup.md) 安装，装完重开终端；启动器 `start_hls_windows.py` 也会预检，缺失时在当前终端报错而不弹下载窗。

### 1. 提取媒体地址

用浏览器打开播放页，等校验和播放器加载完成，然后按站点文档取地址：

- HLS：网络请求里筛 `.m3u8`（首选），或页面播放器配置变量
- B站：直接拿 `ep_id` / `bvid` / `aid`，无需浏览器
- 直链：读 `<video>.currentSrc`（如抖音）

站点文档按顺序查找：技能根目录下的 `references/sites/`（公开）→ `references/sites-private/`（仅本地）→ `SKILL.md` 的通用流程。

### 2. 生成 job 并启动

HLS：

```text
python anything-video-downloader/scripts/make_job.py --page "<播放页 URL>" --m3u8 "<m3u8 地址>" --title "<document.title 原文>" --strip "<站点标题后缀>" --root "<下载目录>"
```

B站：

```text
python anything-video-downloader/scripts/make_job.py --bvid "BV1xx411c7mD" --quality 1080p --root "<下载目录>"
```

直链（抖音等）：

```text
python anything-video-downloader/scripts/make_job.py --url "<video.currentSrc>" --page "<播放页 URL>" --root "<下载目录>"
```

`make_job.py` 会打印清洗后的文件名、输出路径、job JSON 路径和绝对路径启动命令：

```text
python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"
```

执行该命令即可打开下载控制台。启动器按 job 里的 `engine` 分发到对应脚本。

### 3. 批量多集

写一个 JSON 数组（每项 `{title, m3u8|url|ep_id, page, episode?, video_id?}`），然后：

```text
python anything-video-downloader/scripts/make_job.py --batch "<下载目录>/batch.json" --strip "<站点后缀>" --root "<下载目录>"
```

job 文件写入 `<下载目录>/_jobs/`。

## 输出与命名

规则在 `make_job.py` 的 `build_filename`：

1. 取 `document.title`，用 `--strip` 去掉站点后缀
2. 清除 Windows 非法字符与保留名，空名回退到 `--video-id`
3. 单集/电影：`<name>.mp4`；剧集加 `--episode N`：`<name>_第<NN>集.mp4`
4. 想直接指定基名用 `--name`

## 进度与日志

- 下载控制台自带进度条，保持唯一窗口
- 读 `<out>.log` 看结果：`[完成]` 表示成功，`[错误]` 会带原因
- 断点续传：重跑同一命令会跳过已完成分片
- 小缺口（≤5% 或一个分片）会自动补；大缺口通常说明地址过期，重新提取
- 需要进度时才运行（仅 HLS）：`python anything-video-downloader/scripts/hls_progress.py --root "<下载目录>"`

## 合规边界

- 只下载本人拥有或已获授权的内容
- 公开仓库不收录色情站点文档；站点文档分公开与私有两层
- 不绕过 DRM 或付费墙，不提供成品资源或盗版站点清单
- 仅用于个人学习或备份，注意版权，勿传播或商用

## 注意

- 本 README 里的 `anything-video-downloader/scripts/...` 是仓库相对路径，须在仓库根目录执行；其他位置用技能目录的绝对路径
- 输出目录与 job JSON 一律用绝对路径，避免层级数错
- 带 `&` 的 URL 不要直接放在 PowerShell 命令行，统一走 job JSON
- B站取流地址约 2 小时过期，运行时重新解析；抖音签名 URL 过期需回浏览器重新提取
- 发布/打包前确认 `references/sites-private/` 不在产物中，尤其是会打包整个工作树的发布工具
- 仅用于个人学习或备份，注意版权，勿传播或商用

完整流程、参数表与各引擎脚本行为见 [SKILL.md](anything-video-downloader/SKILL.md)。
