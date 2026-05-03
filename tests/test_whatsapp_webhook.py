from __future__ import annotations

import unittest

from api.whatsapp_webhook import extract_whatsapp_events


class WhatsAppWebhookParserTests(unittest.TestCase):
    def test_malformed_payload_is_ignored_without_events(self) -> None:
        events, ignored = extract_whatsapp_events({"entry": {"not": "a-list"}})
        self.assertEqual(events, [])
        self.assertGreaterEqual(ignored, 1)

    def test_partial_messages_are_ignored(self) -> None:
        events, ignored = extract_whatsapp_events(
            {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.missing-from"}]}}]}]}
        )
        self.assertEqual(events, [])
        self.assertEqual(ignored, 1)

    def test_valid_text_message_becomes_context_event(self) -> None:
        events, ignored = extract_whatsapp_events(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {
                                            "id": "wamid.1",
                                            "from": "15551234567",
                                            "type": "text",
                                            "text": {"body": "urgent hello"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        )
        self.assertEqual(ignored, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].id, "whatsapp-wamid.1")
        self.assertEqual(events[0].source, "mcp_whatsapp")
        self.assertIn("urgent", events[0].body)


if __name__ == "__main__":
    unittest.main()
