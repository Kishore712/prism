from __future__ import annotations

from prism.models.capture import CapturedSession, RemoteCaptureSource
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.services.normalization import CaptureNormalizer

from tests.providers.chatgpt.shared_link.helpers import StaticFetcher, synthetic_shared_page


VALID_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


def canonical_capture() -> CapturedSession:
    adapter = ChatGPTSharedLinkAdapter(fetcher=StaticFetcher(synthetic_shared_page()))
    remote = adapter.inspect(RemoteCaptureSource(url=VALID_URL))
    return CaptureNormalizer().normalize(adapter.version, remote.capture)
