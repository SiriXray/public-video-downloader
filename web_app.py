#!/usr/bin/env python3
"""Local web UI for downloading authorized public videos with yt-dlp."""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from video_tool import collect_direct_items, load_yt_dlp


PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "web"
DOWNLOAD_ROOT = PROJECT_DIR / "web-downloads"
MAX_REQUEST_BYTES = 64 * 1024
MAX_ACTIVE_TASKS = 4
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MEDIA_EXTENSIONS = {
    ".3gp",
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}

QUALITY_PRESETS = {
    "best": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]",
    "audio": "ba/b",
}
X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "m.twitter.com"}
DOUYIN_HOSTS = {"douyin.com", "iesdouyin.com"}
COOKIE_BROWSERS = {
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "vivaldi",
}

ARTIFACT_KINDS = {"frame", "audio", "transcript"}
TRANSCRIPTION_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
TRANSCRIPTION_LANGUAGES = {
    "auto": None,
    "zh": "zh",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
}
WHISPER_MODEL_ROOT = PROJECT_DIR / "models"


class AppError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = int(status)


class DownloadCancelled(Exception):
    pass


class ValidatingRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        validate_public_url(new_url)
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


def clean_error(value: Any) -> str:
    text = ANSI_ESCAPE.sub("", str(value)).strip()
    return text[-1200:] if len(text) > 1200 else text


def source_label(url: str) -> str | None:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    if hostname in X_HOSTS or any(hostname.endswith(f".{domain}") for domain in X_HOSTS):
        return "X / Twitter"
    if hostname in DOUYIN_HOSTS or any(
        hostname.endswith(f".{domain}") for domain in DOUYIN_HOSTS
    ):
        return "抖音"
    return None


def validate_cookie_browser(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    browser = str(value).strip().lower()
    if browser not in COOKIE_BROWSERS:
        choices = "、".join(sorted(COOKIE_BROWSERS))
        raise AppError(f"不支持的浏览器 Cookie 来源，可选: {choices}")
    return browser


def cookie_options(browser: str | None) -> dict[str, Any]:
    safe_browser = validate_cookie_browser(browser)
    return {"cookiesfrombrowser": (safe_browser,)} if safe_browser else {}


def alternate_browser_executable(cookie_browser: str | None) -> Path | None:
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    if cookie_browser == "chrome":
        candidates.extend(
            [
                Path(os.environ.get("PROGRAMFILES", ""))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", ""))
                / "Microsoft/Edge/Application/msedge.exe",
            ]
        )
    elif cookie_browser == "edge":
        candidates.extend(
            [
                Path(os.environ.get("PROGRAMFILES", ""))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", ""))
                / "Google/Chrome/Application/chrome.exe",
            ]
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def friendly_source_error(url: str, error: Any) -> str:
    message = clean_error(error)
    lowered = message.lower()
    if source_label(url) == "抖音":
        if "fresh cookies" in lowered or "cookies are needed" in lowered:
            return (
                "抖音要求新鲜浏览器 Cookie。请先在浏览器打开该视频并确认可以正常播放，"
                "关闭对应浏览器后，再设置 YTDLP_COOKIES_FROM_BROWSER=chrome（或 edge）重启工作台。"
                "Cookie 只在本机读取，不会显示在页面或接口响应中。"
            )
        if "could not copy" in lowered and "cookie database" in lowered:
            return (
                "无法读取浏览器 Cookie 数据库。请完全退出对应浏览器后再重启工作台，"
                "并确认 YTDLP_COOKIES_FROM_BROWSER 与实际浏览器一致。"
            )
        if "failed to decrypt" in lowered or "decrypt" in lowered:
            return (
                "浏览器 Cookie 解密失败。请使用当前 Windows 用户启动工作台，"
                "或在浏览器关闭后改用 YTDLP_COOKIES_FROM_BROWSER=chrome/edge 重试。"
            )
    if source_label(url) == "X / Twitter":
        if any(
            marker in lowered
            for marker in (
                "timed out",
                "timeout",
                "unexpected_eof",
                "connection reset",
                "connection refused",
                "sslerror",
                "10060",
                "连接尝试失败",
                "连接的主机没有反应",
            )
        ):
            return "X/Twitter 页面暂时无法连接，请检查本机网络或稍后重试。"
        if "login" in lowered or "authorized" in lowered or "authentication" in lowered:
            return "X/Twitter 要求登录或该内容不可公开访问，请使用无需登录的公开视频链接。"
    return message


def validate_public_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("请输入公开视频页面地址")
    url = value.strip()
    if len(url) > 2048:
        raise AppError("链接过长")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("只接受完整的 http:// 或 https:// 公网页面地址")
    if parsed.username or parsed.password:
        raise AppError("链接不能包含用户名或密码")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AppError("链接端口无效") from exc
    if port not in {None, 80, 443}:
        raise AppError("只允许标准 HTTP/HTTPS 端口")

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise AppError("无法解析链接域名") from exc
    if not addresses:
        raise AppError("链接域名没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise AppError("拒绝访问内网、环回或保留地址")
    return url


def validate_format_selector(body: dict[str, Any]) -> str:
    custom = body.get("format")
    if custom is not None:
        if not isinstance(custom, str) or not custom.strip():
            raise AppError("格式选择表达式不能为空")
        selector = custom.strip()
        if len(selector) > 240 or any(ord(char) < 32 for char in selector):
            raise AppError("格式选择表达式无效")
        return selector
    quality = body.get("quality", "best")
    try:
        return QUALITY_PRESETS[str(quality)]
    except KeyError as exc:
        raise AppError("未知清晰度选项") from exc


def validate_merge_format(value: Any) -> str | None:
    if value in {None, "", "auto"}:
        return None
    if value not in {"mp4", "mkv", "webm"}:
        raise AppError("合并容器只支持 mp4、mkv 或 webm")
    return str(value)


def validate_transcription_options(body: dict[str, Any]) -> tuple[str, str | None]:
    model = str(body.get("model") or "base")
    if model not in TRANSCRIPTION_MODELS:
        raise AppError("未知的语音转写模型")
    language_name = str(body.get("language") or "auto")
    if language_name not in TRANSCRIPTION_LANGUAGES:
        raise AppError("未知的转写语言选项")
    return model, TRANSCRIPTION_LANGUAGES[language_name]


def run_ffmpeg(arguments: list[str]) -> None:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise AppError("未安装 FFmpeg，无法执行媒体转换", HTTPStatus.SERVICE_UNAVAILABLE)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-y", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(clean_error(result.stderr or "FFmpeg 处理失败"))


def transcript_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def first_entry(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("entries")
    if entries:
        for entry in entries:
            if entry:
                return first_entry(entry)
        raise AppError("页面中没有可下载的视频")
    return info


def media_kind(item: dict[str, Any]) -> str:
    video = item.get("vcodec") not in {None, "none"}
    audio = item.get("acodec") not in {None, "none"}
    if video and audio:
        return "video+audio"
    if video:
        return "video"
    if audio:
        return "audio"
    return "unknown"


def display_resolution(item: dict[str, Any]) -> str:
    if item.get("resolution") and item["resolution"] != "audio only":
        return str(item["resolution"])
    width, height = item.get("width"), item.get("height")
    if width and height:
        return f"{width}x{height}"
    if item.get("abr"):
        return f"{round(float(item['abr']))} kbps"
    if media_kind(item) == "audio":
        return "纯音频"
    return "-"


def compact_format(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("format_id"),
        "ext": item.get("ext"),
        "kind": media_kind(item),
        "resolution": display_resolution(item),
        "height": item.get("height"),
        "fps": item.get("fps"),
        "vcodec": item.get("vcodec"),
        "acodec": item.get("acodec"),
        "filesize": item.get("filesize") or item.get("filesize_approx"),
        "tbr": item.get("tbr"),
        "note": item.get("format_note"),
    }


def duration_text(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "-"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_info(
    url: str,
    format_selector: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    yt_dlp = load_yt_dlp()
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
    }
    options.update(cookie_options(cookies_from_browser))
    if format_selector:
        options["format"] = format_selector
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            return downloader.extract_info(url, download=False)
    except Exception as exc:
        raise AppError(
            friendly_source_error(url, exc),
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from exc


def inspect_video(url: str, cookies_from_browser: str | None = None) -> dict[str, Any]:
    safe_url = validate_public_url(url)
    entry = first_entry(extract_info(safe_url, cookies_from_browser=cookies_from_browser))
    formats = [compact_format(item) for item in entry.get("formats") or []]
    formats.sort(
        key=lambda item: (
            item["kind"] == "audio",
            item["height"] or 0,
            item["tbr"] or 0,
        ),
        reverse=True,
    )
    formats = formats[:240]
    return {
        "id": entry.get("id"),
        "title": entry.get("title") or "未命名视频",
        "uploader": entry.get("uploader") or entry.get("channel") or "-",
        "extractor": entry.get("extractor_key") or entry.get("extractor") or "-",
        "duration": entry.get("duration"),
        "duration_text": duration_text(entry.get("duration")),
        "thumbnail": entry.get("thumbnail"),
        "webpage_url": entry.get("webpage_url") or safe_url,
        "description": (entry.get("description") or "")[:600],
        "format_count": len(formats),
        "formats": formats,
        "quality_presets": list(QUALITY_PRESETS),
    }


def direct_links(
    url: str,
    selector: str,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    safe_url = validate_public_url(url)
    info = extract_info(safe_url, selector, cookies_from_browser=cookies_from_browser)
    items = collect_direct_items(info)
    if not items:
        raise AppError("没有解析到可用媒体直链", HTTPStatus.UNPROCESSABLE_ENTITY)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notice": "媒体直链通常会过期，并可能依赖 http_headers 中的请求头。",
        "items": items,
    }


@dataclass
class DownloadTask:
    id: str
    url: str
    format_selector: str
    merge_format: str | None
    state: str = "queued"
    title: str = "等待解析"
    progress: float = 0.0
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None
    filename: str | None = None
    file_path: Path | None = None
    error: str | None = None
    cancel_requested: bool = False
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "state": self.state,
            "title": self.title,
            "progress": round(self.progress, 1),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "filename": self.filename,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "download_url": f"/api/downloads/{self.id}/file"
            if self.state == "ready"
            else None,
            "artifacts": {
                kind: {
                    key: value
                    for key, value in artifact.items()
                    if key != "file_path"
                }
                for kind, artifact in self.artifacts.items()
            },
        }


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, DownloadTask] = {}
        self._lock = threading.RLock()

    def create(self, url: str, selector: str, merge_format: str | None) -> DownloadTask:
        with self._lock:
            active = sum(
                task.state in {"queued", "downloading", "processing"}
                for task in self._tasks.values()
            )
            if active >= MAX_ACTIVE_TASKS:
                raise AppError("当前下载队列已满，请等待任务完成", HTTPStatus.TOO_MANY_REQUESTS)
            task = DownloadTask(
                id=uuid.uuid4().hex[:12],
                url=url,
                format_selector=selector,
                merge_format=merge_format,
            )
            self._tasks[task.id] = task
            return task

    def get(self, task_id: str) -> DownloadTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AppError("下载任务不存在", HTTPStatus.NOT_FOUND)
            return task

    def update(self, task_id: str, **changes: Any) -> DownloadTask:
        with self._lock:
            task = self.get(task_id)
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = time.time()
            return task

    def update_artifact(self, task_id: str, kind: str, **changes: Any) -> DownloadTask:
        if kind not in ARTIFACT_KINDS:
            raise AppError("未知的媒体工具")
        with self._lock:
            task = self.get(task_id)
            artifact = dict(task.artifacts.get(kind) or {})
            artifact.update(changes)
            artifact["updated_at"] = time.time()
            task.artifacts[kind] = artifact
            task.updated_at = time.time()
            return task

    def list(self) -> list[DownloadTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)

    def remove(self, task_id: str) -> DownloadTask:
        with self._lock:
            task = self.get(task_id)
            return self._tasks.pop(task.id)


class Application:
    def __init__(
        self,
        download_root: Path = DOWNLOAD_ROOT,
        cookies_from_browser: str | None = None,
    ) -> None:
        self.download_root = download_root.resolve()
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.cookies_from_browser = validate_cookie_browser(cookies_from_browser)
        self.tasks = TaskRegistry()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-download")
        self.tool_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-tool")
        self._thumbnail_cache: dict[str, tuple[bytes, str]] = {}
        self._thumbnail_lock = threading.Lock()

    def start_download(self, url: str, selector: str, merge_format: str | None) -> DownloadTask:
        task = self.tasks.create(url, selector, merge_format)
        self.executor.submit(self._run_download, task.id)
        return task

    def cancel_or_remove(self, task_id: str) -> DownloadTask:
        task = self.tasks.get(task_id)
        if task.state in {"queued", "downloading", "processing"}:
            return self.tasks.update(task_id, cancel_requested=True)
        if any(
            artifact.get("state") in {"queued", "processing"}
            for artifact in task.artifacts.values()
        ):
            raise AppError("媒体工具仍在处理，请完成后再移除任务", HTTPStatus.CONFLICT)
        removed = self.tasks.remove(task_id)
        task_dir = self.download_root / removed.id
        if task_dir.exists():
            shutil.rmtree(task_dir)
        return removed

    def start_artifact(
        self,
        task_id: str,
        kind: str,
        *,
        model: str = "base",
        language: str | None = None,
    ) -> DownloadTask:
        if kind not in ARTIFACT_KINDS:
            raise AppError("未知的媒体工具")
        task = self.tasks.get(task_id)
        self._require_ready_media(task)
        if kind == "transcript" and any(
            artifact.get("state") in {"queued", "processing"}
            for item in self.tasks.list()
            for artifact_kind, artifact in item.artifacts.items()
            if artifact_kind == "transcript" and item.id != task_id
        ):
            raise AppError(
                "已有语音转写任务正在运行，请完成后再试",
                HTTPStatus.TOO_MANY_REQUESTS,
            )
        current = task.artifacts.get(kind) or {}
        if current.get("state") in {"queued", "processing"}:
            raise AppError("该媒体工具正在处理", HTTPStatus.CONFLICT)
        self.tasks.update_artifact(
            task_id,
            kind,
            state="queued",
            error=None,
            filename=None,
            download_url=None,
            model=model if kind == "transcript" else None,
            language=language if kind == "transcript" else None,
        )
        self.tool_executor.submit(self._run_artifact, task_id, kind, model, language)
        return self.tasks.get(task_id)

    def get_artifact(self, task_id: str, kind: str) -> tuple[DownloadTask, dict[str, Any]]:
        if kind not in ARTIFACT_KINDS:
            raise AppError("未知的媒体工具")
        task = self.tasks.get(task_id)
        artifact = task.artifacts.get(kind)
        if not artifact:
            raise AppError("派生文件不存在", HTTPStatus.NOT_FOUND)
        return task, artifact

    def _require_ready_media(self, task: DownloadTask) -> Path:
        if task.state != "ready" or not task.file_path or not task.file_path.is_file():
            raise AppError("原视频文件尚未准备好", HTTPStatus.CONFLICT)
        file_path = task.file_path.resolve()
        expected_parent = (self.download_root / task.id).resolve()
        if file_path.parent != expected_parent:
            raise AppError("媒体文件路径无效", HTTPStatus.FORBIDDEN)
        return file_path

    def _run_artifact(
        self,
        task_id: str,
        kind: str,
        model: str,
        language: str | None,
    ) -> None:
        try:
            task = self.tasks.get(task_id)
            source = self._require_ready_media(task)
            task_dir = source.parent
            stem = source.stem[:120]
            self.tasks.update_artifact(task_id, kind, state="processing", error=None)
            if kind == "frame":
                output = task_dir / f"{stem} - 首帧.jpg"
                run_ffmpeg(["-i", str(source), "-frames:v", "1", "-q:v", "2", str(output)])
            elif kind == "audio":
                output = task_dir / f"{stem} - 音频.mp3"
                run_ffmpeg(
                    [
                        "-i",
                        str(source),
                        "-vn",
                        "-c:a",
                        "libmp3lame",
                        "-q:a",
                        "2",
                        str(output),
                    ]
                )
            else:
                output = self._transcribe(source, task_dir, stem, model, language)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("媒体工具执行完成，但没有生成文件")
            self.tasks.update_artifact(
                task_id,
                kind,
                state="ready",
                filename=output.name,
                file_path=output.resolve(),
                size=output.stat().st_size,
                download_url=f"/api/downloads/{task_id}/artifacts/{kind}/file",
                error=None,
            )
        except Exception as exc:
            self.tasks.update_artifact(task_id, kind, state="error", error=clean_error(exc))

    def _transcribe(
        self,
        source: Path,
        task_dir: Path,
        stem: str,
        model_name: str,
        language: str | None,
    ) -> Path:
        if importlib.util.find_spec("faster_whisper") is None:
            raise AppError(
                "未安装 faster-whisper。请运行 pip install -r requirements.txt 后重试。",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        from faster_whisper import WhisperModel

        audio_path = task_dir / f"{stem} - 转写音频.wav"
        run_ffmpeg(
            [
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ]
        )
        try:
            WHISPER_MODEL_ROOT.mkdir(parents=True, exist_ok=True)
            try:
                model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(WHISPER_MODEL_ROOT),
                )
            except Exception as exc:
                raise RuntimeError(
                    "转写模型下载或加载失败，请检查网络后重试。"
                    f"详细信息: {clean_error(exc)}"
                ) from exc
            segments, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=5,
                vad_filter=True,
            )
            rows = []
            text_lines = []
            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue
                rows.append(
                    {
                        "start": round(segment.start, 3),
                        "end": round(segment.end, 3),
                        "text": text,
                    }
                )
                text_lines.append(
                    f"[{transcript_timestamp(segment.start)} - {transcript_timestamp(segment.end)}] {text}"
                )
            if not rows:
                raise RuntimeError("没有识别到可转写的语音")
            output = task_dir / f"{stem} - 转写.txt"
            output.write_text(
                "\n".join(
                    [
                        f"语言: {info.language} (置信度 {info.language_probability:.2f})",
                        f"模型: {model_name}",
                        "",
                        *text_lines,
                    ]
                ),
                encoding="utf-8-sig",
            )
            metadata = task_dir / f"{stem} - 转写.json"
            metadata.write_text(
                json.dumps(
                    {
                        "model": model_name,
                        "language": info.language,
                        "language_probability": info.language_probability,
                        "segments": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return output
        finally:
            audio_path.unlink(missing_ok=True)

    def get_thumbnail(self, url: str, referer: str | None) -> tuple[bytes, str]:
        safe_url = validate_public_url(url)
        safe_referer = validate_public_url(referer) if referer else None
        cache_key = f"{safe_url}\n{safe_referer or ''}"
        with self._thumbnail_lock:
            cached = self._thumbnail_cache.get(cache_key)
            if cached:
                return cached

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if safe_referer:
            headers["Referer"] = safe_referer
        request = Request(safe_url, headers=headers)
        try:
            with build_opener(ValidatingRedirectHandler()).open(request, timeout=15) as response:
                content_type = response.headers.get_content_type()
                if not content_type.startswith("image/"):
                    raise AppError("封面响应不是图片", HTTPStatus.BAD_GATEWAY)
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > 5 * 1024 * 1024:
                    raise AppError("封面文件过大", HTTPStatus.BAD_GATEWAY)
                data = response.read(5 * 1024 * 1024 + 1)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("无法读取视频封面", HTTPStatus.BAD_GATEWAY) from exc
        if len(data) > 5 * 1024 * 1024:
            raise AppError("封面文件过大", HTTPStatus.BAD_GATEWAY)
        result = (data, content_type)
        with self._thumbnail_lock:
            if len(self._thumbnail_cache) >= 32:
                self._thumbnail_cache.pop(next(iter(self._thumbnail_cache)))
            self._thumbnail_cache[cache_key] = result
        return result

    def _run_download(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        task_dir = (self.download_root / task.id).resolve()
        if task_dir.parent != self.download_root:
            self.tasks.update(task_id, state="error", error="下载目录无效")
            return
        task_dir.mkdir(parents=True, exist_ok=True)

        def ensure_not_cancelled() -> None:
            if self.tasks.get(task_id).cancel_requested:
                raise DownloadCancelled("任务已取消")

        def progress_hook(data: dict[str, Any]) -> None:
            ensure_not_cancelled()
            status = data.get("status")
            if status == "downloading":
                downloaded = data.get("downloaded_bytes")
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                progress = (float(downloaded) / float(total) * 100) if downloaded and total else 0
                self.tasks.update(
                    task_id,
                    state="downloading",
                    progress=max(0.0, min(progress, 99.5)),
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=data.get("speed"),
                    eta=data.get("eta"),
                )
            elif status == "finished":
                self.tasks.update(task_id, state="processing", progress=99.5, eta=None)

        def postprocessor_hook(data: dict[str, Any]) -> None:
            ensure_not_cancelled()
            self.tasks.update(task_id, state="processing", progress=99.5, eta=None)

        try:
            safe_url = validate_public_url(task.url)
            yt_dlp = load_yt_dlp()
            options: dict[str, Any] = {
                "format": task.format_selector,
                "noplaylist": True,
                "outtmpl": str(task_dir / "%(title).180B [%(id)s].%(ext)s"),
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [postprocessor_hook],
                "concurrent_fragment_downloads": 2,
                "windowsfilenames": True,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
            }
            options.update(cookie_options(self.cookies_from_browser))
            if task.merge_format:
                options["merge_output_format"] = task.merge_format
            self.tasks.update(task_id, state="downloading", progress=0.0)
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(safe_url, download=True)
            ensure_not_cancelled()
            entry = first_entry(info)
            candidates = [
                path
                for path in task_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() in MEDIA_EXTENSIONS
                and not path.name.endswith(".part")
            ]
            if not candidates:
                raise RuntimeError("下载完成，但没有找到最终媒体文件")
            output = max(candidates, key=lambda path: path.stat().st_size)
            self.tasks.update(
                task_id,
                state="ready",
                progress=100.0,
                title=entry.get("title") or task.title,
                filename=output.name,
                file_path=output.resolve(),
                downloaded_bytes=output.stat().st_size,
                total_bytes=output.stat().st_size,
                speed=None,
                eta=None,
                error=None,
            )
        except DownloadCancelled:
            self.tasks.update(task_id, state="cancelled", error=None, eta=None)
        except Exception as exc:
            current = self.tasks.get(task_id)
            state = "cancelled" if current.cancel_requested else "error"
            self.tasks.update(
                task_id,
                state=state,
                error=friendly_source_error(task.url, exc),
                eta=None,
            )

    def shutdown(self) -> None:
        for task in self.tasks.list():
            if task.state in {"queued", "downloading", "processing"}:
                self.tasks.update(task.id, cancel_requested=True)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.tool_executor.shutdown(wait=False, cancel_futures=True)


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: Application):
        super().__init__(server_address, RequestHandler)
        self.app = app


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "PublicVideoWorkbench/1.0"

    @property
    def app(self) -> Application:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self) -> None:
        self._dispatch(self._handle_get)

    def do_HEAD(self) -> None:
        self._dispatch(lambda: self._handle_get(head_only=True))

    def do_POST(self) -> None:
        self._dispatch(self._handle_post)

    def do_DELETE(self) -> None:
        self._dispatch(self._handle_delete)

    def _dispatch(self, handler: Callable[[], None]) -> None:
        try:
            handler()
        except AppError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc()
            self._send_json({"error": "服务器处理请求时发生错误"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _path(self) -> str:
        return unquote(urlparse(self.path).path)

    def _handle_get(self, head_only: bool = False) -> None:
        path = self._path()
        if path == "/api/thumbnail":
            query = parse_qs(urlparse(self.path).query)
            url = query.get("url", [None])[0]
            referer = query.get("referer", [None])[0]
            data, content_type = self.app.get_thumbnail(url, referer)
            self._send_bytes(data, content_type, head_only=head_only)
            return
        if path == "/api/health":
            try:
                version = load_yt_dlp().version.__version__
            except Exception:
                version = None
            self._send_json(
                {
                    "status": "ok" if version else "degraded",
                    "yt_dlp": version,
                    "cookies_from_browser": self.app.cookies_from_browser,
                    "ffmpeg": shutil.which("ffmpeg") is not None,
                    "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
                    "tools": {
                        "frame": "https://github.com/FFmpeg/FFmpeg",
                        "audio": "https://github.com/FFmpeg/FFmpeg",
                        "transcript": "https://github.com/SYSTRAN/faster-whisper",
                    },
                },
                head_only=head_only,
            )
            return
        if path == "/api/downloads":
            self._send_json(
                {"tasks": [task.public() for task in self.app.tasks.list()]},
                head_only=head_only,
            )
            return
        match = re.fullmatch(r"/api/downloads/([a-f0-9]{12})", path)
        if match:
            self._send_json(self.app.tasks.get(match.group(1)).public(), head_only=head_only)
            return
        match = re.fullmatch(r"/api/downloads/([a-f0-9]{12})/file", path)
        if match:
            self._send_download(self.app.tasks.get(match.group(1)), head_only=head_only)
            return
        match = re.fullmatch(
            r"/api/downloads/([a-f0-9]{12})/artifacts/(frame|audio|transcript)/file",
            path,
        )
        if match:
            task, artifact = self.app.get_artifact(match.group(1), match.group(2))
            self._send_artifact(task, artifact, head_only=head_only)
            return
        if path == "/":
            self._send_static(STATIC_DIR / "index.html", head_only=head_only)
            return
        if path in {"/styles.css", "/app.js"}:
            self._send_static(STATIC_DIR / path.removeprefix("/"), head_only=head_only)
            return
        if path.startswith("/assets/"):
            relative = Path(path.removeprefix("/assets/"))
            asset_root = (STATIC_DIR / "assets").resolve()
            candidate = (asset_root / relative).resolve()
            if not candidate.is_relative_to(asset_root) or not candidate.is_file():
                raise AppError("静态资源不存在", HTTPStatus.NOT_FOUND)
            self._send_static(candidate, head_only=head_only)
            return
        raise AppError("页面不存在", HTTPStatus.NOT_FOUND)

    def _handle_post(self) -> None:
        path = self._path()
        body = self._read_json()
        if path == "/api/inspect":
            result = inspect_video(
                body.get("url"),
                cookies_from_browser=self.app.cookies_from_browser,
            )
            if result.get("thumbnail"):
                result["thumbnail"] = (
                    "/api/thumbnail?url="
                    + quote(result["thumbnail"], safe="")
                    + "&referer="
                    + quote(result["webpage_url"], safe="")
                )
            self._send_json(result)
            return
        if path == "/api/links":
            selector = validate_format_selector(body)
            self._send_json(
                direct_links(
                    body.get("url"),
                    selector,
                    cookies_from_browser=self.app.cookies_from_browser,
                )
            )
            return
        if path == "/api/downloads":
            url = validate_public_url(body.get("url"))
            selector = validate_format_selector(body)
            merge_format = validate_merge_format(body.get("merge_format"))
            task = self.app.start_download(url, selector, merge_format)
            self._send_json(task.public(), HTTPStatus.ACCEPTED)
            return
        match = re.fullmatch(
            r"/api/downloads/([a-f0-9]{12})/artifacts/(frame|audio|transcript)",
            path,
        )
        if match:
            kind = match.group(2)
            model, language = (
                validate_transcription_options(body)
                if kind == "transcript"
                else ("base", None)
            )
            task = self.app.start_artifact(
                match.group(1),
                kind,
                model=model,
                language=language,
            )
            self._send_json(task.public(), HTTPStatus.ACCEPTED)
            return
        raise AppError("接口不存在", HTTPStatus.NOT_FOUND)

    def _handle_delete(self) -> None:
        match = re.fullmatch(r"/api/downloads/([a-f0-9]{12})", self._path())
        if not match:
            raise AppError("接口不存在", HTTPStatus.NOT_FOUND)
        task = self.app.cancel_or_remove(match.group(1))
        self._send_json(task.public())

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AppError("请求长度无效") from exc
        if length <= 0:
            raise AppError("请求内容为空或过大")
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            raise AppError("请求内容为空或过大")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("请求必须是有效的 UTF-8 JSON") from exc
        if not isinstance(body, dict):
            raise AppError("请求 JSON 必须是对象")
        return body

    def _send_json(
        self,
        payload: Any,
        status: int = HTTPStatus.OK,
        *,
        head_only: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_static(self, path: Path, *, head_only: bool) -> None:
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        *,
        head_only: bool = False,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_download(self, task: DownloadTask, *, head_only: bool) -> None:
        if task.state != "ready" or not task.file_path or not task.file_path.is_file():
            raise AppError("文件尚未准备好", HTTPStatus.CONFLICT)
        file_path = task.file_path.resolve()
        expected_parent = (self.app.download_root / task.id).resolve()
        if file_path.parent != expected_parent:
            raise AppError("下载文件路径无效", HTTPStatus.FORBIDDEN)
        filename = task.filename or file_path.name
        self._send_file(file_path, filename, head_only=head_only)

    def _send_artifact(
        self,
        task: DownloadTask,
        artifact: dict[str, Any],
        *,
        head_only: bool,
    ) -> None:
        file_path = artifact.get("file_path")
        if artifact.get("state") != "ready" or not isinstance(file_path, Path):
            raise AppError("派生文件尚未准备好", HTTPStatus.CONFLICT)
        resolved = file_path.resolve()
        expected_parent = (self.app.download_root / task.id).resolve()
        if resolved.parent != expected_parent or not resolved.is_file():
            raise AppError("派生文件路径无效", HTTPStatus.FORBIDDEN)
        filename = str(artifact.get("filename") or resolved.name)
        self._send_file(resolved, filename, head_only=head_only)

    def _send_file(self, file_path: Path, filename: str, *, head_only: bool) -> None:
        size = file_path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                raise AppError("Range 请求无效", HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            elif match.group(2):
                suffix_length = int(match.group(2))
                start = max(0, size - suffix_length)
            if start > end or start >= size:
                raise AppError("Range 超出文件范围", HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "artifact.bin"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", formatdate(file_path.stat().st_mtime, usegmt=True))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}",
        )
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if head_only:
            return
        with file_path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动公开视频下载网页")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认: 8765")
    parser.add_argument("--open-browser", action="store_true", help="启动后打开浏览器")
    parser.add_argument(
        "--cookies-from-browser",
        choices=sorted(COOKIE_BROWSERS),
        help="从本机指定浏览器读取 Cookie；仅用于你有权访问的内容",
    )
    return parser


def open_local_browser(url: str, cookies_from_browser: str | None) -> None:
    executable = alternate_browser_executable(cookies_from_browser)
    if executable:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            subprocess.Popen(
                [str(executable), url],
                creationflags=creation_flags,
                close_fds=True,
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("错误: 端口必须在 0 到 65535 之间")
        return 2
    if not STATIC_DIR.is_dir():
        print(f"错误: 网页资源目录不存在: {STATIC_DIR}")
        return 2
    app = Application(cookies_from_browser=args.cookies_from_browser)
    try:
        server = AppHTTPServer((args.host, args.port), app)
    except OSError as exc:
        print(f"错误: 无法监听 {args.host}:{args.port}: {exc}")
        app.shutdown()
        return 1
    actual_port = server.server_address[1]
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{actual_port}/"
    print(f"取流台已启动: {url}")
    print("按 Ctrl+C 停止服务。")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("警告: 当前服务可被其他设备访问，请勿直接暴露到公网。")
    if args.open_browser:
        threading.Timer(
            0.5,
            lambda: open_local_browser(url, args.cookies_from_browser),
        ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    finally:
        server.shutdown()
        server.server_close()
        app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
