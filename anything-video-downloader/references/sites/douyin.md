# 站点参考：抖音（douyin.com）

> 由实际下载流程沉淀（2026-10-11，`https://www.douyin.com/video/7235504140267441447` 分析 + 全量下载验证）。抖音**没有 m3u8**：PC 端播放器已改用 webcodecs，网络请求里拿不到 mp4/HLS 直链；真实地址在 `<video>` 元素的 `currentSrc`。走 `scripts/direct_download.py`（job `engine=direct`，`url` 字段），**不要用 `hls_download.py` 硬套**。

## 匹配特征

- 播放页域名：`www.douyin.com`
- 播放页 URL 模式：
  - 视频页：`/video/{aweme_id}`（推荐，直接单视频）
  - 精选/搜索页：`/jingxuan/.../search/{关键词}?...&modal_id={aweme_id}&...`（modal 弹窗，可能不渲染 `<video>`，不如直接换成 `/video/{id}`）
- 真实媒体地址：`v*-weba.douyinvod.com/video/tos/.../{hash}/?...签名...`（域名/路径随视频变，以 `currentSrc` 为准）

## 提取方式（无 m3u8：读 `<video>.currentSrc`）

抖音 PC 端是 **webcodecs player**——视频不是简单 mp4 请求，网络请求里**没有**可直取的 `.mp4`/`.m3u8`（`douyinvod` 只出现在 dns-prefetch 和 captcha 排除配置里）。真实地址只挂在播放器元素上：

1. 用浏览器打开 `https://www.douyin.com/video/{aweme_id}`，等待播放器加载（几秒）。
2. 在页面上下文执行：
   ```js
   document.querySelector('video').currentSrc
   ```
   得到带签名的 mp4 直链（`mime_type=video_mp4`）。`video.src` 常为空（JS/webcodecs 设置），**读 `currentSrc`**。
   - 若 `videoCount=0`（例如从搜索 modal 进来没渲染播放器）→ 换成 `/video/{id}` 再取。
3. 校验可达（Range 探测）：
   ```text
   curl.exe -s -L -r 0-0 -D - -o NUL "<currentSrc>" -H "Referer: https://www.douyin.com/" -H "<Chrome UA>"
   ```
   应返回 `206` 且 `Content-Type: video/mp4`、`Content-Range: bytes 0-0/<总字节数>`。

`direct_download.py` 已封装「探测 → aria2 下载 → ffprobe 校验」；job 里放这条 `url`。

## 视频名称提取（输出文件命名）

- `document.title` 格式：`<描述文案> - 抖音`（如 `越南林志玲采耳按摩，美美的享受 - 抖音`）
- **给 `make_job.py --strip` 用的尾缀字符串**：`" - 抖音"`
- 交叉确认：`<meta name="description">` 的前缀同为描述文案；播放器区 `[data-e2e="detail-video-desc"]` / `h1` 是原始文案。

## 媒体结构（已验证）

| 层级 | 地址模式 | 说明 |
|------|------|------|
| 播放页 | `www.douyin.com/video/{aweme_id}` | 单视频；`aweme_id` 即 URL 里的数字 |
| 直链 | `v11-weba.douyinvod.com/video/tos/.../{hash}/?...签名...` | **单文件 mp4**（含视频+音频，非分片、无独立音轨）；实测 1024x576 HEVC + AAC，927s，56.9 MB |
| 参数 | `mime_type=video_mp4`、`br/bt`=码率、`l=`/`dy_q=`=签发时间 | 签名带时效，见下 |

- 单文件即可用，**不需要 ffmpeg 合并**（`direct_download.py` 下载后直接校验，无合并步骤）。

## 画质档位（已验证）

- **单一画质**：签名直链只对应播放器当前下发的那个 mp4，无多档可选；`quality` 不适用，无需询问用户。
- 实测样例：1024x576 HEVC + AAC，927s，56.9 MB。

## 加密与自定义标签

- 无 `#EXT-X-KEY` 概念：明文 mp4，无解密步骤。
- 源视频常见 **HEVC/H.265**（本机实测即 HEVC）。部分老播放器不支持；需要兼容性更好时自行转码 H.264（重编码，非无损封装）。

## 请求头要求

- `Referer: https://www.douyin.com/`（实测带上是稳妥做法；`direct_download.py` 用 job 的 `page` 当 Referer）
- `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36`（普通 Chrome UA 即可）
- 无需 Cookie（当前公开视频）。

## 时效性

- 直链的 `l=` / `dy_q=` 等签名参数带时效，**过期后 CDN 返回 403**。
- 与 B站不同：抖音**没有公开接口可重取直链**（需要 webcodecs 会话/签名），所以直链**必须烤进 job**，取到后尽快启动下载。
- 过期/失败后：回浏览器重新执行提取（步骤 2）拿新 `currentSrc`，重建 job 再下（或对同一 `out` 重跑，`direct_download.py` 支持断点续传）。

## 脚本调用示例

```text
python "<技能根目录>/scripts/make_job.py" --page "https://www.douyin.com/video/7235504140267441447" --url "<currentSrc 直链>" --title "示例视频 - 抖音" --strip " - 抖音" --root "<下载目录>"
```

```text
python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"
```

- 结果回读 `<out>.log`（`[完成]` / `[错误]` / 每 5 秒一条 `[进度]`）。
- 无 `--max-segments`（单文件直下）；`--quality` 不适用（画质由源直链决定）。
- 冒烟：先用「校验」里的 curl Range 探测确认直链可达即可。

不要再用 `Invoke-HlsDownload.ps1`（已废弃，只用 Python 脚本）。
