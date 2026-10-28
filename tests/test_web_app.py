from __future__ import annotations

import io
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import web_app


class WebAppTests(unittest.TestCase):
    def test_validate_public_url_blocks_loopback(self):
        loopback = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(web_app.socket, "getaddrinfo", return_value=loopback):
            with self.assertRaisesRegex(web_app.AppError, "拒绝访问"):
                web_app.validate_public_url("https://localhost/video")

    def test_validate_public_url_accepts_global_address(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch.object(web_app.socket, "getaddrinfo", return_value=public):
            result = web_app.validate_public_url("https://example.com/video")
        self.assertEqual(result, "https://example.com/video")

    def test_x_twitter_urls_are_recognized_as_a_supported_source(self):
        self.assertEqual(
            web_app.source_label("https://x.com/example/status/123"),
            "X / Twitter",
        )
        self.assertEqual(
            web_app.source_label("https://twitter.com/example/status/123"),
            "X / Twitter",
        )

    def test_x_twitter_timeout_has_actionable_error(self):
        message = web_app.friendly_source_error(
            "https://x.com/example/status/123",
            "Unable to download JSON metadata: timed out",
        )
        self.assertIn("X/Twitter", message)
        self.assertIn("网络", message)

    def test_douyin_urls_and_fresh_cookie_errors_are_actionable(self):
        self.assertEqual(
            web_app.source_label("https://v.douyin.com/example/"),
            "抖音",
        )
        message = web_app.friendly_source_error(
            "https://www.douyin.com/video/7695252900267838771",
            "Fresh cookies (not necessarily logged in) are needed",
        )
        self.assertIn("新鲜浏览器 Cookie", message)
        self.assertIn("YTDLP_COOKIES_FROM_BROWSER", message)

    def test_extract_info_passes_browser_cookie_option(self):
        class FakeDownloader:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def extract_info(self, url, download=False):
                return {"id": "demo", "url": url}

        class FakeYtDlp:
            def __init__(self):
                self.options = None

            def YoutubeDL(self, options):
                self.options = options
                return FakeDownloader(options)

        fake = FakeYtDlp()
        with patch.object(web_app, "load_yt_dlp", return_value=fake):
            result = web_app.extract_info(
                "https://www.douyin.com/video/7695252900267838771",
                cookies_from_browser="chrome",
            )
        self.assertEqual(result["id"], "demo")
        self.assertEqual(fake.options["cookiesfrombrowser"], ("chrome",))

    def test_invalid_browser_cookie_source_is_rejected(self):
        with self.assertRaisesRegex(web_app.AppError, "不支持的浏览器 Cookie"):
            web_app.validate_cookie_browser("not-a-browser")

    def test_compact_format_classifies_split_streams(self):
        video = web_app.compact_format(
            {
                "format_id": "v1080",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "width": 1920,
                "height": 1080,
            }
        )
        audio = web_app.compact_format(
            {
                "format_id": "a128",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 128,
            }
        )
        self.assertEqual(video["kind"], "video")
        self.assertEqual(video["resolution"], "1920x1080")
        self.assertEqual(audio["kind"], "audio")
        self.assertEqual(audio["resolution"], "128 kbps")

    def test_validate_transcription_options_uses_whitelist(self):
        self.assertEqual(
            web_app.validate_transcription_options({"model": "base", "language": "zh"}),
            ("base", "zh"),
        )
        with self.assertRaisesRegex(web_app.AppError, "未知的语音转写模型"):
            web_app.validate_transcription_options({"model": "../../model"})

    def test_frame_artifact_is_created_inside_task_directory(self):
        with tempfile.TemporaryDirectory() as download_root:
            app = web_app.Application(Path(download_root))
            task = app.tasks.create("https://example.com/video", "best", None)
            task_dir = Path(download_root) / task.id
            task_dir.mkdir()
            media = task_dir / "demo.mp4"
            media.write_bytes(b"media")
            app.tasks.update(
                task.id,
                state="ready",
                title="Demo",
                filename=media.name,
                file_path=media.resolve(),
            )

            def fake_ffmpeg(arguments):
                Path(arguments[-1]).write_bytes(b"jpeg")

            try:
                with patch.object(web_app, "run_ffmpeg", side_effect=fake_ffmpeg):
                    app.start_artifact(task.id, "frame")
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        artifact = app.tasks.get(task.id).artifacts.get("frame") or {}
                        if artifact.get("state") in {"ready", "error"}:
                            break
                        time.sleep(0.02)
                artifact = app.tasks.get(task.id).artifacts["frame"]
                self.assertEqual(artifact["state"], "ready")
                self.assertEqual(artifact["file_path"].parent, task_dir.resolve())
                self.assertTrue(artifact["download_url"].endswith("/artifacts/frame/file"))
            finally:
                app.shutdown()

    def test_http_inspect_endpoint_returns_json(self):
        expected = {
            "id": "demo",
            "title": "Public demo",
            "formats": [],
            "format_count": 0,
        }
        with tempfile.TemporaryDirectory() as download_root:
            app = web_app.Application(Path(download_root))
            server = web_app.AppHTTPServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/inspect"
            request = Request(
                url,
                data=json.dumps({"url": "https://example.com/video"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with patch.object(web_app, "inspect_video", return_value=expected):
                    with urlopen(request, timeout=5) as response:
                        payload = json.load(io.TextIOWrapper(response, encoding="utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["title"], "Public demo")
            finally:
                server.shutdown()
                server.server_close()
                app.shutdown()
                thread.join(timeout=5)

    def test_http_rejects_oversized_request(self):
        with tempfile.TemporaryDirectory() as download_root:
            app = web_app.Application(Path(download_root))
            server = web_app.AppHTTPServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_address[1], timeout=5
            )
            try:
                connection.putrequest("POST", "/api/inspect")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", web_app.MAX_REQUEST_BYTES + 1)
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                app.shutdown()
                thread.join(timeout=5)

    def test_http_serves_nested_static_assets(self):
        with tempfile.TemporaryDirectory() as download_root:
            app = web_app.Application(Path(download_root))
            server = web_app.AppHTTPServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/assets/favicon.svg"
            try:
                with urlopen(url, timeout=5) as response:
                    payload = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
                self.assertIn(b"<svg", payload)
            finally:
                server.shutdown()
                server.server_close()
                app.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
