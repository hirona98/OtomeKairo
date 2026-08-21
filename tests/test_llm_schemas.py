from __future__ import annotations

import unittest
from typing import Any

from otomekairo.llm.contracts import (
    DECISION_COMPARISON_SCOPE_KINDS,
    MEMORY_TYPE_VALUES,
    RECALL_PACK_SECTION_NAMES,
    SCOPE_TYPE_VALUES,
)
from otomekairo.llm.schemas import (
    SCHEMA_DIALECT_KEYS,
    all_response_formats,
    decision_response_format,
    event_evidence_response_format,
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

    def test_event_evidence_array_has_output_cap(self) -> None:
        schema = _root_schema(event_evidence_response_format())
        self.assertEqual(schema["properties"]["evidence"]["maxItems"], 16)

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
        self.assertTrue(unit["properties"]["qualifiers_hint"]["additionalProperties"])
        self.assertNotIn("properties", unit["properties"]["qualifiers_hint"])

    def test_capability_input_is_open_object(self) -> None:
        schema = _root_schema(decision_response_format(comparison_scope="full"))
        capability_input = schema["properties"]["capability_request"]["properties"]["input"]
        self.assertEqual(capability_input["type"], "object")
        self.assertTrue(capability_input["additionalProperties"])
        self.assertNotIn("properties", capability_input)

    def test_decision_kind_follows_comparison_scope(self) -> None:
        for scope, kinds in DECISION_COMPARISON_SCOPE_KINDS.items():
            with self.subTest(scope=scope):
                schema = _root_schema(decision_response_format(comparison_scope=scope))
                self.assertEqual(set(schema["properties"]["kind"]["enum"]), set(kinds))

    def test_outward_decision_disallows_self_activity_payloads_in_schema(self) -> None:
        schema = _root_schema(decision_response_format(comparison_scope="outward_speech"))

        self.assertEqual(schema["properties"]["capability_request"], {"type": "null"})
        self.assertEqual(schema["properties"]["autonomous_run"], {"type": "null"})

    def test_decision_foreground_refs_follow_runtime_workspace(self) -> None:
        schema = _root_schema(
            decision_response_format(
                comparison_scope="outward_speech",
                workspace_factor_refs=["current_input:user_message", "world_state:visual"],
            )
        )
        foreground = schema["properties"]["foreground_selection"]["properties"]

        self.assertEqual(
            set(foreground["primary_factor_ref"]["enum"]),
            {"current_input:user_message", "world_state:visual"},
        )
        self.assertEqual(
            set(foreground["supporting_factor_refs"]["items"]["enum"]),
            {"current_input:user_message", "world_state:visual"},
        )
        suppressed_factor = foreground["suppressed_factors"]["items"]["properties"]["factor_ref"]
        self.assertEqual(
            set(suppressed_factor["enum"]),
            {"current_input:user_message", "world_state:visual"},
        )

    def test_decision_foreground_refs_require_empty_selection_without_candidates(self) -> None:
        schema = _root_schema(
            decision_response_format(
                comparison_scope="outward_speech",
                workspace_factor_refs=[],
            )
        )
        foreground = schema["properties"]["foreground_selection"]["properties"]

        self.assertEqual(foreground["primary_factor_ref"], {"type": "null"})
        self.assertEqual(foreground["supporting_factor_refs"]["maxItems"], 0)
        self.assertEqual(foreground["suppressed_factors"]["maxItems"], 0)

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
            self.assertIn(additional, {True, False}, f"{path} の additionalProperties が無い")
            if additional is False:
                properties = node.get("properties")
                required = node.get("required")
                self.assertIsInstance(properties, dict, f"{path} の閉じた object に properties が無い")
                self.assertEqual(set(required or []), set(properties), f"{path} の required が properties と一致しない")
        for key, value in node.items():
            self._assert_closed_objects(value, path=f"{path}.{key}")
