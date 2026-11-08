# 站点参考：YouTube（youtube.com）

> 由实际下载流程沉淀（2026-11-08，`watch?v=Qk52ypnGs68` T-ARA《NUMBER NINE》MV 实测下载 + ffprobe 校验通过，1080p/233.85s/112MB）。**YouTube 不走 `make_job.py` / `start_hls_windows.py` 流程**，用 yt-dlp，见下。

## 匹配特征

- 播放页域名：`youtube.com`（含 `m.youtube.com`、`youtu.be/<ID>`）
- 播放页 URL 模式：`/watch?v=<视频ID>`，常带 `&list=<播放列表ID>&index=<序号>`
- 片源 CDN：`rr*---sn-*.googlevideo.com/videoplayback`（主机名每次变）

## 为什么技能的三个引擎都不适用（已实测）

| 引擎 | 结论 |
|------|------|
| `hls` | 页面与 `streamingData` 都**没有 `.m3u8`**（`hlsManifestUrl` 字段不存在），排除 |
| `dash` | `bilibili_download.py` 硬编码 B站 playview/playurl 接口，不通用；且 YouTube 的 `adaptiveFormats` 每条 `url` 都是**空字符串**，也没有 `signatureCipher`，拿不到可直接请求的地址 |
| `direct` | 没有合并音视频的直链档（`formats` 数组为空）；实际媒体走 **UMP 协议**：`POST https://rr*---sn-*.googlevideo.com/videoplayback`，请求体是二进制 protobuf，响应 `content-type: application/vnd.yt-ump`，**不是单个 mp4 文件**，Range 探测 + aria2 单文件那套走不通 |

手工调 innertube `youtubei/v1/player` 也不行：`WEB` 客户端 200 但无 url；`ANDROID` / `IOS` 直接 400 `Precondition check failed`；`TVHTML5_SIMPLY_EMBEDDED_PLAYER` / `WEB_EMBEDDED_PLAYER` 200 但 0 条格式。

**结论：YouTube 用 yt-dlp**（它专门处理签名 / UMP / 各客户端），下载后的合并与校验仍复用技能自带的 `ffmpeg` / `ffprobe`。

## 依赖（额外，非三引擎依赖表内）

- **yt-dlp**：本机无 `pip`，用 uv 装（不需管理员）：
  ```text
  uv tool install yt-dlp
  ```
  装完可执行文件在 uv 的 tool bin 目录，直接用 `yt-dlp` 调用。
- **代理（本环境必需）**：沙箱直连 `www.youtube.com` / `i.ytimg.com` / `*.googlevideo.com` 全部被重置或超时（DNS 返回假 IP `185.45.5.35`）。本机有系统代理，实测可用：
  ```text
  curl.exe -sS -x http://127.0.0.1:7890 -o NUL -w "%{http_code}" https://www.youtube.com/
  ```
  200 即通。所有 yt-dlp 命令都要带 `--proxy http://127.0.0.1:7890`（`7897` 也通，取系统设置里那个）。换机器先 `netsh winhttp show proxy` 与注册表 `Internet Settings.ProxyServer` 确认端口。

## 提取方式

不需要浏览器、不需要读网络请求，yt-dlp 一条龙：

1. 先列格式确认可用档位（**加 `--no-playlist`**，否则 `watch?v=..&list=..` 会下整张播放列表）：
   ```text
   yt-dlp --proxy http://127.0.0.1:7890 --no-playlist -F "<watch URL>"
   ```
2. 默认客户端会撞 **`Sign in to confirm you're not a bot`**（`visionos` / `android` 客户端实测触发）。换 `tv_embedded` 客户端可绕开，实测拿到 1080p `https` 直链：
   ```text
   yt-dlp --proxy http://127.0.0.1:7890 --no-playlist --extractor-args "youtube:player_client=tv_embedded" ...
   ```
   仍报 bot check 时再轮换 `web` / `tv_embedded` / `android_vr`（`android_vr` 的 https 格式需 GVS PO Token，会 403，优先前两个）。
3. 无 JS 运行时警告（`No supported JavaScript runtime could be found`）可忽略：本机没装 deno，`tv_embedded` 仍给出完整 `https` 格式；要消警告可 `--js-runtimes node`（有 node 时）。

## 视频名称提取（输出文件命名）

- `document.title` 格式：`<标题> - YouTube`，尾缀固定 ` - YouTube`
- 但**不建议走 `make_job.py` 命名**：YouTube 用 yt-dlp 的 `-o` 模板更省事，如 `-o "<下载目录>/T-ARA_NUMBER NINE_MV.%(ext)s"`
- yt-dlp 自己的 `--print title` / `%(title)s` 模板可拿到原名；含 `/` 等非法字符时手工指定基名

## 画质档位（已验证，`-F` 实测）

| 档位标签 | itag | 分辨率 / 码率 | 备注 |
|----------|------|---------------|------|
| 1080p | `137` | 1920x1080 / 3889k，108.38MiB | avc1.640028，mp4_dash，**本次默认** |
| 720p | `136` | 1280x720 / 1187k，33.08MiB | avc1.4D401F |
| 480p | `135` | 854x480 / 759k，21.14MiB |  |
| 360p | `134` | 640x360 / 474k，13.21MiB |  |
| 240p | `133` | 426x240 / 218k，6.08MiB |  |
| 144p | `160` | 256x144 / 100k，2.78MiB |  |
| 音频 | `140` | m4a 130k，3.61MiB | mp4a.40.2，与 mp4 视频合并最省事 |
| 音频 | `251` | webm opus 127k，3.55MiB | vp9 视频配它更小 |

- 同分辨率还有 vp9（`248`/`247`/…）与 av1（`399`/`398`/…）档，文件更小但需播放器支持；要通用兼容就选 avc。
- **默认画质：1080p**（记录首次成功下载时用户实际选择）。每次仍要问用户，默认值只作推荐。
- 没有「视频+音频合一」的档位，必须选 `bv*+ba` 让 yt-dlp 合并。

## 请求头要求

- yt-dlp 自带 UA 与必需头，**不需要 Referer**、不需要 Cookie（撞 bot check 换 client，不要上 Cookie 绕过）
- 走 `--proxy`，不带代理时所有域名都连不上

## 时效性

- 播放地址数小时过期，重跑即可（yt-dlp 每次现取）
- bot check 是随机风控，不是过期：换 `player_client` 重试，或等几分钟再跑

## 脚本调用示例（已验证）

```text
yt-dlp --proxy http://127.0.0.1:7890 --no-playlist --extractor-args "youtube:player_client=tv_embedded" -f "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b" --merge-output-format mp4 --newline -o "C:/Users/cnfox/Downloads/video-downloader/T-ARA_NUMBER NINE_MV.%(ext)s" "https://www.youtube.com/watch?v=Qk52ypnGs68"
```

实测输出：视频 `f137.mp4`（108.38MiB，17s @ 6MiB/s）+ 音频 `f140.m4a`（3.61MiB）→ `[Merger] Merging formats into ".../T-ARA_NUMBER NINE_MV.mp4"`，自动删掉中间文件。

校验：

```text
ffprobe -v error -show_entries format=duration,size -show_entries stream=codec_type,codec_name,width,height,channels -of default=noprint_wrappers=1 "<输出 mp4>"
```

实测 `duration=233.848163`（与 `videoDetails.lengthSeconds=234` 一致）、`h264 1920x1080` + `aac 2ch`。

**多集/播放列表**：去掉 `--no-playlist`，用 `-o "<下载目录>/%(playlist_index)02d - %(title)s.%(ext)s"`；同一张单据建议逐个下，不要并发多开 yt-dlp（易触发风控）。
