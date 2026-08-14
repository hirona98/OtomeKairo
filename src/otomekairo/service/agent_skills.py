from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from otomekairo.agent_skill_runner import DEFAULT_SCRIPT_LIMITS
from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.contracts import LLMError
from otomekairo.service.common import debug_log


SELF_INITIATED_SOURCE_KINDS = frozenset({"wake", "background_thinking"})
PERSON_ORIGIN_SOURCE_KINDS = frozenset({"user_message"})


def origin_source_kind_from_capability_request(
    capability_request_summary: dict[str, Any] | None,
) -> str | None:
    if not isinstance(capability_request_summary, dict):
        return None
    source = capability_request_summary.get("source_current_input")
    if not isinstance(source, dict):
        return None
    value = source.get("source_kind")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_origin_source_kind(
    *,
    current_input: CurrentInput,
    trigger_kind: str,
    run: dict[str, Any] | None = None,
    origin_source_kind: str | None = None,
) -> str | None:
    if isinstance(origin_source_kind, str) and origin_source_kind.strip():
        return origin_source_kind.strip()
    if isinstance(run, dict):
        value = run.get("origin_kind")
        if isinstance(value, str) and value.strip():
            return value.strip()
    source_kind = current_input.source_kind
    if source_kind in SELF_INITIATED_SOURCE_KINDS or source_kind in PERSON_ORIGIN_SOURCE_KINDS:
        return source_kind
    if trigger_kind in SELF_INITIATED_SOURCE_KINDS or trigger_kind in PERSON_ORIGIN_SOURCE_KINDS:
        return trigger_kind
    return None


def resolve_agent_skill_host_authorization(
    *,
    current_input: CurrentInput,
    trigger_kind: str,
    run: dict[str, Any] | None = None,
    origin_source_kind: str | None = None,
) -> dict[str, str]:
    origin = resolve_origin_source_kind(
        current_input=current_input,
        trigger_kind=trigger_kind,
        run=run,
        origin_source_kind=origin_source_kind,
    )
    if trigger_kind in SELF_INITIATED_SOURCE_KINDS or origin in SELF_INITIATED_SOURCE_KINDS:
        return {
            "kind": "current_individual_decision",
            "summary_text": "いまの個がこの判断で働きかける許可である。",
        }
    if current_input.sender_kind == "person" and current_input.response_target_refs:
        return {
            "kind": "person_request",
            "summary_text": "人物の明示依頼がある。",
        }
    if origin in PERSON_ORIGIN_SOURCE_KINDS or current_input.response_target_refs:
        return {
            "kind": "person_request",
            "summary_text": "人物の依頼から続く作業である。",
        }
    return {
        "kind": "none",
        "summary_text": "ホストの公開許可はこの入力からは立っていない。",
    }


class ServiceAgentSkillsMixin:
    _AGENT_SKILL_RUNNER_CLIENT_ID = "local:agent-skill-runner"

    def _agent_skill_script_execution_available(self) -> bool:
        with self._runtime_state_lock:
            registry = self._agent_skill_registry
        return any(
            any(resource.kind == "script" for skill in source.skills.values() for resource in skill.resources.values())
            for source in registry.sources.values()
        )

    def _build_agent_skill_context(
        self,
        *,
        model_config: dict[str, Any],
        current_input: CurrentInput,
        trigger_kind: str,
        capability_decision_view: list[dict[str, Any]] | None,
        run: dict[str, Any] | None = None,
        prior_activation: dict[str, Any] | None = None,
        origin_source_kind: str | None = None,
    ) -> dict[str, Any] | None:
        with self._runtime_state_lock:
            registry = self._agent_skill_registry
        catalog = registry.catalog()
        if not catalog:
            return None

        host_authorization = resolve_agent_skill_host_authorization(
            current_input=current_input,
            trigger_kind=trigger_kind,
            run=run,
            origin_source_kind=origin_source_kind,
        )
        selection = self.llm.generate_agent_skill_selection(
            model_config=model_config,
            selection_context={
                "current_input": current_input.to_prompt_payload(),
                "trigger_kind": trigger_kind,
                "run": run,
                "prior_activation": prior_activation,
                "host_authorization": host_authorization,
                "capability_decision_view": capability_decision_view or [],
                "allowed_skill_ids": [entry["skill_id"] for entry in catalog],
                "skill_catalog": catalog,
            },
        )
        selected_ids = list(selection["selected_skill_ids"])
        unknown_ids = sorted(set(selected_ids) - set(registry.skills))
        if unknown_ids:
            raise LLMError(
                "AgentSkillSelection が catalog にない skill_id を返しました: "
                + ", ".join(unknown_ids)
            )
        if not selected_ids:
            return None

        active_ids = list(selected_ids)
        active_set = set(active_ids)
        selected_resources: dict[str, dict[str, dict[str, Any]]] = {}
        material_reasons: list[str] = []
        while True:
            linked_candidates = sorted(
                {
                    linked_id
                    for skill_id in active_ids
                    for linked_id in registry.require_skill(skill_id).linked_skill_names
                    if linked_id in registry.skills and linked_id not in active_set
                }
            )
            resource_candidates = [
                {
                    "skill_id": skill_id,
                    **resource.catalog_entry(),
                }
                for skill_id in active_ids
                for resource in sorted(
                    registry.require_skill(skill_id).resources.values(),
                    key=lambda item: item.relative_path,
                )
                if (
                    resource.kind == "resource"
                    and resource.text_content is not None
                    and resource.relative_path not in selected_resources.get(skill_id, {})
                )
            ]
            if not linked_candidates and not resource_candidates:
                break
            active_skills: list[dict[str, Any]] = []
            for skill_id in active_ids:
                payload = registry.require_skill(skill_id).instruction_payload()
                payload["selected_resources"] = list(
                    selected_resources.get(skill_id, {}).values()
                )
                active_skills.append(payload)
            material = self.llm.generate_agent_skill_material_selection(
                model_config=model_config,
                selection_context={
                    "current_input": current_input.to_prompt_payload(),
                    "trigger_kind": trigger_kind,
                    "run": run,
                    "prior_activation": prior_activation,
                    "host_authorization": host_authorization,
                    "active_skills": active_skills,
                    "allowed_additional_skill_ids": linked_candidates,
                    "allowed_resource_reads": [
                        {
                            "skill_id": candidate["skill_id"],
                            "path": candidate["path"],
                        }
                        for candidate in resource_candidates
                    ],
                    "additional_skill_candidates": linked_candidates,
                    "resource_candidates": resource_candidates,
                },
            )
            additional_ids = list(material["additional_skill_ids"])
            invalid_additional = sorted(set(additional_ids) - set(linked_candidates))
            if invalid_additional:
                raise LLMError(
                    "AgentSkillMaterialSelection が候補にない skill_id を返しました: "
                    + ", ".join(invalid_additional)
                )
            candidate_pairs = {
                (candidate["skill_id"], candidate["path"])
                for candidate in resource_candidates
            }
            requested_pairs = {
                (entry["skill_id"], entry["path"])
                for entry in material["resource_reads"]
            }
            invalid_pairs = sorted(requested_pairs - candidate_pairs)
            if invalid_pairs:
                raise LLMError(
                    "AgentSkillMaterialSelection が候補にない resource を返しました: "
                    + ", ".join(f"{skill_id}/{path}" for skill_id, path in invalid_pairs)
                )

            progressed = False
            for skill_id in additional_ids:
                if skill_id not in active_set:
                    active_set.add(skill_id)
                    active_ids.append(skill_id)
                    progressed = True
            for skill_id, relative_path in sorted(requested_pairs):
                resource = registry.require_skill(skill_id).resources[relative_path]
                selected_resources.setdefault(skill_id, {})[relative_path] = {
                    "path": relative_path,
                    "sha256": resource.sha256,
                    "content": resource.text_content,
                }
                progressed = True
            material_reasons.append(material["reason_summary"].strip())
            if not progressed:
                break

        skills_payload: list[dict[str, Any]] = []
        for skill_id in active_ids:
            skill = registry.require_skill(skill_id)
            payload = skill.instruction_payload()
            payload["selected_resources"] = list(
                selected_resources.get(skill_id, {}).values()
            )
            skills_payload.append(payload)
        return {
            "selected_skill_ids": active_ids,
            "selection_reason_summary": selection["reason_summary"].strip(),
            "material_reason_summaries": material_reasons,
            "host_authorization": host_authorization,
            "skills": skills_payload,
        }

    def _agent_skill_activation_summary(
        self,
        agent_skill_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(agent_skill_context, dict):
            return None
        skills = agent_skill_context.get("skills")
        if not isinstance(skills, list):
            return None
        return {
            "selected_skill_ids": list(agent_skill_context.get("selected_skill_ids") or []),
            "skills": [
                {
                    "source_id": skill.get("source_id"),
                    "skill_id": skill.get("skill_id"),
                    "sha256": skill.get("sha256"),
                }
                for skill in skills
                if isinstance(skill, dict)
            ],
        }

    def _dispatch_agent_skill_script_capability(
        self,
        *,
        memory_set_id: str,
        input_payload: dict[str, Any],
        current_time: str,
        goal_summary: str,
        wait_for_response: bool,
        manifest: dict[str, Any],
        source_current_input: dict[str, Any] | None,
        assistant_message_target_client_id: str | None,
        track_ongoing_action: bool,
        autonomous_run_id: str | None,
    ) -> dict[str, Any] | None:
        source_id = str(input_payload.get("source_id") or "").strip()
        skill_id = str(input_payload.get("skill_id") or "").strip()
        with self._runtime_state_lock:
            registry = self._agent_skill_registry
        source = registry.sources.get(source_id)
        skill = registry.skills.get(skill_id)
        if source is None or skill is None or skill.source_id != source_id:
            raise ValueError("Agent Skill source or skill is not available.")
        if input_payload.get("skill_sha256") != skill.sha256:
            raise ValueError("Agent Skill snapshot digest does not match.")
        script_path = input_payload.get("script_path")
        script = skill.resources.get(script_path) if isinstance(script_path, str) else None
        if script is None or script.kind != "script":
            raise ValueError("Agent Skill script_path is not available.")
        if source.definition["enabled"] is not True:
            raise ValueError("Agent Skill source is not enabled.")

        timeout_ms = min(
            int(manifest["timeout_ms"]),
            int(DEFAULT_SCRIPT_LIMITS["wall_time_seconds"]) * 1000 + 5000,
        )
        action_seed = None
        if track_ongoing_action:
            action_seed = self._begin_capability_ongoing_action(
                memory_set_id=memory_set_id,
                capability_id="agent_skill.run_script",
                manifest=manifest,
                current_time=current_time,
                timeout_ms=timeout_ms,
                goal_summary=goal_summary,
            )
        request_record = self._build_capability_request_record(
            memory_set_id=memory_set_id,
            capability_id="agent_skill.run_script",
            target_client_id=self._AGENT_SKILL_RUNNER_CLIENT_ID,
            input_payload=input_payload,
            timeout_ms=timeout_ms,
            current_time=current_time,
            manifest=manifest,
            action_seed=action_seed,
            wait_for_response=wait_for_response,
            source_current_input=source_current_input,
            assistant_message_target_client_id=assistant_message_target_client_id,
            autonomous_run_id=autonomous_run_id,
        )
        pending = {
            "event": threading.Event(),
            "response": None,
            "request_record": request_record,
            "wait_for_response": wait_for_response,
        }
        with self._capability_request_lock:
            self._pending_capability_requests[request_record["request_id"]] = pending
        self._set_capability_runtime_busy(request_record=request_record)

        runner_request = {
            "source_definition": source.definition,
            "source_id": source_id,
            "skill_id": skill_id,
            "skill_sha256": skill.sha256,
            "script_path": script.relative_path,
            "args": input_payload["args"],
            "stdin_text": input_payload["stdin_text"],
            "run_dir": str(
                Path(self.store.root_dir)
                / "agent-skill-runs"
                / request_record["request_id"].replace(":", "-")
            ),
        }

        def execute() -> None:
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "otomekairo.agent_skill_runner"],
                    input=json.dumps(runner_request, ensure_ascii=False),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=(timeout_ms / 1000.0) + 2.0,
                    check=False,
                )
                if completed.returncode != 0 or completed.stderr:
                    raise RuntimeError("Agent Skill runner process failed.")
                result_payload = json.loads(completed.stdout)
                if not isinstance(result_payload, dict):
                    raise RuntimeError("Agent Skill runner returned an invalid result.")
            except Exception as exc:  # noqa: BLE001
                result_payload = {
                    "status": "failed",
                    "exit_code": None,
                    "status_text": "Agent Skill runner process failed.",
                    "stdout": "",
                    "stderr": "",
                    "client_context": {},
                    "error": str(exc),
                }
            try:
                self._submit_async_capability_result_response(
                    state=self.store.read_state(),
                    capability_id="agent_skill.run_script",
                    request_id=request_record["request_id"],
                    client_id=self._AGENT_SKILL_RUNNER_CLIENT_ID,
                    result_payload=result_payload,
                    accepted_at=self._now_iso(),
                    log_channel="AgentSkillRunner",
                    accepted_detail=(
                        f"status={result_payload.get('status')} exit_code={result_payload.get('exit_code')} "
                        f"output_chars={len(str(result_payload.get('stdout') or '')) + len(str(result_payload.get('stderr') or ''))}"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._clear_capability_runtime_busy(
                    capability_id="agent_skill.run_script",
                    request_id=request_record["request_id"],
                    action_id=request_record.get("action_id"),
                )
                self._mark_capability_runtime_failure(
                    capability_id="agent_skill.run_script",
                    current_time=self._now_iso(),
                    failure_summary="Agent Skill runner result could not be accepted.",
                )
                debug_log(
                    "AgentSkillRunner",
                    f"result acceptance failed error={type(exc).__name__}",
                    level="ERROR",
                )

        thread = threading.Thread(
            target=execute,
            name="otomekairo-agent-skill-runner",
            daemon=True,
        )
        thread.start()
        if not wait_for_response:
            return {
                "request_record": request_record,
                "capability_request_summary": self._capability_request_summary(request_record),
                "ongoing_action_transition_summary": self._capability_ongoing_action_transition_summary(
                    request_record=request_record,
                    current_time=current_time,
                    final_state="waiting_result",
                    reason_summary="Agent Skill script を専用 runner process へ配送し、結果待ちに入った。",
                    reason_code="request_dispatched",
                    transition_source="capability_dispatch",
                ),
            }
        pending["event"].wait(timeout=(timeout_ms / 1000.0) + 3.0)
        with self._capability_request_lock:
            response = pending["response"]
            self._pending_capability_requests.pop(request_record["request_id"], None)
        if not isinstance(response, dict):
            self._clear_capability_runtime_busy(
                capability_id="agent_skill.run_script",
                request_id=request_record["request_id"],
                action_id=request_record.get("action_id"),
            )
            self._mark_capability_runtime_failure(
                capability_id="agent_skill.run_script",
                current_time=self._now_iso(),
                failure_summary="Agent Skill runner result timed out.",
                unavailable_reason="request_timeout",
            )
            if track_ongoing_action:
                self._finish_capability_ongoing_action(
                    request_record=request_record,
                    current_time=self._now_iso(),
                    terminal_kind="interrupted",
                    reason_code="request_timeout",
                    terminal_reason="Agent Skill runner result の待機が timeout した。",
                    final_step_summary="Agent Skill script の結果を受け取れなかった。",
                    transition_source="capability_dispatch",
                    detail_summary="agent_skill.run_script timed out.",
                )
            raise ValueError("Agent Skill runner result timed out.")
        return response
