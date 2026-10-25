# anything-video-downloader

[English](README.md) | 中文

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Engines](https://img.shields.io/badge/engines-HLS%20%7C%20DASH%20%7C%20Direct-success)
![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-purple)

**给它一个播放页，还你一个干净的 mp4；下一个同类站点，直接不用重新分析。**

一个带三种下载引擎的 Agent 技能：自动处理加密流，并在每次使用后沉淀站点知识。没有录制屏幕的土办法，也没有「一个工具只支持一个站」的限制——一套可复用的流程，用得越多越快。

## 它强在哪

- **每个新站点都会变成经验。** 第一次遇到某站，通用流程会判断播放器如何交付视频，并把结论写成 `references/sites/<域名>.md`。下次再遇到同站点，先读文档，不再从零分析。
- **覆盖你真正常见的三种交付方式。** HLS/m3u8 流、B站式 DASH 双流、以及直接给 mp4 的站点。
- **把脏活都处理掉。** AES-128 解密、fMP4 初始化段、音视频分离、Windows 非法文件名、签名 URL 过期。
- **是真能下完，不是演示。** 分片并发、断点续传、自动补小缺口、ffprobe 成品校验、每次下载独立日志。
- **批量友好。** 一季的剧集，一条命令生成全部 job。
- **路径可预期。** 输出绝对路径，不受 cwd 影响，不含任何本机硬编码路径。

## 能下什么

| 引擎 | 典型来源 | 已处理的情况 |
|------|----------|--------------|
| `hls`（默认） | 使用 `.m3u8` 的流媒体站点 | TS 或 fMP4 分片、`EXT-X-MAP` 初始化段、AES-128 密钥、按分片续传 |
| `dash` | B站视频页 | 视频 `.m4s` + 音频 `.m4s`、清晰度选择、运行时重新解析取流地址 |
| `direct` | 暴露 mp4 直链的站点（如抖音） | 签名 URL 探测、单文件下载、大小与时长校验 |

只要不绕过 DRM、不绕过付费墙，流程能触达的都在范围内：剧集页、课程播放器、自建 HLS、公开视频平台。成人、盗版来源不在本公开仓库的支持范围内。

## 知识库会自己长大

```text
新站点
   |
   v
打开播放页  ->  看网络请求 / 播放器配置  ->  判定引擎
   |                                        |
   |                                        v
   |                              下载、校验、写日志
   |                                        |
   +--------------------<-------------------+
                        |
                        v
            写入 references/sites/<域名>.md
                        |
                        v
     下次请求：读文档，跳过分析，直接下载
```

查找顺序：公开 `references/sites/` → 本地 `references/sites-private/` → `SKILL.md` 的通用流程。站点文档会记录：匹配 URL、播放器类型、取地址方法、加密方式、CDN 特点、过期表现，以及一条已验证的命令示例。

## 快速开始

### 0. 检查依赖

```text
python anything-video-downloader/scripts/check_deps.py
```

逐项输出 `[OK]` / `[缺失]`，并给出 Windows、macOS、Linux 的安装命令。退出码 `0` 再继续。

### 1. 提取媒体地址

打开播放页，等校验和播放器加载完成，然后按站点文档取地址：

- **HLS**：网络请求里筛 `.m3u8`（首选），或读播放器配置变量
- **B站**：直接用 `ep_id` / `bvid` / `aid`，无需浏览器
- **直链**：读 `<video>.currentSrc`

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

执行即可打开下载控制台，启动器按 job 的 `engine` 分发。

### 3. 批量下一整季

写一个 JSON 数组（每项 `{title, m3u8|url|ep_id, page, episode?, video_id?}`），然后：

```text
python anything-video-downloader/scripts/make_job.py --batch "<下载目录>/batch.json" --strip "<站点后缀>" --root "<下载目录>"
```

job 文件写入 `<下载目录>/_jobs/`，逐个执行启动命令；同站点建议并发不超过 3 个。

## 内置的兜底能力

| 情况 | 行为 |
|------|------|
| 下载被打断 | 重跑同一命令，已完成分片自动跳过 |
| 少量分片失败 | 缺口 ≤5% 或仅一个分片时自动重试 |
| 大面积失败 | 判为地址过期，提示重新提取，而不是无限重试 |
| B站音视频分离 | 双流分别下载，再合并并校验 |
| 标题含非法字符 / 保留名 | 自动清洗，空名回退 `--video-id`，剧集补集号 |
| 需要确认成功 | `ffprobe` 校验成品；`<out>.log` 以 `[完成]` 收尾 |

## 输出与命名

1. 取 `document.title`，用 `--strip` 去掉站点后缀
2. 清除 Windows 非法字符与保留名，空名回退到 `--video-id`
3. 单集/电影：`<name>.mp4`；剧集加 `--episode N`：`<name>_第<NN>集.mp4`
4. 想直接指定基名用 `--name`

需要看进度时才运行（仅 HLS）：

```text
python anything-video-downloader/scripts/hls_progress.py --root "<下载目录>"
```

## 环境依赖

| 依赖 | 说明 |
|------|------|
| Python 3.8+ | `python` 在 PATH 中；AES-128 解密需要 `cryptography` |
| aria2c | 在 PATH 中；并发分段下载与续传 |
| ffmpeg / ffprobe | 在 PATH 中；合并与校验（`direct` 引擎只需 ffprobe） |
| curl | 取 playlist/密钥与直链探测；Windows 10/11 自带 `curl.exe` |
| 浏览器工具 | 推荐 **chrome-devtools MCP**：过 Cloudflare 校验并读网络请求取媒体地址 |

完整安装步骤与 chrome-devtools MCP 配置见 [references/setup.md](anything-video-downloader/references/setup.md)。

<details>
<summary><b>仓库结构</b></summary>

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

</details>

<details>
<summary><b>路径约定</b></summary>

- 本 README 位于仓库根目录，命令里的 `anything-video-downloader/scripts/...` 是**仓库相对路径**。
- 安装后**技能根目录是 `anything-video-downloader/`**（`SKILL.md` 所在目录）。在该目录下可写 `scripts/...`；其他位置一律写 `<技能根目录>/scripts/...` 并替换为绝对路径。
- `${CLAUDE_SKILL_DIR}` 只在 Claude Code 中保证可用（主要面向 bash 注入命令），其他宿主不保证；跨宿主技能改用绝对路径占位符。脚本内用 `Path(__file__).resolve().parent` 定位自身。
- 统一使用正斜杠，不要写 `scripts\...`。
- 示例中的 `<下载目录>` 换成绝对路径，如 `$env:USERPROFILE/Downloads/video-downloader`；不要硬编码某台机器的路径。
- job JSON 的 `out` / `workdir` / `log` 必须是绝对路径，`make_job.py` 会转换；手写 job 时相对值按 **job 文件所在目录** 解析（不是 cwd）。

</details>

## 合规边界

- 只下载本人拥有或已获授权的内容
- 不绕过 DRM 或付费墙，不提供成品资源或盗版站点清单
- 站点文档分公开与私有两层；公开仓库不收录成人站点文档
- 仅用于个人学习或备份，注意版权，勿传播或商用

## 注意

- 带 `&` 的 URL 不要直接放在 PowerShell 命令行，统一走 job JSON
- B站取流地址约 2 小时过期，运行时重新解析；抖音签名 URL 过期需回浏览器重新提取
- 发布/打包前确认 `references/sites-private/` 不在产物中，尤其是会打包整个工作树的发布工具

完整流程、参数表与各引擎脚本行为见 [SKILL.md](anything-video-downloader/SKILL.md)。
