from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import video_tool


class FakeDownloader:
    def __init__(self, options, info=None, download_result=0):
        self.options = options
        self.info = info or {}
        self.download_result = download_result
        self.download_urls = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=False):
        self.extract_args = (url, download)
        return self.info

    def download(self, urls):
        self.download_urls = urls
        return self.download_result


class FakeYtDlp:
    def __init__(self, info=None, download_result=0):
        self.info = info
        self.download_result = download_result
        self.instances = []

    def YoutubeDL(self, options):
        instance = FakeDownloader(options, self.info, self.download_result)
        self.instances.append(instance)
        return instance


class VideoToolTests(unittest.TestCase):
    def test_rejects_non_http_input(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            video_tool.validate_http_url("file:///tmp/video.mp4")

    def test_collects_split_video_audio_urls_and_headers(self):
        info = {
            "id": "BV-test",
            "title": "Test",
            "webpage_url": "https://www.bilibili.com/video/BV-test",
            "requested_formats": [
                {
                    "format_id": "video-1080p",
                    "url": "https://cdn.example/video.m4s",
                    "vcodec": "avc1",
                    "acodec": "none",
                    "width": 1920,
                    "height": 1080,
                    "http_headers": {"Referer": "https://www.bilibili.com/"},
                },
                {
                    "format_id": "audio",
                    "url": "https://cdn.example/audio.m4s",
                    "vcodec": "none",
                    "acodec": "mp4a",
                },
            ],
        }
        items = video_tool.collect_direct_items(info)
        self.assertEqual([item["media_type"] for item in items], ["video", "audio"])
        self.assertEqual(items[0]["resolution"], "1920x1080")
        self.assertEqual(items[0]["http_headers"]["Referer"], "https://www.bilibili.com/")

    def test_links_json_is_machine_readable(self):
        info = {
            "id": "123",
            "title": "Public sample",
            "url": "https://cdn.example/video.mp4",
            "vcodec": "h264",
            "acodec": "aac",
            "ext": "mp4",
        }
        fake = FakeYtDlp(info=info)
        stdout = io.StringIO()
        with patch.object(video_tool, "load_yt_dlp", return_value=fake):
            with redirect_stdout(stdout):
                result = video_tool.main(
                    ["links", "https://example.com/watch/123", "--json"]
                )
        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["items"][0]["url"], "https://cdn.example/video.mp4")
        self.assertEqual(payload["items"][0]["media_type"], "audio+video")

    def test_download_builds_safe_options_without_shell(self):
        fake = FakeYtDlp(download_result=0)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(video_tool, "load_yt_dlp", return_value=fake):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = video_tool.main(
                        [
                            "download",
                            "https://example.com/watch/123",
                            "--cookies-from-browser",
                            "edge",
                            "--concurrent-fragments",
                            "3",
                            "--output-dir",
                            output_dir,
                        ]
                    )
        self.assertEqual(result, 0)
        options = fake.instances[0].options
        self.assertEqual(options["cookiesfrombrowser"], ("edge",))
        self.assertEqual(options["concurrent_fragment_downloads"], 3)
        self.assertEqual(options["socket_timeout"], 30)
        self.assertEqual(options["retries"], 2)
        self.assertEqual(fake.instances[0].download_urls, ["https://example.com/watch/123"])

    def test_invalid_fragment_count_returns_usage_error(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = video_tool.main(
                [
                    "download",
                    "https://example.com/watch/123",
                    "--concurrent-fragments",
                    "0",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("必须大于等于 1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
