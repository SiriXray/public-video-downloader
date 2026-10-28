# B 站及多平台公开视频下载工具与 GitHub 调研

调研日期：2026-08-09。数据来自 GitHub 仓库 API、项目 README 和 Releases 页面；Star 数会继续变化。

本目录提供一个基于 `yt-dlp` 的本地命令行工具，支持：

- 下载你有权保存的公开视频；
- 列出可用画质、音轨和格式；
- 输出临时媒体直链；
- 输出包含请求头、格式和来源信息的 JSON，方便产品原型或研发验证。
- 通过 Web 工作台提取首帧 JPG、导出 MP3，并用 `faster-whisper` 在本地生成文字稿。

个人学习并不自动等于取得下载或再传播授权。请同时遵守内容版权、平台条款和所在地法律；不要用于绕过 DRM、付费墙、会员权限或其他访问控制。

## 先给结论

普通用户优先选择图形界面：只下载 B 站内容可看 `Bili23-Downloader`，需要 B 站、YouTube、HLS/m3u8 等多来源可看 `MediaGo`。希望批量处理 B 站投稿、番剧或课程，并能接受命令行，可选 `yutto`。

产品或研发优先研究 `yt-dlp`。它的站点覆盖、格式选择、Cookie、代理、元数据和直链输出能力更完整，适合作为“解析器层”或内部调研工具；媒体合并与转码交给 `FFmpeg`。不要把网页解析规则当成永久稳定 API，生产化时必须准备升级、回归测试和失败降级。

## GitHub 项目清单

| 项目 | 2026-08-09 快照 | 适用范围 | 许可证 | 选型判断 |
| --- | ---: | --- | --- | --- |
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 183,440 Star；最新发布 `2026.07.04`；未归档 | Bilibili 及数千站点；CLI/Python；下载、格式查询、直链输出 | Unlicense | 跨平台研发首选，也是本工具的内核 |
| [mediago-dev/mediago](https://github.com/mediago-dev/mediago) | 9,114 Star；最新发布 `v3.5.0`；未归档 | Windows/macOS/Linux GUI；Bilibili、YouTube、HLS/m3u8；嗅探与下载 | MIT | 多平台普通用户优先，适合研究桌面端产品形态 |
| [ScottSloan/Bili23-Downloader](https://github.com/ScottSloan/Bili23-Downloader) | 6,342 Star；最新发布 `v2.12.1`；未归档 | B 站 GUI；投稿、番剧、课程、空间、收藏夹等 | GPL-3.0 | B 站普通用户优先，功能和中文体验更聚焦 |
| [yutto-dev/yutto](https://github.com/yutto-dev/yutto) | 1,970 Star；最新发布 `v2.2.0`；未归档 | B 站 CLI；投稿、番剧、课程、单集及批量下载 | GPL-3.0 | B 站专项研发/批处理对照，要求 Python 3.11+ 与 FFmpeg |
| [soimort/you-get](https://github.com/soimort/you-get) | 56,877 Star；最新发布 `v0.4.1743`（2025-01-04）；未归档 | 多站点 CLI | 仓库 API 未给出 SPDX | 可做历史与兼容性对照，新项目不作为第一选择 |
| [iawia002/lux](https://github.com/iawia002/lux) | 31,599 Star；最新发布 `v0.24.1`（2024-05-06）；未归档 | Go CLI；Bilibili 与其他站点 | MIT | 单文件 Go 工具对照；正式采用前先验证目标站点回归情况 |
| [nilaoda/BBDown](https://github.com/nilaoda/BBDown) | 13,907 Star；GitHub 已标记归档 | B 站 CLI | MIT | 仅作历史方案研究，不建议作为新产品主依赖 |

项目的“未归档”不等于每个站点当前都可用。视频站会频繁调整页面、接口、签名和风控，实际选型必须用自己的目标 URL 集合做定期回归。

## 快速开始

环境要求：Python 3.10+。下载 B 站常见的分离音视频流时需要 `FFmpeg` 完成合并。

Windows PowerShell：

```powershell
cd C:\Users\26670\Documents\Codex\2026-07-01\codex-codex\public-video-downloader
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python video_tool.py --help
```

查看某个公开视频可用格式：

```powershell
.\.venv\Scripts\python video_tool.py formats "https://www.bilibili.com/video/BVxxxxxxxxxx"
```

下载当前视频到 `downloads`：

```powershell
.\.venv\Scripts\python video_tool.py download "https://www.bilibili.com/video/BVxxxxxxxxxx"
```

指定画质表达式、目录与分片并发数：

```powershell
.\.venv\Scripts\python video_tool.py download "视频页面 URL" -f "bv*+ba/b" -o D:\StudyVideos --concurrent-fragments 4
```

直接输出选中格式的媒体 URL，一行一个：

```powershell
.\.venv\Scripts\python video_tool.py links "视频页面 URL"
```

输出适合研发使用的 JSON，其中包含视频/音频类型、格式 ID、清晰度、临时 URL 和可能需要的 HTTP 请求头：

```powershell
.\.venv\Scripts\python video_tool.py links "视频页面 URL" --json --output links.json
```

部分公开页面的高画质会要求登录。只能读取你自己的浏览器登录态：

```powershell
.\.venv\Scripts\python video_tool.py download "视频页面 URL" --cookies-from-browser edge
```

网页工作台默认不读取浏览器 Cookie。抖音部分公开视频需要新鲜 Cookie 时，先在浏览器中打开并确认视频可播放，完全退出该浏览器，再在 PowerShell 中设置来源后双击 `start_web.cmd`：

```powershell
$env:YTDLP_COOKIES_FROM_BROWSER = "chrome"
& .\start_web.cmd
```

也可以将 `chrome` 改为 `edge`、`firefox`、`brave`、`chromium`、`opera` 或 `vivaldi`。Cookie 只由本机 `yt-dlp` 读取，不会写入网页接口响应。解析和下载期间应保持对应浏览器关闭；若出现 Cookie 数据库锁定，请关闭浏览器后重新启动工作台。

媒体直链通常带有签名和有效期，过期后必须重新解析。某些 CDN 还会校验 `Referer`、`User-Agent` 或 Cookie；研发接入时不要只保存 URL，应同时读取 JSON 中的 `http_headers`。这些链接不应被当作长期 CDN 地址或公开分享链接。

## 按角色选型

### C 端普通用户

1. 只想点选下载、主要使用 B 站：先试 [Bili23-Downloader Releases](https://github.com/ScottSloan/Bili23-Downloader/releases/latest)。
2. 来源很多、会遇到 m3u8/HLS：先试 [MediaGo Releases](https://github.com/mediago-dev/mediago/releases/latest)。
3. 想要透明、可脚本化且能看到格式：使用本目录的 `video_tool.py`。
4. 下载后没有声音或只有画面，通常是音视频分流尚未合并；安装 [FFmpeg](https://ffmpeg.org/download.html) 后重试。

### 产品 / 研发

建议把同类能力拆成六层评估：

1. URL 识别与提取：站点覆盖、页面类型、登录态、区域限制、更新速度。
2. 格式决策：清晰度、编码、HDR、音轨、字幕、弹幕、封面和元数据。
3. 下载执行：分片并发、断点续传、限速、重试、代理和任务取消。
4. 媒体处理：FFmpeg 合并、转封装、转码、字幕与封面写入。
5. 产品工程：队列、进度、失败原因、文件命名、去重、磁盘配额和升级机制。
6. 合规与安全：版权授权、平台条款、Cookie 本地保护、日志脱敏、直链和签名的生命周期。

推荐验证矩阵至少覆盖：B 站单 P/多 P、合集、公开视频、登录后高画质、AV1/HEVC/AVC、独立音轨、字幕、失效链接、限流、网络中断和磁盘空间不足。对外产品还需要逐站点完成法务和平台条款审查，不能只依据“技术上可以解析”。

## 行业与实现资料

- [yt-dlp README 与嵌入方式](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp)：CLI、Python API、格式选择、输出模板、Cookie 等官方说明。
- [yt-dlp 支持站点列表](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)：可核验 Bilibili、Bangumi、Cheese、收藏夹、稍后再看、直播等 extractor。
- [FFmpeg 官方文档](https://ffmpeg.org/documentation.html)：音视频合并、转封装和转码基础。
- [RFC 8216: HTTP Live Streaming](https://www.rfc-editor.org/rfc/rfc8216)：HLS/m3u8 协议资料。
- [DASH Industry Forum Guidelines](https://dashif.org/guidelines/)：MPEG-DASH 生态与互操作资料。
- [MediaGo 文档](https://downloader.caorushizi.cn/en/documents.html?form=github)：桌面端、API、嗅探与任务管理产品形态参考。
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)：已归档，只适合做历史接口研究，不应视为官方或稳定 API。

## 验证命令

```powershell
python -m unittest discover -s tests -v
python video_tool.py --help
python video_tool.py formats "一个你有权访问的公开视频 URL"
python video_tool.py links "一个你有权访问的公开视频 URL" --json
python video_tool.py download "一个你有权保存的公开视频 URL" -o .\downloads
```

如果解析突然失败，先升级 `yt-dlp` 后复测：

```powershell
python -m pip install -U yt-dlp
```
