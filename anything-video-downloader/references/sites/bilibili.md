# 站点参考：哔哩哔哩（bilibili.com）

> 由实际下载流程沉淀（2026-10-08，`https://www.bilibili.com/cheese/play/ep720220` 分析 + 1080P+ 全量下载验证）。B 站**没有 m3u8**：视频/音频是 DASH 双流（整段 fMP4 `.m4s`），走 `scripts/bilibili_download.py`（job `engine=dash`），**不要用 `hls_download.py` 硬套**。

## 匹配特征

- 播放页域名：`www.bilibili.com`
- 播放页 URL 模式：
  - 课程（cheese / pugv）：`/cheese/play/ep{epid}` → 取 `ep_id`
  - 普通视频：`/video/BV{bvid}` 或 `/video/av{aid}` → 取 `bvid` / `aid`
- 详情页 / 播放器接口（均匿名可用，无需浏览器）：
  - 课程分集信息：`https://api.bilibili.com/pugv/view/web/season?ep_id={epid}` → `data.title`（课程名）、`data.episodes[].title`（如 `第01集【时间】消失吧，拖延症！`）、`data.episodes[].cid`
  - 课程直链：`https://api.bilibili.com/ogv/player/playview?biz_type=pugv&ep_id={epid}&qn={qn}&fnver=0&fnval=4048&app_id=100` → `data.video_info.dash`
  - 普通视频 cid：`https://api.bilibili.com/x/web-interface/view?bvid={bvid}` → `data.aid/cid/title`
  - 普通视频直链：`https://api.bilibili.com/x/player/playurl?avid={aid}&cid={cid}&qn={qn}&fnver=0&fnval=4048&fourk=1` → `data.dash`
- 片源 CDN：`upos-*.bilivideo.com`（`upos-sz-estgcos` / `upos-sz-mirrorcos` / `upos-sz-mirrorbd` 等随请求轮换），路径 `/upgcxcode/{cid后2位}/{cid次2位}/{cid}/{cid}-1-{分段id}.m4s?签名参数`

## 提取方式（无 m3u8：直链接口）

**B 站不提供 HLS**——pugv playview 对 `fnval=1/4` 直接返回 `-400`，只认 `fnval=4048`（纯 DASH）。免费内容全流程不需要浏览器、不需要登录：

1. 从播放页 URL 取标识：cheese → `ep_id`；普通视频 → `bvid`（view 接口换 `aid`+`cid`）。
2. 调上面「播放器接口」拿 `dash` 结构：`video[]`（`id`=qn、`codecs`、`base_url`、`backup_url`、`bandwidth`）+ `audio[]`（`id` 30280/30232/30216）。
3. 画质档位（qn）：`112`=1080P+高码率、`80`=1080P、`64`=720P、`32`=480P、`16`=360P；请求 `qn=0` 返回服务器最高档（`accept_quality` 给完整列表）。
   - `support_formats` 对 112/80/64 标 `need_login:true`，但**游客实测照样给可用直链**（免费集）。
4. 选流：视频 = 请求 qn 匹配 + `codecs` 前缀（`avc1`/`hev1`/`av01`）；音频 = `bandwidth` 最大者（`30280`，192k AAC）。
5. **加密：无**（明文 fMP4，首盒 `ftyp`）。
6. `bilibili_download.py` 已封装以上全部（`--ep-id/--bvid/--aid --quality --codec`），**运行时现取直链**。

### 校验

```text
curl.exe -s -L -r 0-0 -D - -o NUL "<base_url>" -H "Referer: <播放页>" -H "<Chrome UA>"
```

应返回 `206` 且 `Content-Range: bytes 0-0/<总字节数>`；响应首字节 `00 00 00 24 66 74 79 70`（ftyp）。返回 403/404 = 直链过期，重新调接口即可（脚本每次运行都会重取）。

## 视频名称提取（输出文件命名）

- cheese 播放页 `document.title` 格式：`<课程名>_哔哩哔哩_bilibili`（**是课程名不是集名**，如 `土豆逗严肃科普：消失吧，拖延症！_哔哩哔哩_bilibili`）
- **给 `make_job.py --strip` 用的尾缀字符串**：`_哔哩哔哩_bilibili`
- 集名在 season 接口 `data.episodes[].title`；多集下载用 `--episode N` 生成 `<名称>_第NN集.mp4`
- 普通视频 `document.title` 同样带 `_哔哩哔哩_bilibili` 后缀

## 播放列表结构（已验证）

| 层级 | 地址模式 | 说明 |
|------|------|------|
| 接口 | `api.bilibili.com/ogv/player/playview?...&fnval=4048` | 无 master/media 层级；`qn=0` 给最高档 |
| 视频流 | `upos-*.bilivideo.com/upgcxcode/.../{cid}-1-{segid}.m4s?签名` | **整段单文件**（非分片）；1080P+ avc 实测 155,747,307 字节 |
| 音频流 | 同上 `.../{cid}-1-30280.m4s?签名` | 整段单文件 13,656,240 字节（192k AAC） |

- 分段 id 规律：avc `30032/30064/30080/30112`（480/720/1080/1080+），hev `30033/30066/30077/30102`，音频 `30280/30232/30216`
- 两条流都是 fMP4（ftyp 开头），`ffmpeg -i video.m4s -i audio.m4s -c copy` 直接无损合并

## 画质档位（已验证）

| 档位标签 | 传给脚本的值 | 分辨率 / 码率 | 备注 |
|----------|--------------|---------------|------|
| 1080P+ | `1080p+` | 1920x1080 高码率 | qn=112；avc 分段 30112；**默认** |
| 1080P | `1080p` | 1920x1080 | qn=80；avc 30080 / hev 30077 |
| 720P | `720p` | 1280x720 | qn=64；avc 30064 / hev 30066 |
| 480P | `480p` | 854x480 | qn=32；avc 30032 |
| 360P | `360p` | 640x360 | qn=16 |

- **默认画质**：`1080p+`（记录首次全量下载时用户实际选择的档位）
- 选择规则：`dash` 按 qn / 画质标签；多档每次询问用户，默认项只作推荐
- `--codec` 与档位正交：`avc` 兼容性最好（默认）、`hev` 同画质体积约减半、`av1` 取决于片源
- 112/80 的 `support_formats` 标 `need_login:true`，但游客实测免费集仍给可用直链；付费内容仍以账号权限为准

## 加密与自定义标签

- 无 `#EXT-X-KEY` 概念：DASH 明文直链，无解密步骤。

## 请求头要求

- `Referer: <播放页 URL>`（实测缺失也能下，保持带上）
- `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36`
- 接口匿名可用；**仅限你本人已登录且已购买/有权访问的内容**：把浏览器 Cookie（SESSDATA 等）放进 job 的 `extra_headers`：`"extra_headers": ["Cookie: SESSDATA=...; bili_jct=..."]`，脚本会带到接口请求与 aria2 下载。

## 时效性

- `base_url` 带 `deadline` 参数 ≈ 签发后 **2 小时**过期，过期后 CDN 返回 403。
- **不要把直链烤进 job**——job 只存 `ep_id/bvid/aid`，`bilibili_download.py` 每次运行（含断点续传重跑）现调接口刷新直链。
- aria2 中断后重跑同一命令：已完成的流跳过，只续传缺的那条。

## 付费 / 试看判定（下单前先查，别等脚本报错）

B站课程（cheese）多为付费，**先查分集是否解锁再决定下不下**：

- season 接口 `data.episodes[i]`：`playable: true/false`；免费试看集还带 `label: "全集试看"`（实测第01集 free），付费集 `playable: false`、`ep_status: 2`
- season 接口 `data.payment`：`price` / `price_unit`（如 `18 B币`）、`pay_shade`；`data.user_status.has_paid`
- playview 付费拦截的表现：`data.play_video_type == "none"`、**无 `dash`**，`data.not_playable_reason = {code: 6002102, message: "付费开启全片"}`，`user_status: {is_login, has_paid}` → 脚本会报 `playview 没有 dash（...需要登录/大会员）`
- **已购买、本人有权访问**时：把该账号浏览器 Cookie（`SESSDATA` 等）放进 job 的 `extra_headers`，接口即返回 dash；**未购买无论怎么调都取不到流**（不是技术问题，别绕）

## 脚本调用示例

```text
python "<技能根目录>/scripts/make_job.py" --page "https://www.bilibili.com/cheese/play/ep720220" --ep-id 720220 --quality "1080p+" --codec avc --title "土豆逗严肃科普：消失吧，拖延症！_哔哩哔哩_bilibili" --strip "_哔哩哔哩_bilibili" --root "<下载目录>" --episode 1
```

```text
python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"
```

- `--quality`：`highest`（服务器最高档）/ `1080p+`(qn112) / `1080p`(80) / `720p`(64) / `480p`(32) / `360p`(16) / qn 数字（档位见「画质档位」；多档每次询问用户，默认项标推荐）
- `--codec`：`avc`（默认，兼容性最好）/ `hev`（同画质体积约减半）/ `av1`
- 普通视频：`--bvid BV1xx...`（或 `--aid 123`）代替 `--ep-id`
- 多集批量：`--batch` 每项 `{title, page, ep_id, episode, video_id?}`，共享 `--strip/--quality/--codec`
- 结果回读 `<out>.log`（`[完成]` / `[错误]` / 每 5 秒一条 `[进度]`）；下载中进度另看 workdir 里 `video.m4s`、`audio.m4s` 字节数
- 冒烟：没有 `--max-segments`（DASH 只有 2 个文件，单集 ~160MB 直接全量即可）；先用「校验」里的 curl Range 探测确认直链可达

不要再用 `Invoke-HlsDownload.ps1`（已废弃，只用 Python 脚本）。
