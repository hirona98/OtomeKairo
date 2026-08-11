from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from otomekairo.agent_skills import AgentSkillError, AgentSkillRegistry
from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.agent_skills import ServiceAgentSkillsMixin
from otomekairo.service.app import OtomeKairoService


def _source_definition(root: Path, *, execution_enabled: bool) -> dict:
    return {
        "source_id": "test-source",
        "display_name": "Test Skills",
        "enabled": True,
        "root_path": str(root),
        "script_execution": {
            "enabled": execution_enabled,
            "runtimes": (
                [
                    {
                        "runtime_id": "python",
                        "executable": sys.executable,
                        "prefix_args": [],
                    }
                ]
                if execution_enabled
                else []
            ),
            "limits": (
                {
                    "wall_time_seconds": 5,
                    "cpu_time_seconds": 3,
                    "memory_bytes": 268435456,
                    "max_processes": 8,
                    "max_open_files": 64,
                    "max_file_bytes": 1048576,
                    "max_output_bytes": 1048576,
                }
                if execution_enabled
                else None
            ),
        },
    }


def _write_skill(root: Path) -> Path:
    skill_dir = root / "echo-skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: echo-skill\ndescription: Echo structured input for tests.\n---\n"
        "Run scripts/echo.py when an exact echo is needed.\n",
        encoding="utf-8",
    )
    script = scripts_dir / "echo.py"
    script.write_text(
        "import json, sys\nprint(json.dumps({'args': sys.argv[1:], 'stdin': sys.stdin.read()}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return skill_dir


class AgentSkillRegistryTests(unittest.TestCase):
    def test_loads_metadata_body_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "skills"
            root.mkdir()
            _write_skill(root)
            definition = _source_definition(root, execution_enabled=False)

            registry = AgentSkillRegistry.load({"test-source": definition})

            skill = registry.require_skill("echo-skill")
            self.assertIn("exact echo", skill.body)
            self.assertEqual(skill.resources["scripts/echo.py"].kind, "script")
            self.assertEqual(registry.catalog()[0]["skill_id"], "echo-skill")

    def test_rejects_symlinked_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "skills"
            root.mkdir()
            _write_skill(root)
            linked_root = base / "linked-skills"
            linked_root.symlink_to(root, target_is_directory=True)
            definition = _source_definition(linked_root, execution_enabled=False)

            with self.assertRaises(AgentSkillError) as raised:
                AgentSkillRegistry.load({"test-source": definition})

            self.assertEqual(raised.exception.code, "agent_skill_symlink_forbidden")

    def test_dedicated_runner_executes_snapshot_with_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "skills"
            root.mkdir()
            _write_skill(root)
            definition = _source_definition(root, execution_enabled=True)
            registry = AgentSkillRegistry.load({"test-source": definition})
            skill = registry.require_skill("echo-skill")
            request = {
                "source_definition": definition,
                "source_id": "test-source",
                "skill_id": "echo-skill",
                "skill_sha256": skill.sha256,
                "script_path": "scripts/echo.py",
                "runtime_id": "python",
                "args": ["one", "two"],
                "stdin_text": "hello",
                "run_dir": str(base / "run"),
            }

            completed = subprocess.run(
                [sys.executable, "-m", "otomekairo.agent_skill_runner"],
                input=json.dumps(request),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                },
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.stderr, "")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn('"args": ["one", "two"]', result["stdout"])
            self.assertIn('"stdin": "hello"', result["stdout"])

    def test_config_api_replaces_and_reloads_registry_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "skills"
            root.mkdir()
            _write_skill(root)
            service = OtomeKairoService(base / "data")
            state = service.store.read_state()
            state["console_access_token"] = "token"
            service.store.write_state(state)
            definition = _source_definition(root, execution_enabled=False)

            response = service.replace_agent_skill_sources_editor_state(
                "token",
                {"agent_skill_sources": [definition]},
            )
            initial_inspection = service.inspect_agent_skills("token")
            (root / "echo-skill" / "SKILL.md").write_text(
                "---\nname: echo-skill\ndescription: Changed description.\n---\nChanged body.\n",
                encoding="utf-8",
            )
            reloaded = service.reload_agent_skill_sources("token")

            self.assertEqual(response["agent_skill_sources"][0]["source_id"], "test-source")
            self.assertEqual(initial_inspection["skill_count"], 1)
            self.assertNotEqual(
                initial_inspection["sources"][0]["sha256"],
                reloaded["sources"][0]["sha256"],
            )

    def test_llm_selection_loads_only_selected_materials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "skills"
            root.mkdir()
            root_skill = root / "root-skill"
            root_skill.mkdir()
            (root_skill / "SKILL.md").write_text(
                "---\nname: root-skill\ndescription: Root workflow.\n---\n"
                "Use [child](../child-skill/SKILL.md) and references/guide.md.\n",
                encoding="utf-8",
            )
            references = root_skill / "references"
            references.mkdir()
            (references / "guide.md").write_text("selected guide", encoding="utf-8")
            child = root / "child-skill"
            child.mkdir()
            (child / "SKILL.md").write_text(
                "---\nname: child-skill\ndescription: Child workflow.\n---\nChild instructions.\n",
                encoding="utf-8",
            )
            definition = _source_definition(root, execution_enabled=False)

            class FakeLlm:
                def generate_agent_skill_selection(self, **_kwargs):
                    return {"selected_skill_ids": ["root-skill"], "reason_summary": "needed"}

                def generate_agent_skill_material_selection(self, **_kwargs):
                    return {
                        "additional_skill_ids": ["child-skill"],
                        "resource_reads": [{"skill_id": "root-skill", "path": "references/guide.md"}],
                        "done": True,
                        "reason_summary": "read guide and child",
                    }

            class Subject(ServiceAgentSkillsMixin):
                def __init__(self):
                    self._runtime_state_lock = threading.RLock()
                    self._agent_skill_registry = AgentSkillRegistry.load({"test-source": definition})
                    self.llm = FakeLlm()

            context = Subject()._build_agent_skill_context(
                model_config={"model": "real-model"},
                current_input=CurrentInput(
                    sender_kind="person",
                    sender_ref="person:test",
                    source_kind="user_message",
                    response_target_refs=("person:test",),
                    interaction_context=None,
                    text="perform the workflow",
                ),
                trigger_kind="user_message",
                capability_decision_view=[],
            )

            self.assertEqual(context["selected_skill_ids"], ["root-skill", "child-skill"])
            self.assertEqual(
                context["skills"][0]["selected_resources"][0]["content"],
                "selected guide",
            )

    def test_run_script_capability_uses_local_runner_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "skills"
            root.mkdir()
            _write_skill(root)
            service = OtomeKairoService(base / "data")
            state = service.store.read_state()
            state["console_access_token"] = "token"
            service.store.write_state(state)
            definition = _source_definition(root, execution_enabled=True)
            service.replace_agent_skill_sources_editor_state(
                "token",
                {"agent_skill_sources": [definition]},
            )
            state = service.store.read_state()
            skill = service._agent_skill_registry.require_skill("echo-skill")
            current_time = service._now_iso()

            decision_view = service._build_capability_decision_view(
                state=state,
                current_time=current_time,
            )
            result = service._dispatch_capability_request(
                memory_set_id=state["selected_memory_set_id"],
                capability_id="agent_skill.run_script",
                input_payload={
                    "source_id": "test-source",
                    "skill_id": "echo-skill",
                    "skill_sha256": skill.sha256,
                    "script_path": "scripts/echo.py",
                    "runtime_id": "python",
                    "args": ["capability"],
                    "stdin_text": "input",
                },
                current_time=current_time,
                goal_summary="test runner",
                wait_for_response=True,
                component="Test",
                track_ongoing_action=False,
            )

            capability = next(item for item in decision_view if item["id"] == "agent_skill.run_script")
            self.assertTrue(capability["available"])
            self.assertEqual(result["status"], "completed")
            self.assertIn('"args": ["capability"]', result["stdout"])


if __name__ == "__main__":
    unittest.main()
