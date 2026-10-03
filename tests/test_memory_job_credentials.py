from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from otomekairo.memory.consolidator import MemoryConsolidator


class MemoryJobCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "selected_persona_id": "persona:test",
            "selected_memory_set_id": "memory:test",
            "selected_model_preset_id": "model:test",
            "personas": {"persona:test": {"display_name": "Test"}},
            "model_presets": {"model:test": {"model": "mock", "api_key": "test-generator-credential"}},
            "memory_sets": {"memory:test": {"embedding": {
                "model": "mock-embedding", "api_key": "test-embedding-credential",
            }}},
        }
        self.consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        self.consolidator.store = SimpleNamespace(read_state=lambda: self.state)

    def job(self) -> dict:
        return self.consolidator._build_postprocess_job(
            state=self.state, cycle_id="cycle:test", finished_at="2026-10-01T12:00:00+09:00",
            episode={}, memory_actions=[], correction_context=None,
        )

    def test_queued_payload_excludes_credentials_and_execution_resolves_current_keys(self) -> None:
        job = self.job()
        serialized = json.dumps(job)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("test-generator-credential", serialized)
        self.assertNotIn("test-embedding-credential", serialized)
        self.state["model_presets"]["model:test"].update(model="new-model", api_key="test-rotated-credential")
        resolved = self.consolidator._resolve_postprocess_state(job["state_snapshot"])
        self.assertEqual(resolved["model_presets"]["model:test"]["model"], "mock")
        self.assertEqual(resolved["model_presets"]["model:test"]["api_key"], "test-rotated-credential")
        self.assertEqual(resolved["memory_sets"]["memory:test"]["embedding"]["api_key"], "test-embedding-credential")
        self.assertNotIn("api_key", json.dumps(job))

    def test_removed_configuration_fails_without_switching_to_selected_model(self) -> None:
        job = self.job()
        self.state["selected_model_preset_id"] = "model:other"
        self.state["model_presets"] = {"model:other": {"model": "mock"}}
        with self.assertRaises(KeyError):
            self.consolidator._resolve_postprocess_state(job["state_snapshot"])


if __name__ == "__main__":
    unittest.main()
