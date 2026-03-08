"""Cognition client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: 認知計画リクエスト
@dataclass(frozen=True, slots=True)
class CognitionPlanRequest:
    cycle_id: str
    input_kind: str
    cognition_input: dict[str, Any]
    completion_settings: dict[str, Any]


# Block: 認知計画レスポンス
@dataclass(frozen=True, slots=True)
class CognitionPlanResponse:
    cognition_plan: dict[str, Any]


# Block: 応答文レンダリングリクエスト
@dataclass(frozen=True, slots=True)
class ReplyRenderRequest:
    cycle_id: str
    input_kind: str
    cognition_input: dict[str, Any]
    cognition_plan: dict[str, Any]
    completion_settings: dict[str, Any]


# Block: 応答文レンダリングレスポンス
@dataclass(frozen=True, slots=True)
class ReplyRenderResponse:
    speech_draft: dict[str, Any]


# Block: 認知クライアント契約
class CognitionClient(Protocol):
    def generate_plan(self, request: CognitionPlanRequest) -> CognitionPlanResponse:
        ...

    def render_reply(self, request: ReplyRenderRequest) -> ReplyRenderResponse:
        ...
