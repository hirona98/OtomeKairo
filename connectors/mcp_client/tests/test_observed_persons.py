import json
import unittest

from otomekairo_mcp_client_connector.observed_persons import (
    content_summary,
    observed_persons_from_mcp_result,
)


class McpObservedPersonTests(unittest.TestCase):
    def test_extracts_actor_and_author_handles(self) -> None:
        persons = observed_persons_from_mcp_result(
            mcp_server_id="elyth",
            content=[],
            structured_content={
                "data": {
                    "items": [
                        {
                            "type": "post.reply_received",
                            "actor": {
                                "type": "aituber",
                                "id": "4b2365cd-57f0-4f40-8042-762530c10fab",
                                "display_name": "一ノ瀬 凜",
                                "handle": "rin_ichinose",
                            },
                        },
                        {
                            "author": {
                                "display_name": "ネクタリカ",
                                "handle": "nectalica",
                            }
                        },
                    ]
                }
            },
        )

        self.assertEqual(
            persons,
            [
                {
                    "person_ref": "person:mcp:elyth:rin_ichinose",
                    "display_name": "一ノ瀬 凜",
                },
                {
                    "person_ref": "person:mcp:elyth:nectalica",
                    "display_name": "ネクタリカ",
                },
            ],
        )

    def test_does_not_treat_self_profile_or_system_actor_as_person(self) -> None:
        persons = observed_persons_from_mcp_result(
            mcp_server_id="elyth",
            content=[],
            structured_content={
                "self": {
                    "profile": {
                        "display_name": "レイカ-P1",
                        "handle": "leika_p1",
                    }
                },
                "actor": {
                    "type": "system",
                    "display_name": "ELYTH",
                },
            },
        )

        self.assertEqual(persons, [])

    def test_reads_actor_from_content_json_text(self) -> None:
        persons = observed_persons_from_mcp_result(
            mcp_server_id="elyth",
            content=[
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "actor": {
                                "display_name": "一ノ瀬 凜",
                                "handle": "rin_ichinose",
                            }
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            structured_content=None,
        )

        self.assertEqual(
            persons,
            [
                {
                    "person_ref": "person:mcp:elyth:rin_ichinose",
                    "display_name": "一ノ瀬 凜",
                }
            ],
        )

    def test_summary_uses_structured_content_when_content_is_empty(self) -> None:
        summary = content_summary([], {"data": {"items": []}})
        self.assertIn("items", summary)
