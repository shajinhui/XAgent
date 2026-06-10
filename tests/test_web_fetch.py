from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.catalog import build_default_registry
from tools.core.runner import ToolRunner, create_tool_context
from tools.network import web_fetch


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_size: int | None = None

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        self.read_size = size
        return self.body


class WebFetchTests(unittest.TestCase):
    def test_runner_fetches_public_url_without_approval_or_env_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_default_registry()
            runner = ToolRunner(registry, create_tool_context(Path(tmp)))
            response = FakeHttpResponse("hello".encode())

            with patch.dict(os.environ, {"ENABLE_WEB_FETCH": "false"}), patch(
                "tools.network.web_fetch.socket.getaddrinfo",
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                ],
            ), patch("tools.network.web_fetch.urlopen", return_value=response):
                result = runner.execute(
                    "web_fetch",
                    json.dumps({"url": "https://example.com/page"}),
                )

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "hello")
        self.assertEqual(response.read_size, web_fetch.MAX_RESPONSE_BYTES)

    def test_metadata_marks_web_fetch_as_no_approval_needed(self) -> None:
        registry = build_default_registry()

        self.assertFalse(registry.metadata()["web_fetch"]["requires_approval"])

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(PermissionError):
            web_fetch.run(None, {"url": "file:///etc/passwd"})  # type: ignore[arg-type]

    def test_rejects_localhost_before_dns_resolution(self) -> None:
        with patch("tools.network.web_fetch.socket.getaddrinfo") as getaddrinfo:
            with self.assertRaises(PermissionError):
                web_fetch.run(None, {"url": "http://localhost:8000"})  # type: ignore[arg-type]

        getaddrinfo.assert_not_called()

    def test_rejects_direct_private_ip_before_fetch(self) -> None:
        with patch("tools.network.web_fetch.urlopen") as urlopen:
            with self.assertRaises(PermissionError):
                web_fetch.run(None, {"url": "http://192.168.1.10/status"})  # type: ignore[arg-type]

        urlopen.assert_not_called()

    def test_rejects_domain_that_resolves_to_private_ip(self) -> None:
        with patch(
            "tools.network.web_fetch.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))],
        ), patch("tools.network.web_fetch.urlopen") as urlopen:
            with self.assertRaises(PermissionError):
                web_fetch.run(None, {"url": "http://internal.example"})  # type: ignore[arg-type]

        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
