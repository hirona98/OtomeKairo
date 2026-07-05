from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse, urlunparse
from urllib.request import Request, urlopen

from otomekairo.service.common import ServiceError


WAKE_REFERENCE_IMAGE_SOURCE_ID = "vision_source:wake_reference"
WAKE_REFERENCE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
WAKE_REFERENCE_TEXT_MAX_BYTES = 64 * 1024
WAKE_REFERENCE_URI_MAX_CHARS = 2048
WAKE_REFERENCE_TEXT_FIELD_MAX_CHARS = 512
WAKE_REFERENCE_URL_TIMEOUT_SECONDS = 5.0
WAKE_REFERENCE_IMAGE_MEDIA_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
WAKE_REFERENCE_TEXT_MEDIA_TYPES = {
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/markdown",
    "application/rss+xml",
    "application/xml",
    "application/xhtml+xml",
    "application/x-yaml",
    "text/markdown",
}


@dataclass(frozen=True, slots=True)
class WakeReferenceResolution:
    summary: dict[str, Any]
    content_kind: str
    image_data_uri: str | None
    text: str | None


class ServiceInputWakeReferenceMixin:
    def _prepare_wake_reference_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        client_context: dict[str, Any],
        reference_payload: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        if reference_payload is None:
            return client_context, None, None
        resolution = self._resolve_wake_reference(
            reference_payload=reference_payload,
            resolved_at=started_at,
        )
        next_client_context = {
            **client_context,
            "wake_reference": resolution.summary,
        }
        reference_context = {
            "summary": resolution.summary,
        }
        if resolution.text is not None:
            # raw text は trace 用 client_context へ入れず、この cycle の LLM 文脈だけへ渡す。
            reference_context["text"] = resolution.text
            next_client_context["initiative_entry_check"] = self._wake_reference_initiative_entry(
                reason_summary=resolution.summary.get("reason_summary")
            )
            return next_client_context, None, reference_context

        if resolution.image_data_uri is None:
            return next_client_context, None, reference_context

        # 参照画像は capability result ではなく wake_reference source として視覚観測経路へ乗せる。
        observation_summary = {
            "source": "wake_reference",
            "image_input_kind": "wake_reference",
            "image_count": 1,
            "image_interpreted": False,
            "error": None,
            "vision_source_id": WAKE_REFERENCE_IMAGE_SOURCE_ID,
            "source_kind": "virtual",
            "source_label": resolution.summary.get("label") or "wake reference",
            "reference_summary": resolution.summary,
        }
        next_client_context, observation_summary = self._interpret_visual_observation(
            state=state,
            started_at=started_at,
            trigger_kind="wake",
            client_context=next_client_context,
            observation_summary=observation_summary,
            input_text=input_text,
            images=[resolution.image_data_uri],
        )
        updated_summary = dict(resolution.summary)
        visual_summary_text = observation_summary.get("visual_summary_text")
        if isinstance(visual_summary_text, str) and visual_summary_text.strip():
            updated_summary["visual_summary_text"] = visual_summary_text.strip()
        next_client_context["wake_reference"] = updated_summary
        reference_context["summary"] = updated_summary
        next_client_context["visual_observation_signals"] = [
            self._wake_reference_visual_signal(
                observation_summary=observation_summary,
                reason_summary=updated_summary.get("reason_summary"),
            )
        ]
        return next_client_context, observation_summary, reference_context

    def _resolve_wake_reference(
        self,
        *,
        reference_payload: Any,
        resolved_at: str,
    ) -> WakeReferenceResolution:
        if not isinstance(reference_payload, dict):
            raise ServiceError(400, "invalid_wake_reference", "wake reference must be an object.")
        uri = reference_payload.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            raise ServiceError(400, "invalid_wake_reference", "wake reference.uri must be a non-empty string.")
        normalized_uri = uri.strip()
        if len(normalized_uri) > WAKE_REFERENCE_URI_MAX_CHARS:
            raise ServiceError(400, "invalid_wake_reference", "wake reference.uri is too long.")

        content_hint = reference_payload.get("content_hint", "auto")
        if not isinstance(content_hint, str) or content_hint.strip() not in {"auto", "image", "text"}:
            raise ServiceError(
                400,
                "invalid_wake_reference",
                "wake reference.content_hint must be auto, image, or text.",
            )
        normalized_hint = content_hint.strip()
        label = self._wake_reference_optional_text(reference_payload.get("label"))
        reason_summary = self._wake_reference_optional_text(reference_payload.get("reason_summary"))

        # URI は wake サイクル開始時に 1 回だけ解決し、以降の判断では同じ bytes を使う。
        raw_bytes, media_type, sanitized_uri = self._read_wake_reference_bytes(normalized_uri)
        detected_media_type = self._detect_wake_reference_media_type(
            raw_bytes=raw_bytes,
            media_type=media_type,
        )
        content_kind = self._wake_reference_content_kind(
            raw_bytes=raw_bytes,
            media_type=detected_media_type,
            content_hint=normalized_hint,
        )

        summary = {
            "uri": sanitized_uri,
            "sanitized_uri": sanitized_uri,
            "label": label or sanitized_uri,
            "reason_summary": reason_summary,
            "content_hint": normalized_hint,
            "content_kind": content_kind,
            "media_type": detected_media_type,
            "byte_count": len(raw_bytes),
            "resolved_at": resolved_at,
        }

        if content_kind == "image":
            data_uri = "data:" + detected_media_type + ";base64," + base64.b64encode(raw_bytes).decode("ascii")
            return WakeReferenceResolution(
                summary=summary,
                content_kind=content_kind,
                image_data_uri=data_uri,
                text=None,
            )

        if len(raw_bytes) > WAKE_REFERENCE_TEXT_MAX_BYTES:
            raise ServiceError(
                413,
                "wake_reference_too_large",
                "wake reference text is too large.",
            )
        text = self._decode_wake_reference_text(raw_bytes)
        return WakeReferenceResolution(
            summary=summary,
            content_kind=content_kind,
            image_data_uri=None,
            text=text,
        )

    def _read_wake_reference_bytes(self, uri: str) -> tuple[bytes, str | None, str]:
        parsed = urlparse(uri)
        if parsed.scheme in {"http", "https"}:
            return self._read_wake_reference_url(uri, parsed)
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise ServiceError(400, "invalid_wake_reference", "wake reference file URI host is unsupported.")
            return self._read_wake_reference_file(Path(unquote(parsed.path)))
        if parsed.scheme:
            raise ServiceError(400, "invalid_wake_reference", "wake reference URI scheme is unsupported.")
        return self._read_wake_reference_file(Path(uri).expanduser())

    def _read_wake_reference_url(self, uri: str, parsed: Any) -> tuple[bytes, str | None, str]:
        request = Request(uri, headers={"User-Agent": "OtomeKairo/0.1"})
        try:
            with urlopen(request, timeout=WAKE_REFERENCE_URL_TIMEOUT_SECONDS) as response:  # noqa: S310
                content_length = response.headers.get("Content-Length")
                if isinstance(content_length, str) and content_length.strip().isdigit():
                    if int(content_length.strip()) > WAKE_REFERENCE_IMAGE_MAX_BYTES:
                        raise ServiceError(
                            413,
                            "wake_reference_too_large",
                            "wake reference is too large.",
                        )
                raw_bytes = response.read(WAKE_REFERENCE_IMAGE_MAX_BYTES + 1)
                media_type = response.headers.get_content_type()
        except ServiceError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ServiceError(
                502,
                "wake_reference_unavailable",
                "wake reference URL could not be read.",
            ) from exc
        if len(raw_bytes) > WAKE_REFERENCE_IMAGE_MAX_BYTES:
            raise ServiceError(413, "wake_reference_too_large", "wake reference is too large.")
        return raw_bytes, media_type, self._sanitize_wake_reference_url(parsed)

    def _read_wake_reference_file(self, path: Path) -> tuple[bytes, str | None, str]:
        try:
            if not path.is_file():
                raise ServiceError(
                    502,
                    "wake_reference_unavailable",
                    "wake reference file could not be read.",
                )
            if path.stat().st_size > WAKE_REFERENCE_IMAGE_MAX_BYTES:
                raise ServiceError(413, "wake_reference_too_large", "wake reference is too large.")
            raw_bytes = path.read_bytes()
        except ServiceError:
            raise
        except OSError as exc:
            raise ServiceError(
                502,
                "wake_reference_unavailable",
                "wake reference file could not be read.",
            ) from exc
        media_type, _ = mimetypes.guess_type(str(path))
        return raw_bytes, media_type, str(path)

    def _sanitize_wake_reference_url(self, parsed: Any) -> str:
        # 認証情報や query は trace に残さず、参照先の大枠だけを保存する。
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"{host}{port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))

    def _detect_wake_reference_media_type(self, *, raw_bytes: bytes, media_type: str | None) -> str:
        normalized_media_type = media_type.split(";", 1)[0].strip().lower() if isinstance(media_type, str) else ""
        magic_media_type = self._wake_reference_magic_image_media_type(raw_bytes)
        if magic_media_type is not None:
            return magic_media_type
        if normalized_media_type:
            return normalized_media_type
        return "application/octet-stream"

    def _wake_reference_magic_image_media_type(self, raw_bytes: bytes) -> str | None:
        if raw_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
            return "image/webp"
        return None

    def _wake_reference_content_kind(
        self,
        *,
        raw_bytes: bytes,
        media_type: str,
        content_hint: str,
    ) -> str:
        is_image = media_type in WAKE_REFERENCE_IMAGE_MEDIA_TYPES
        if content_hint == "image":
            if not is_image:
                raise ServiceError(
                    415,
                    "unsupported_wake_reference_content",
                    "wake reference is not an image.",
                )
            return "image"
        if content_hint == "text":
            self._decode_wake_reference_text(raw_bytes)
            return "text"
        if is_image:
            return "image"
        if media_type.startswith("text/") or media_type in WAKE_REFERENCE_TEXT_MEDIA_TYPES:
            self._decode_wake_reference_text(raw_bytes)
            return "text"
        try:
            self._decode_wake_reference_text(raw_bytes)
        except ServiceError as exc:
            if exc.error_code != "unsupported_wake_reference_content":
                raise
            raise ServiceError(
                415,
                "unsupported_wake_reference_content",
                "wake reference content is unsupported.",
            ) from exc
        return "text"

    def _decode_wake_reference_text(self, raw_bytes: bytes) -> str:
        if b"\x00" in raw_bytes:
            raise ServiceError(
                415,
                "unsupported_wake_reference_content",
                "wake reference text contains binary data.",
            )
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ServiceError(
                415,
                "unsupported_wake_reference_content",
                "wake reference text must be UTF-8.",
            ) from exc
        if not text.strip():
            raise ServiceError(
                415,
                "unsupported_wake_reference_content",
                "wake reference text is empty.",
            )
        return text

    def _wake_reference_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ServiceError(400, "invalid_wake_reference", "wake reference optional text must be a string.")
        stripped = value.strip()
        if len(stripped) > WAKE_REFERENCE_TEXT_FIELD_MAX_CHARS:
            raise ServiceError(400, "invalid_wake_reference", "wake reference optional text is too long.")
        return stripped or None

    def _wake_reference_initiative_entry(self, *, reason_summary: Any) -> dict[str, str]:
        reason = reason_summary if isinstance(reason_summary, str) and reason_summary.strip() else None
        return {
            "entry_kind": "enter",
            "entry_basis": "strong_interest",
            "reason_summary": reason or "参照付き wake が即時判断対象を明示した。",
        }

    def _wake_reference_visual_signal(
        self,
        *,
        observation_summary: dict[str, Any],
        reason_summary: Any,
    ) -> dict[str, Any]:
        change_state = observation_summary.get("change_state")
        if change_state not in {"first_seen", "changed"}:
            change_state = "changed"
        reason = reason_summary if isinstance(reason_summary, str) and reason_summary.strip() else None
        return {
            "observation_id": "wake_reference",
            "change_state": change_state,
            "change_basis": observation_summary.get("change_basis") or "semantic_change",
            "reason_summary": reason or "参照付き wake の画像を取得した。",
            "same_as_recent_speech": False,
            "summary_text": observation_summary.get("visual_summary_text"),
            "vision_source_id": observation_summary.get("vision_source_id"),
            "source_kind": observation_summary.get("source_kind"),
            "source_label": observation_summary.get("source_label"),
            "source_owner": "user_environment",
        }
