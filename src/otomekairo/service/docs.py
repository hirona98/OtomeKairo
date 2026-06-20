from __future__ import annotations

from pathlib import Path
from typing import Any

from otomekairo.service.common import ServiceError


CONSOLE_DOC_SECTIONS = (
    {
        "section_id": "conversation",
        "title": "会話API",
        "relative_path": Path("docs/console/api_conversation.txt"),
    },
    {
        "section_id": "wake",
        "title": "API起床",
        "relative_path": Path("docs/console/api_wake.txt"),
    },
)


class ServiceDocsMixin:
    def get_docs(self, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        root_dir = self._repository_root()
        sections = []
        for section in CONSOLE_DOC_SECTIONS:
            doc_path = root_dir / section["relative_path"]
            try:
                body_text = doc_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ServiceError(
                    500,
                    "docs_unavailable",
                    "The selected documentation file is not available.",
                ) from exc
            sections.append(
                {
                    "section_id": section["section_id"],
                    "title": section["title"],
                    "body_text": body_text,
                }
            )

        return {
            "document_set_id": "console_docs",
            "title": "OtomeKairo Docs",
            "format": "plain_text",
            "sections": sections,
        }

    def _repository_root(self) -> Path:
        for parent in Path(__file__).resolve().parents:
            if (parent / "docs").is_dir() and (parent / "pyproject.toml").is_file():
                return parent
        raise ServiceError(
            500,
            "docs_unavailable",
            "The documentation root is not available.",
        )
