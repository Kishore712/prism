from __future__ import annotations

import io
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from prism.exceptions import RemoteCaptureError
from prism.providers.chatgpt.shared_link import BoundedSharedPageFetcher


VALID_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


class _Headers:
    @staticmethod
    def get_content_type() -> str:
        return "text/html"


class _Response:
    status = 200
    headers = _Headers()

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return False

    def read(self, size: int) -> bytes:
        return self._body[:size]


class _Opener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout):  # type: ignore[no-untyped-def]
        self.requests.append((request, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class BoundedSharedPageFetcherTests(unittest.TestCase):
    def test_makes_one_bounded_request_without_credentials(self) -> None:
        opener = _Opener(_Response(b"<html></html>"))
        fetcher = BoundedSharedPageFetcher(timeout_seconds=7.0)

        with patch(
            "prism.providers.chatgpt.shared_link.fetcher.build_opener",
            return_value=opener,
        ):
            body = fetcher.fetch(VALID_URL)

        self.assertEqual(body, b"<html></html>")
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(timeout, 7.0)
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)

    def test_rejects_redirects_and_oversized_bodies(self) -> None:
        redirect = HTTPError(
            VALID_URL,
            302,
            "Found",
            Message(),
            io.BytesIO(),
        )
        with patch(
            "prism.providers.chatgpt.shared_link.fetcher.build_opener",
            return_value=_Opener(redirect),
        ), self.assertRaises(RemoteCaptureError):
            BoundedSharedPageFetcher().fetch(VALID_URL)
        redirect.close()

        with patch(
            "prism.providers.chatgpt.shared_link.fetcher.build_opener",
            return_value=_Opener(_Response(b"12345")),
        ), self.assertRaises(RemoteCaptureError):
            BoundedSharedPageFetcher(max_page_bytes=4).fetch(VALID_URL)


if __name__ == "__main__":
    unittest.main()
