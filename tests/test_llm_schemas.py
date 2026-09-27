from __future__ import annotations

import unittest
from typing import Any

from otomekairo.llm.contracts import (
    DECISION_COMPARISON_SCOPE_KINDS,
    LLMError,
    MEMORY_TYPE_VALUES,
    RECALL_PACK_SECTION_NAMES,
    SCOPE_TYPE_VALUES,
)
from otomekairo.llm.schemas import (
    SCHEMA_DIALECT_KEYS,
    all_response_formats,
    autonomous_step_response_format,
    decision_response_format,
    materialize_provider_open_maps,
    memory_interpretation_response_format,
    recall_pack_selection_response_format,
)


def _root_schema(response_format: dict[str, Any]) -> dict[str, Any]:
    return response_format["json_schema"]["schema"]


class LLMSchemaTests(unittest.TestCase):
    def test_all_response_formats_use_json_schema_wrapper(self) -> None:
        formats = all_response_formats()
        self.assertGreaterEqual(len(formats), 17)
        for name, response_format in formats.items():
            with self.subTest(name=name):
                self.assertEqual(response_format["type"], "json_schema")
                json_schema = response_format["json_schema"]
                self.assertTrue(json_schema["strict"])
                self.assertRegex(json_schema["name"], r"^[A-Za-z0-9_]+$")
                self.assertEqual(json_schema["schema"]["type"], "object")

    def test_schemas_stay_in_gemini_subset(self) -> None:
        for name, response_format in all_response_formats().items():
            with self.subTest(name=name):
                self._assert_dialect(_root_schema(response_format), path=name)

    def test_closed_objects_require_every_property(self) -> None:
        for name, response_format in all_response_formats().items():
            with self.subTest(name=name):
                self._assert_closed_objects(_root_schema(response_format), path=name)

    def test_recall_pack_candidate_refs_reject_empty_array(self) -> None:
        schema = _root_schema(recall_pack_selection_response_format())
        candidate_refs = schema["properties"]["section_selection"]["items"]["properties"]["candidate_refs"]
        self.assertEqual(candidate_refs["minItems"], 1)
        section_name = schema["properties"]["section_selection"]["items"]["properties"]["section_name"]
        self.assertEqual(set(section_name["enum"]), set(RECALL_PACK_SECTION_NAMES))

    def test_memory_subject_hint_is_string(self) -> None:
        schema = _root_schema(memory_interpretation_response_format())
        unit = schema["properties"]["candidate_memory_units"]["items"]
        subject_hint = unit["properties"]["subject_hint"]
        self.assertEqual(subject_hint["type"], "string")
        self.assertEqual(set(unit["properties"]["memory_type"]["enum"]), MEMORY_TYPE_VALUES)
        self.assertEqual(set(unit["properties"]["scope"]["enum"]), SCOPE_TYPE_VALUES)
        qualifiers_hint = unit["properties"]["qualifiers_hint"]
        self.assertEqual(qualifiers_hint["type"], "string")
        self.assertNotIn("additionalProperties", qualifiers_hint)

    def test_capability_input_is_json_object_text(self) -> None:
        for scope in ("full", "self_activity", "outward_speech"):
            with self.subTest(scope=scope):
                schema = _root_schema(decision_response_format(comparison_scope=scope))
                capability_input = schema["properties"]["capability_request"]["properties"]["input"]
                self.assertEqual(capability_input["type"], "string")
                self.assertNotIn("additionalProperties", capability_input)
        step_schema = _root_schema(autonomous_step_response_format())
        step_input = step_schema["properties"]["action"]["properties"]["capability_request"]["properties"]["input"]
        self.assertEqual(step_input["type"], "string")

    def test_open_map_text_materializes_to_object(self) -> None:
        decision = {
            "capability_request": {
                "capability_id": "vision.capture",
                "input": '{"vision_source_id":"vision_source:main","mode":"still"}',
            }
        }
        materialize_provider_open_maps(decision, schema_name="decision")
        self.assertEqual(
            decision["capability_request"]["input"],
            {"vision_source_id": "vision_source:main", "mode": "still"},
        )

        step = {
            "action": {
                "capability_request": {
                    "capability_id": "mcp.call_tool",
                    "input": '{"mcp_server_id":"elyth","tool_name":"get_notifications","arguments":{}}',
                }
            }
        }
        materialize_provider_open_maps(step, schema_name="autonomous_step")
        self.assertEqual(step["action"]["capability_request"]["input"]["tool_name"], "get_notifications")

        memory = {
            "candidate_memory_units": [
                {"qualifiers_hint": '{"polarity":"positive"}'},
            ]
        }
        materialize_provider_open_maps(memory, schema_name="memory_interpretation")
        self.assertEqual(memory["candidate_memory_units"][0]["qualifiers_hint"], {"polarity": "positive"})

    def test_open_map_text_rejects_raw_object_and_non_object_json(self) -> None:
        raw_object = {"capability_request": {"capability_id": "vision.capture", "input": {"mode": "still"}}}
        with self.assertRaisesRegex(LLMError, "文字列である必要があります"):
            materialize_provider_open_maps(raw_object, schema_name="decision_self_activity")
        non_object = {"capability_request": {"capability_id": "vision.capture", "input": "[1]"}}
        with self.assertRaisesRegex(LLMError, "JSON object である必要があります"):
            materialize_provider_open_maps(non_object, schema_name="decision_outward_speech")
        broken = {"capability_request": {"capability_id": "vision.capture", "input": "{not json"}}
        with self.assertRaisesRegex(LLMError, "JSON object として読めません"):
            materialize_provider_open_maps(broken, schema_name="decision")

    def test_decision_kind_follows_comparison_scope(self) -> None:
        for scope, kinds in DECISION_COMPARISON_SCOPE_KINDS.items():
            with self.subTest(scope=scope):
                schema = _root_schema(decision_response_format(comparison_scope=scope))
                self.assertEqual(set(schema["properties"]["kind"]["enum"]), set(kinds))

    def _assert_dialect(self, node: Any, *, path: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                self._assert_dialect(item, path=f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return
        extra = set(node) - SCHEMA_DIALECT_KEYS
        if "type" in node or "properties" in node or "items" in node:
            self.assertFalse(extra, f"{path} に方言外キーがある: {sorted(extra)}")
        for key, value in node.items():
            self._assert_dialect(value, path=f"{path}.{key}")

    def _assert_closed_objects(self, node: Any, *, path: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                self._assert_closed_objects(item, path=f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return
        types = node.get("type")
        type_values = {types} if isinstance(types, str) else set(types or [])
        if "object" in type_values:
            additional = node.get("additionalProperties")
            self.assertIs(additional, False, f"{path} の additionalProperties は false である必要があります")
            properties = node.get("properties")
            required = node.get("required")
            self.assertIsInstance(properties, dict, f"{path} の閉じた object に properties が無い")
            self.assertEqual(set(required or []), set(properties), f"{path} の required が properties と一致しない")
        for key, value in node.items():
            self._assert_closed_objects(value, path=f"{path}.{key}")
