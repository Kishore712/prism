from __future__ import annotations

import json
import unittest

from prism.exceptions import ValidationError
from prism.models.capture import CaptureMethod, RemoteCaptureSource
from prism.providers.chatgpt.shared_link import (
    ChatGPTSharedLinkAdapter,
    ChatGPTShareUrlPolicy,
)

from .helpers import StaticFetcher, synthetic_shared_page


VALID_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


class ChatGPTSharedLinkAdapterTests(unittest.TestCase):
    def test_url_policy_accepts_only_canonical_chatgpt_share_links(self) -> None:
        policy = ChatGPTShareUrlPolicy()
        self.assertEqual(policy.validate(VALID_URL), VALID_URL)
        invalid = (
            VALID_URL.replace("https://", "http://"),
            VALID_URL.replace("chatgpt.com", "example.com"),
            VALID_URL + "?redirect=https://example.com",
            "https://user@chatgpt.com/share/00000000-0000-4000-8000-000000000000",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                policy.validate(url)

    def test_adapter_filters_hidden_records_and_removes_native_ids(self) -> None:
        fetcher = StaticFetcher(synthetic_shared_page())
        adapter = ChatGPTSharedLinkAdapter(fetcher=fetcher)

        candidate = adapter.inspect(RemoteCaptureSource(url=VALID_URL))
        serialized = json.dumps(candidate.model_dump(mode="json"))

        self.assertEqual(fetcher.received_url, VALID_URL)
        self.assertEqual(candidate.capture.method, CaptureMethod.SHARED_LINK)
        self.assertEqual(len(candidate.capture.messages), 4)
        self.assertEqual(candidate.observations.provider_node_count, 6)
        self.assertEqual(
            candidate.observations.content_reference_types,
            {"grouped_webpages": 1},
        )
        self.assertNotIn("PRIVATE SYSTEM TEXT", serialized)
        self.assertNotIn("native-conversation-id", serialized)
        self.assertNotIn("native-user-message", serialized)
        self.assertNotIn("native-exchange", serialized)
        self.assertNotIn(VALID_URL, serialized)


if __name__ == "__main__":
    unittest.main()
