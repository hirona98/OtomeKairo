from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.contracts import LLMError


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
            "kind": "person_input",
            "summary_text": "人物発話が起点である。実行する目的と許可の範囲は、その発話と会話文脈の意味から判断する。",
        }
    if origin in PERSON_ORIGIN_SOURCE_KINDS or current_input.response_target_refs:
        return {
            "kind": "person_input",
            "summary_text": "人物発話に由来する入力である。実行する目的と許可の範囲は、起点の発話と継続中の作業から判断する。",
        }
    return {
        "kind": "none",
        "summary_text": "ホストの公開許可はこの入力からは立っていない。",
    }


class ServiceAgentSkillsMixin:
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
        recent_turns: list[dict[str, Any]] | None = None,
        work_log: list[dict[str, Any]] | None = None,
        orientation_context: dict[str, Any] | None = None,
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
                "recent_turns": recent_turns or [],
                "work_log": work_log or [],
                "orientation_context": orientation_context or {"periodic_thought_topics": []},
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
                    resource.text_content is not None
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
                    "orientation_context": orientation_context or {"periodic_thought_topics": []},
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
            invalid_additional = sorted(set(additional_ids) - set(linked_candidates) - active_set)
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
