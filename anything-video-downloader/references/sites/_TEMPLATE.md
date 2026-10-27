# 站点参考：<站点名>（<主域名>）

> 由实际下载流程沉淀（YYYY-MM-DD，<播放页示例> 分析 + <是否冒烟验证>）。遇到新站点时按本模板新建 `references/sites/<主域名>.md`。

## 匹配特征

- 播放页域名：`<主域名>`
- 播放页 URL 模式：`<如 /comic/index/detail?video_key={数字ID}>`
- 详情页 / 播放器接口：`<如有>`
- 片源 CDN：`<master / KEY / 分片域名，注明是否会变>`

## m3u8 提取方式

优先「浏览器打开播放页，直接读网络请求筛 `.m3u8`」——多数播放器会自动加载，不用点击、不用解 JS：

1. 浏览器打开播放页，等 Cloudflare 校验通过（若站点有防护）。
2. 在网络请求里筛 `.m3u8`，拿到带鉴权串的媒体列表地址；`.ts` / `crypt.key` 通常也会一并出现。

> 若无自动加载，再写具体兜底：页面 JS 变量名（如 `player_aaaa.url`）、packed JS 解包方式、或手工调播放器接口（含必需的现算时间戳等）。

### 校验

`curl.exe -s -L "<m3u8>" -H "Referer: <播放页>" -H "<Chrome UA>"`，应返回 `#EXTM3U`；若有加密应含 `#EXT-X-KEY`。

## 视频名称提取（输出文件命名）

- `document.title` 格式：`<原文格式>`
- **给 `make_job.py --strip` 用的尾缀字符串**：`<如 " - 抖音" / "_哔哩哔哩_bilibili"；以 -- 开头时用 --strip="--播放器" 等号形式>`
- 交叉确认字段：`<如 JSON-LD VideoObject.name / player_aaaa.vod_data>`

## 播放列表结构（已验证）

| 层级 | 地址 | 说明 |
|------|------|------|
| master | `<地址模式>` | <是否有多档画质；档位见「画质档位」> |
| media | `<地址模式>` | <分片数 / 时长等实测值> |
| 分片 | `<地址模式>` | <相对/绝对、是否需保留查询串> |

## 画质档位（已验证）

| 档位标签 | 传给脚本的值 | 分辨率 / 码率 | 备注 |
|----------|--------------|---------------|------|
| <如 1080P+> | `<如 1080p+>` | <如 1920x1080 / 5.8 Mbps> | <如 qn=112，需登录> |
| <如 1080P> | `<如 1080p>` | <如 1920x1080 / 3.2 Mbps> | <如 默认> |
| <如 720P> | `<如 720p>` | <如 1280x720 / 1.8 Mbps> |  |

- **默认画质**：`<脚本值>`（记录首次成功下载时用户实际选择的档位）
- 选择规则：`hls` 按分辨率高度精确匹配；`dash` 按 qn / 画质标签；`direct` 无多档（写「单一画质，无需选择」）
- 档位以本次 `curl` master 或接口响应的 `accept_quality` / `accept_description` 为准；站点改版后重测并更新本表
- 每次下载多档仍需询问用户，默认值只作推荐项，不静默沿用

## 加密与自定义标签

- `<无 #EXT-X-KEY> 或 <#EXT-X-KEY:METHOD=AES-128,...>；脚本对 AES-128 自动解密>`
- `<自定义标签如 #EXT-X-TOKEN / #EXT-X-DISCONTINUITY，注明是否需处理>`

## 请求头要求

- `Referer: <规则>` —— <是否必需、缺失的表现>
- `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36`
- <是否需要 Cookie / x-cdn-auth>

## 时效性

- <鉴权串/token 的有效期与过期现象；过期后从哪一步重提>

## 脚本调用示例

用 `make_job.py` 生成 job（尾缀见上），再执行它打印出的绝对路径命令：

```text
python "<技能根目录>/scripts/make_job.py" --page "<播放页 URL>" --m3u8 "<m3u8 URL>" --title "<document.title>" --strip "<站点尾缀>" --root "<下载目录>" --video-id <ID> --quality "<用户选择的画质>"
```

```text
python "<技能根目录>/scripts/start_hls_windows.py" --args-json "<下载目录>/_job.json"
```

多集用 `--batch <数组.json>`（每项 `{title,m3u8,page,episode,video_id}`）一次生成多个 job。

冒烟：`make_job.py` 加 `--max-segments 3`，确认合并产物首片 `0x47`（如加密）、时长合理即可。

不要再用 `Invoke-HlsDownload.ps1`（已废弃，只用 Python 脚本）。
