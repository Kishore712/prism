"""Bounded unauthenticated retrieval of one public ChatGPT shared page."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ....exceptions import RemoteCaptureError
from .url_policy import ChatGPTShareUrlPolicy


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


class BoundedSharedPageFetcher:
    """Fetch one HTML document without cookies, credentials, redirects, or retries."""

    def __init__(
        self,
        url_policy: ChatGPTShareUrlPolicy | None = None,
        *,
        max_page_bytes: int = 8 * 1024 * 1024,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url_policy = url_policy or ChatGPTShareUrlPolicy()
        self._max_page_bytes = max_page_bytes
        self._timeout_seconds = timeout_seconds

    def fetch(self, raw_url: str) -> bytes:
        url = self._url_policy.validate(raw_url)
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "close",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36 Prism/0.1"
                ),
            },
            method="GET",
        )
        try:
            with build_opener(_NoRedirects).open(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                if response.status != 200:
                    raise RemoteCaptureError(
                        f"The shared page returned HTTP {response.status}"
                    )
                if response.headers.get_content_type() != "text/html":
                    raise RemoteCaptureError("The shared page did not return HTML")
                body = response.read(self._max_page_bytes + 1)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise RemoteCaptureError(
                    "The shared page attempted an unsupported redirect"
                ) from exc
            raise RemoteCaptureError(
                f"The shared page returned HTTP {exc.code}"
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise RemoteCaptureError(
                "The shared page could not be retrieved within the timeout"
            ) from exc
        if len(body) > self._max_page_bytes:
            raise RemoteCaptureError("The shared page exceeded the response-size limit")
        return body
