# 环境准备：依赖与浏览器工具

本技能要跑通「取地址 → 下载 → 合并 → 校验」，需要几项外部工具。**首次使用前检查一次即可**：通过后脚本会在技能根目录写 `.deps-ok.json`，之后启动器与下载脚本自动跳过预检；只有某次运行中发现缺工具时，才会清标记并自动做一次全量检查。

## 依赖矩阵

| 引擎 | aria2c | ffmpeg | ffprobe | curl | cryptography |
|------|--------|--------|---------|------|--------------|
| `hls`（默认，m3u8） | 必需 | 必需 | 必需 | 必需 | 仅 AES-128 源需要 |
| `dash`（B站双流） | 必需 | 必需 | 必需 | 必需 | 不需要 |
| `direct`（直链 mp4） | 必需 | 不需要 | 必需 | 必需 | 不需要 |

各自的作用：

- **aria2c**：并发下载分片/双流，支持断点续传与 `--continue`。
- **ffmpeg**：把 TS 分片或 B站音视频双流**无损**合并成 mp4（不重新编码）。
- **ffprobe**：合并后校验时长、文件大小与音视频流是否齐全。
- **curl**：拉取 master/media 播放列表、AES-128 的 KEY，以及直链 Range 探测。Windows 10/11 自带 `curl.exe`。
- **cryptography**：AES-128 分片解密用，只在源启用加密时才需要。

> 脚本当前默认面向 Windows（调用 `curl.exe`、用新控制台跑下载）。非 Windows 平台需把脚本里的 `curl.exe` 改成 `curl`。

## 一键检查（首次使用前一次即可）

在技能根目录（`SKILL.md` 所在目录）执行：

```text
python scripts/check_deps.py
```

省略 `--engine` 会检查三个引擎的并集；只看某个引擎：

```text
python scripts/check_deps.py --engine direct
python scripts/check_deps.py --engine hls --cryptography
```

输出会逐项标注 `[OK]` / `[缺失]`，缺失时附上对应平台的安装命令。退出码：`0` 全部必需项就绪，`2` 有缺失。

检查通过后，脚本把结果写入技能根目录的 `.deps-ok.json`（已在 `.gitignore` 中）。此后启动器与下载脚本直接跳过预检，不必每次再跑。只有这些情况会重新检查：

- 某次下载运行中报缺少外部工具：脚本会删除标记，自动做一次全引擎完整检查并给出安装命令。安装会改动系统，执行前先征得用户同意，装完重开终端（刷新 PATH）再重试。
- 手动要求重查：`python scripts/check_deps.py --force`
- 想清除「已通过」记录：`python scripts/check_deps.py --forget`

不确定当前目录时用绝对路径，例如：

```text
python "<技能根目录>/scripts/check_deps.py" --engine hls
```

## Windows 安装

推荐 winget（包 ID 已在本地核实）：

```text
winget install --id Gyan.FFmpeg -e
winget install --id aria2.aria2 -e
```

备选包管理器（任选其一）：

```text
choco install ffmpeg aria2 -y
scoop install ffmpeg aria2
```

装完**重开终端**（刷新 PATH），再验证：

```text
ffmpeg -version
ffprobe -version
aria2c --version
```

`curl.exe` 为 Windows 10/11 自带；若缺失，从 <https://curl.se/windows/> 下载解压后把所在目录加入 PATH。

## macOS / Linux

```text
# macOS
brew install ffmpeg aria2

# Debian / Ubuntu
sudo apt install ffmpeg aria2

# Fedora
sudo dnf install ffmpeg aria2

# Arch
sudo pacman -S ffmpeg aria2
```

Linux/macOS 还要把 `scripts/*.py` 里的 `curl.exe` 换成 `curl`（或给 `curl.exe` 建个别名）。

## Python 依赖

Python 3.8+ 即可，无需额外安装。只有下载 **AES-128 加密**的 HLS 源时需要：

```text
python -m pip install cryptography
```

未安装时：明文源正常下载；加密源会在解密阶段明确报错并提示这条安装命令。

## 浏览器访问工具（推荐 chrome-devtools MCP）

### 为什么必须有

站点普遍有 Cloudflare 防护，命令行直连播放页会被拦。实际流程需要浏览器：

- 过校验后**读网络请求**筛 `.m3u8`（首选，也最容易拿到带鉴权的完整地址）；
- 对直链站点读 `document.querySelector('video').currentSrc`；
- 必要时执行 `evaluate_script` 读播放器配置变量或 `performance.getEntriesByType('resource')`。

浏览器只负责**取地址**，下载和合成都由命令行脚本完成。

### 前置条件

- Node.js 20.19+（或用最新 LTS）与 npm；
- Chrome 当前稳定版。

### 配置

在支持 `mcpServers` 的 MCP 客户端里加入：

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    }
  }
}
```

### 验证

```text
npx chrome-devtools-mcp@latest --help
```

能正常输出帮助、且客户端里能看到 `navigate_page` / `list_network_requests` / `evaluate_script` 等工具，即安装成功。

### 实操要点

- 用**有头模式**：headless 更容易被 Cloudflare 判定为自动化而拦下。
- 优先「打开播放页 → 等校验与播放器加载 → 读网络请求筛 `.m3u8`」，不要一上来就解混淆 JS。
- 取到地址后先用 `curl.exe` 打一次 CDN 确认可达，再生成 job。
- 抖音这类站点的签名直链会过期，取到后尽快启动下载。

### 没有浏览器工具怎么办

- 任何能读页面网络请求、能执行脚本的浏览器自动化 MCP（Playwright / Puppeteer / agent-browser 等）都可以替代。
- 完全没有浏览器工具时：**B站 DASH 可匿名直取**（`--bvid` / `--ep-id`，不需要浏览器）；其余站点请用户在自己的浏览器里打开播放页，从开发者工具的 Network 面板复制 `.m3u8` 地址（或 `<video>.currentSrc`）贴回来，再走 `make_job.py`。

### 常见问题

- MCP 客户端与终端用的 node/npm 版本不一致 → 在客户端配置里用绝对路径的 `npx`，或在同一 Node 环境下重装。
- `npx` 首次运行会询问是否安装 → 配置里已加 `-y` 自动确认。
- 打开页面后网络请求里没有 `.m3u8` → 先点一下播放；仍没有就按站点文档（`sites/<域名>.md`）的兜底写法处理。

## 依赖常见问题

- **明明装过仍提示缺失**：PATH 未刷新，或装到了别的用户目录。重开终端后用 `python scripts/check_deps.py --force` 复核；Windows 再确认 `curl.exe --version` 可用。
- **想强制重查**：加 `--force`；**想忘掉「已通过」记录**：`python scripts/check_deps.py --forget`。
- **标记写不进去**（技能目录只读）：只在 stderr 警告，不影响下载，只是每次仍会检查。

## 安装后自检清单

1. 首次安装后跑 `python scripts/check_deps.py --force`，退出码为 `0`（日常无需重复跑）。
2. `ffmpeg -version`、`aria2c --version` 都有输出。
3. 浏览器 MCP 能打开一个播放页并列出网络请求。
4. 用 `make_job.py --max-segments 3` 做一次冒烟下载，日志出现 `[完成]`。
