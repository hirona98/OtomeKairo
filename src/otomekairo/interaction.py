from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.service.common import ServiceError


@dataclass(frozen=True, slots=True)
class ParticipantContext:
    # 外部システムが確定した人物参照と人間向けの呼び名を保持する。
    person_ref: str
    display_name: str

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "person_ref": self.person_ref,
            "display_name": self.display_name,
        }


@dataclass(frozen=True, slots=True)
class InteractionContext:
    # 人物同一性ではなく、現在の相互作用の論理的な場を表す。
    interaction_ref: str
    speaker_ref: str | None
    participants: tuple[ParticipantContext, ...]

    @property
    def participant_refs(self) -> tuple[str, ...]:
        return tuple(participant.person_ref for participant in self.participants)

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "interaction_ref": self.interaction_ref,
            "participants": [participant.to_prompt_payload() for participant in self.participants],
        }
        if self.speaker_ref is not None:
            payload["speaker_ref"] = self.speaker_ref
        return payload

def normalize_interaction_context(
    value: Any,
    *,
    required: bool,
    require_speaker: bool,
) -> InteractionContext | None:
    # OtomeKairo は外部から渡された人物同一性を信頼し、wire shape だけを検証する。
    if value is None and not required:
        return None
    if not isinstance(value, dict):
        raise ServiceError(
            400,
            "invalid_interaction_context",
            "interaction_context must be an object.",
        )

    unsupported_fields = sorted(set(value) - {"interaction_ref", "speaker_ref", "participants"})
    if unsupported_fields:
        raise ServiceError(
            400,
            "invalid_interaction_context",
            f"interaction_context.{unsupported_fields[0]} is not supported.",
        )

    interaction_ref = _required_text(
        value.get("interaction_ref"),
        error_code="invalid_interaction_ref",
        message="interaction_context.interaction_ref must be a non-empty string.",
    )
    raw_participants = value.get("participants")
    if not isinstance(raw_participants, list) or not raw_participants:
        raise ServiceError(
            400,
            "invalid_interaction_participants",
            "interaction_context.participants must be a non-empty array.",
        )
    if len(raw_participants) != 1:
        raise ServiceError(
            400,
            "unsupported_group_interaction",
            "The current implementation accepts exactly one human participant.",
        )

    participants = tuple(
        _normalize_participant(participant, index=index)
        for index, participant in enumerate(raw_participants)
    )
    participant_refs = [participant.person_ref for participant in participants]
    if len(set(participant_refs)) != len(participant_refs):
        raise ServiceError(
            400,
            "invalid_interaction_participants",
            "interaction_context.participants contains duplicate person_ref values.",
        )

    speaker_ref_value = value.get("speaker_ref")
    speaker_ref: str | None = None
    if speaker_ref_value is not None:
        speaker_ref = _person_ref(
            speaker_ref_value,
            error_code="invalid_speaker_ref",
            message="interaction_context.speaker_ref must use person:<key> form.",
        )
    if require_speaker and speaker_ref is None:
        raise ServiceError(
            400,
            "invalid_speaker_ref",
            "interaction_context.speaker_ref is required.",
        )
    if speaker_ref is not None and speaker_ref not in participant_refs:
        raise ServiceError(
            400,
            "interaction_speaker_not_participant",
            "interaction_context.speaker_ref must be present in participants.",
        )

    return InteractionContext(
        interaction_ref=interaction_ref,
        speaker_ref=speaker_ref,
        participants=participants,
    )


def _normalize_participant(value: Any, *, index: int) -> ParticipantContext:
    if not isinstance(value, dict):
        raise ServiceError(
            400,
            "invalid_interaction_participants",
            f"interaction_context.participants[{index}] must be an object.",
        )
    unsupported_fields = sorted(set(value) - {"person_ref", "display_name"})
    if unsupported_fields:
        raise ServiceError(
            400,
            "invalid_interaction_participants",
            f"interaction_context.participants[{index}].{unsupported_fields[0]} is not supported.",
        )
    person_ref = _person_ref(
        value.get("person_ref"),
        error_code="invalid_person_ref",
        message=f"interaction_context.participants[{index}].person_ref must use person:<key> form.",
    )
    display_name = _required_text(
        value.get("display_name"),
        error_code="invalid_person_display_name",
        message=f"interaction_context.participants[{index}].display_name must be a non-empty string.",
    )
    return ParticipantContext(person_ref=person_ref, display_name=display_name)


def _person_ref(value: Any, *, error_code: str, message: str) -> str:
    normalized = _required_text(value, error_code=error_code, message=message)
    if not normalized.startswith("person:") or normalized == "person:":
        raise ServiceError(400, error_code, message)
    return normalized


def _required_text(value: Any, *, error_code: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(400, error_code, message)
    return value.strip()
