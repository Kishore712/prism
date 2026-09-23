"""Strict allowlist policy for public ChatGPT shared links."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ....exceptions import ValidationError


class ChatGPTShareUrlPolicy:
    """Accept only the canonical public ChatGPT share URL shape."""

    _path = re.compile(r"^/share/[A-Za-z0-9_-]{20,200}/?$")

    def validate(self, raw_url: str) -> str:
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError as exc:
            raise ValidationError("The shared-link URL is malformed") from exc
        if parsed.scheme != "https":
            raise ValidationError("The shared-link URL must use HTTPS")
        if parsed.hostname != "chatgpt.com":
            raise ValidationError("The shared-link URL host must be exactly chatgpt.com")
        if port not in {None, 443}:
            raise ValidationError("The shared-link URL cannot use an alternate port")
        if parsed.username is not None or parsed.password is not None:
            raise ValidationError("The shared-link URL cannot contain user information")
        if parsed.query or parsed.fragment:
            raise ValidationError(
                "The shared-link URL cannot contain a query string or fragment"
            )
        if not self._path.fullmatch(parsed.path):
            raise ValidationError(
                "The URL path must be /share/<conversation-id> with no extra text"
            )
        return raw_url
