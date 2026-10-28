#!/usr/bin/env python3
"""Download authorized public videos or inspect their direct media URLs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


DEFAULT_FORMAT = "bv*+ba/b"
DEFAULT_TEMPLATE = "%(title).180B [%(id)s].%(ext)s"


def load_yt_dlp() -> Any:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 yt-dlp。请先运行: python -m pip install -r requirements.txt"
        ) from exc
    return yt_dlp


def validate_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("请输入完整的 http:// 或 https:// 视频页面地址")
    return value


def validate_template(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("输出模板必须是下载目录内的相对路径")
    return value


def add_source_arguments(parser: argparse.ArgumentParser, *, include_format: bool) -> None:
    parser.add_argument("url", type=validate_http_url, help="公开视频页面地址")
    if include_format:
        parser.add_argument(
            "-f",
            "--format",
            default=DEFAULT_FORMAT,
            help=f"yt-dlp 格式选择表达式，默认: {DEFAULT_FORMAT}",
        )
    parser.add_argument(
        "--playlist",
        action="store_true",
        help="允许处理播放列表；默认只处理当前视频",
    )
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument(
        "--cookies",
        type=Path,
        metavar="FILE",
        help="Netscape 格式 cookies 文件，仅用于你自己的账号",
    )
    auth.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="从本机浏览器读取你自己的登录态，例如 edge、chrome、firefox",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "面向公开且有权保存内容的视频下载与直链解析工具。"
            "不支持绕过 DRM、付费墙或访问控制。"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="下载并按需合并音视频")
    add_source_arguments(download, include_format=True)
    download.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("downloads"),
        help="下载目录，默认: ./downloads",
    )
    download.add_argument(
        "--output-template",
        type=validate_template,
        default=DEFAULT_TEMPLATE,
        help="yt-dlp 文件名模板",
    )
    download.add_argument(
        "--merge-format",
        choices=("mp4", "mkv", "webm"),
        help="音视频合并容器；不指定时由 yt-dlp 自动选择",
    )
    download.add_argument(
        "--concurrent-fragments",
        type=int,
        default=1,
        metavar="N",
        help="HLS/DASH 分片并发数，默认: 1",
    )

    links = subparsers.add_parser("links", help="输出当前选中格式的临时媒体直链")
    add_source_arguments(links, include_format=True)
    links.add_argument(
        "--json",
        action="store_true",
        help="输出包含格式、请求头和来源信息的结构化 JSON",
    )
    links.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="写入文件；不指定时输出到终端",
    )

    formats = subparsers.add_parser("formats", help="列出页面可用的音视频格式")
    add_source_arguments(formats, include_format=False)

    return parser


def build_common_options(args: argparse.Namespace, *, quiet: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "noplaylist": not args.playlist,
        "quiet": quiet,
        "no_warnings": False,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
    }
    if getattr(args, "format", None):
        options["format"] = args.format
    if args.cookies:
        options["cookiefile"] = str(args.cookies.expanduser().resolve())
    if args.cookies_from_browser:
        options["cookiesfrombrowser"] = (args.cookies_from_browser,)
    return options


def iter_entries(info: dict[str, Any]) -> Iterable[dict[str, Any]]:
    entries = info.get("entries")
    if entries:
        for entry in entries:
            if entry:
                yield from iter_entries(entry)
        return
    yield info


def media_type(item: dict[str, Any]) -> str:
    has_video = item.get("vcodec") not in {None, "none"}
    has_audio = item.get("acodec") not in {None, "none"}
    if has_video and has_audio:
        return "audio+video"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return "unknown"


def resolution(item: dict[str, Any]) -> str | None:
    if item.get("resolution"):
        return str(item["resolution"])
    width, height = item.get("width"), item.get("height")
    if width and height:
        return f"{width}x{height}"
    if item.get("abr"):
        return f"audio {item['abr']}k"
    return None


def collect_direct_items(info: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in iter_entries(info):
        selected = (
            entry.get("requested_downloads")
            or entry.get("requested_formats")
            or [entry]
        )
        for item in selected:
            url = item.get("url")
            if not url:
                continue
            result.append(
                {
                    "source_id": entry.get("id"),
                    "source_title": entry.get("title"),
                    "webpage_url": entry.get("webpage_url") or info.get("webpage_url"),
                    "extractor": entry.get("extractor_key") or info.get("extractor_key"),
                    "format_id": item.get("format_id"),
                    "media_type": media_type(item),
                    "ext": item.get("ext"),
                    "resolution": resolution(item),
                    "protocol": item.get("protocol"),
                    "filesize": item.get("filesize") or item.get("filesize_approx"),
                    "url": url,
                    "http_headers": item.get("http_headers")
                    or entry.get("http_headers")
                    or {},
                }
            )
    return result


def write_output(content: str, output: Path | None) -> None:
    if output is None:
        print(content)
        return
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content + ("" if content.endswith("\n") else "\n"), encoding="utf-8")
    print(f"已写入: {destination}", file=sys.stderr)


def run_download(args: argparse.Namespace) -> int:
    if args.concurrent_fragments < 1:
        raise ValueError("--concurrent-fragments 必须大于等于 1")
    yt_dlp = load_yt_dlp()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    options = build_common_options(args, quiet=False)
    options.update(
        {
            "outtmpl": str(output_dir / args.output_template),
            "concurrent_fragment_downloads": args.concurrent_fragments,
        }
    )
    if args.merge_format:
        options["merge_output_format"] = args.merge_format
    with yt_dlp.YoutubeDL(options) as downloader:
        return 0 if downloader.download([args.url]) == 0 else 1


def run_links(args: argparse.Namespace) -> int:
    yt_dlp = load_yt_dlp()
    options = build_common_options(args, quiet=True)
    options["skip_download"] = True
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(args.url, download=False)
    items = collect_direct_items(info)
    if not items:
        raise RuntimeError("未解析到可用媒体直链；请先运行 formats 检查可用格式")

    if args.json:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notice": "媒体直链通常会过期，并可能依赖 http_headers 中的请求头。",
            "items": items,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        content = "\n".join(item["url"] for item in items)
        print(
            "提示：直链通常有时效；若请求被拒绝，请使用 --json 查看所需请求头。",
            file=sys.stderr,
        )
    write_output(content, args.output)
    return 0


def human_size(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return "-"


def clipped(value: Any, width: int) -> str:
    text = "-" if value in {None, ""} else str(value)
    return text if len(text) <= width else text[: width - 1] + "~"


def run_formats(args: argparse.Namespace) -> int:
    yt_dlp = load_yt_dlp()
    options = build_common_options(args, quiet=True)
    options["skip_download"] = True
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(args.url, download=False)

    for entry in iter_entries(info):
        print(f"\n{entry.get('title') or entry.get('id') or '未知标题'}")
        print(f"{'ID':<12} {'EXT':<5} {'RESOLUTION':<13} {'FPS':<5} {'VCODEC':<15} {'ACODEC':<15} {'SIZE':>10}")
        print("-" * 82)
        for item in entry.get("formats") or []:
            size = item.get("filesize") or item.get("filesize_approx")
            print(
                f"{clipped(item.get('format_id'), 12):<12} "
                f"{clipped(item.get('ext'), 5):<5} "
                f"{clipped(resolution(item), 13):<13} "
                f"{clipped(item.get('fps'), 5):<5} "
                f"{clipped(item.get('vcodec'), 15):<15} "
                f"{clipped(item.get('acodec'), 15):<15} "
                f"{human_size(size):>10}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            return run_download(args)
        if args.command == "links":
            return run_links(args)
        if args.command == "formats":
            return run_formats(args)
        parser.error(f"未知命令: {args.command}")
    except (RuntimeError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"处理失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
