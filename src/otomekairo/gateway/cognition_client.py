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


# Block: 想起選別リクエスト
@dataclass(frozen=True, slots=True)
class RetrievalSelectionRequest:
    cycle_id: str
    current_observation: dict[str, Any]
    retrieval_plan: dict[str, Any]
    candidate_pack: dict[str, Any]
    completion_settings: dict[str, Any]


# Block: 想起選別レスポンス
@dataclass(frozen=True, slots=True)
class RetrievalSelectionResponse:
    retrieval_selection: dict[str, Any]


# Block: 応答文レンダリングリクエスト
@dataclass(frozen=True, slots=True)
class ReplyRenderRequest:
    cycle_id: str
    input_kind: str
    reply_render_input: dict[str, Any]
    cognition_plan: dict[str, Any]
    completion_settings: dict[str, Any]


# Block: 応答文レンダリングレスポンス
@dataclass(frozen=True, slots=True)
class ReplyRenderResponse:
    speech_draft: dict[str, Any]


# Block: 認知クライアント契約
class CognitionClient(Protocol):
    def select_retrieval_candidates(
        self,
        request: RetrievalSelectionRequest,
    ) -> RetrievalSelectionResponse:
        ...

    def generate_plan(self, request: CognitionPlanRequest) -> CognitionPlanResponse:
        ...

    def render_reply(self, request: ReplyRenderRequest) -> ReplyRenderResponse:
        ...
