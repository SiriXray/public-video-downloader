# 取流台：端到端公开视频下载网页

这是一个本地 Web 应用。浏览器负责输入链接、选择清晰度、查看进度和保存成品；本机 Python 服务使用 `yt-dlp` 解析和下载，使用 `FFmpeg` 合并音视频、提取首帧和导出 MP3，使用 `faster-whisper` 在本地把音频转成带时间戳的文字稿。

仅用于你有权保存的公开内容。不要用于绕过 DRM、付费墙、会员权限、区域限制或其他访问控制。

## Windows 一键启动

双击 `start_web.cmd`。首次运行会创建 `.venv` 并安装 `yt-dlp`，随后自动打开：

```text
http://127.0.0.1:8877/
```

如果 `8877` 已被其他程序占用，脚本会自动使用一个系统分配的空闲端口，并打开对应页面。

抖音部分公开视频要求新鲜浏览器 Cookie。先在浏览器打开并确认视频可以播放，然后完全退出该浏览器，在 PowerShell 中设置 Cookie 来源后双击启动脚本：

```powershell
$env:YTDLP_COOKIES_FROM_BROWSER = "chrome"
& .\start_web.cmd
```

支持 `chrome`、`edge`、`firefox`、`brave`、`chromium`、`opera` 和 `vivaldi`。Cookie 只在本机读取；浏览器 Cookie 数据库被锁定或无法解密时，工作台会返回明确的重试提示。默认不读取 Cookie，B 站、YouTube 等不需要 Cookie 的来源不受影响。

如果端口被占用，可手动启动其他端口：

```powershell
cd C:\Users\26670\Documents\Codex\2026-07-01\codex-codex\public-video-downloader
.\.venv\Scripts\python web_app.py --port 8766 --open-browser
```

## 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python web_app.py --open-browser
```

下载 B 站常见的分离音视频流需要 `FFmpeg`。确认命令可用：

```powershell
ffmpeg -version
```

## 页面流程

1. 输入公开视频页面 URL 并解析。
2. 查看封面、来源、时长和可用格式。
3. 确认拥有保存权限，选择清晰度和文件容器。
4. 创建下载任务并查看实时进度。
5. 下载完成后点击“保存文件”。
6. 在任务队列中继续生成首帧 JPG、MP3 音频或 TXT/JSON 转写结果。
7. 研发视图可以选择格式 ID、输入 yt-dlp 格式表达式并导出带请求头的临时直链 JSON。

## 集成的开源工具

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：公开视频解析、格式选择和下载。
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：音视频合并、提取首帧、导出音频。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：基于 CTranslate2 的本地语音转文字，MIT License。

`faster-whisper` 首次使用某个模型时会从 Hugging Face 下载模型文件到项目的 `models` 目录。Windows 一键启动脚本默认使用 `hf-mirror.com` 镜像并禁用 Xet 下载；你可以在启动前设置自己的 `HF_ENDPOINT` 覆盖它。默认 `base` 模型使用 CPU `int8`，更大的模型通常更准确，但下载体积、内存和处理时间也会增加。

当前依赖约束使用 `PyAV < 19`，规避 faster-whisper 1.2.1 与 PyAV 19 已确认的解码 API 兼容问题。

语音方案对比中，[OpenAI Whisper](https://github.com/openai/whisper) 是官方 PyTorch 基线，[whisper.cpp](https://github.com/ggml-org/whisper.cpp) 适合进一步做原生桌面打包；当前工作台选择 faster-whisper，是因为它能直接接入现有 Python 服务，并兼顾 CPU 推理速度与实现复杂度。为控制普通电脑内存峰值，工作台一次只运行一个转写任务。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 检查 yt-dlp 和 FFmpeg |
| `POST` | `/api/inspect` | 解析页面元数据与格式 |
| `POST` | `/api/links` | 输出所选格式的临时媒体直链 |
| `POST` | `/api/downloads` | 创建下载任务 |
| `GET` | `/api/downloads` | 获取当前任务列表 |
| `GET` | `/api/downloads/{id}` | 获取单个任务状态 |
| `DELETE` | `/api/downloads/{id}` | 取消活动任务或删除已结束任务 |
| `GET` | `/api/downloads/{id}/file` | 下载成品，支持 HTTP Range |
| `POST` | `/api/downloads/{id}/artifacts/frame` | 生成首帧 JPG |
| `POST` | `/api/downloads/{id}/artifacts/audio` | 生成 MP3 音频 |
| `POST` | `/api/downloads/{id}/artifacts/transcript` | 本地生成带时间戳文字稿 |
| `GET` | `/api/downloads/{id}/artifacts/{kind}/file` | 下载派生文件，支持 HTTP Range |

请求示例：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' `
  -Uri 'http://127.0.0.1:8765/api/inspect' `
  -Body '{"url":"https://www.bilibili.com/video/BVxxxxxxxxxx"}'
```

## 安全边界

- 默认只监听 `127.0.0.1`，不会开放给局域网或公网。
- 输入 URL 只允许 HTTP/HTTPS 标准端口，并在解析前拒绝内网、环回、链路本地和保留 IP。
- 站点返回的标题、文件名和错误信息不会直接作为 HTML 注入页面。
- 下载目录限制在 `web-downloads/{task_id}` 内，文件接口会再次校验路径。
- 任务、进度和文件索引保存在内存中；服务重启后旧文件仍在磁盘，但不会自动恢复任务列表。

不要把当前服务直接绑定到公网。若要产品化部署，需要增加身份认证、用户隔离、配额、持久队列、恶意文件扫描、反向代理、HTTPS、域名白名单或受控出口代理，并对重定向后的目标地址继续执行 SSRF 防护。

## 验证

```powershell
python -m unittest discover -s tests -v
node --check web\app.js
python web_app.py --help
```

手动验收：

1. 打开网页，健康状态应显示 yt-dlp 版本。
2. 解析一个你有权访问的公开视频，封面、标题、时长和格式表应出现。
3. 输出直链 JSON，确认视频流、音频流与 `http_headers` 存在。
4. 下载低清晰度样例，任务应从下载中进入合并处理，最终出现“保存文件”。
5. 使用 `ffprobe` 检查成品同时包含视频和音频流。
6. 在任务队列分别生成首帧和 MP3，并验证下载文件可打开。
7. 选择 `Tiny` 或 `Base` 模型执行转写，确认 TXT 中包含语言、模型和时间戳。

## 已知边界

- 媒体直链通常带签名并会过期，不能作为长期 CDN 地址。
- 公开页面的高画质或抖音风控有时要求 Cookie；只有显式设置 `YTDLP_COOKIES_FROM_BROWSER` 时网页版本才读取本机浏览器 Cookie。
- yt-dlp 的站点解析规则需要持续升级和回归测试。
- 取消发生在下载进度回调处；FFmpeg 已开始合并时可能不会立即停止。
- 本地转写速度取决于 CPU、模型大小和视频时长；首次模型下载还取决于网络。
