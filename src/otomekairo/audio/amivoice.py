from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


AMIVOICE_ENDPOINT = "https://acp-api.amivoice.com/v1/nolog/recognize"
AMIVOICE_TIMEOUT_SECONDS = 30


class AmiVoiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        provider_code: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.provider_code = provider_code


@dataclass(frozen=True)
class AmiVoiceResult:
    text: str
    confidence: float | None
    provider_code: str


class AmiVoiceClient:
    def __init__(self, endpoint: str = AMIVOICE_ENDPOINT) -> None:
        self._endpoint = endpoint

    def recognize(
        self,
        pcm16le: bytes,
        *,
        api_key: str,
        profile_id: str,
    ) -> AmiVoiceResult:
        # 音声と秘密値はログへ出さず、1発話を1回だけ送る。
        if not pcm16le:
            raise AmiVoiceError("empty_audio", "AmiVoice audio is empty.")
        if not api_key:
            raise AmiVoiceError("missing_api_key", "AmiVoice api_key is empty.")

        boundary = f"otomekairo-{secrets.token_hex(16)}"
        request = urllib.request.Request(
            self._endpoint,
            data=self._build_multipart_body(
                boundary=boundary,
                pcm16le=pcm16le,
                api_key=api_key,
                profile_id=profile_id,
            ),
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "OtomeKairo/0.3",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=AMIVOICE_TIMEOUT_SECONDS,
            ) as response:
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            raise AmiVoiceError(
                "http_error",
                f"AmiVoice returned HTTP {exc.code}.",
                http_status=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AmiVoiceError(
                "network_error",
                "AmiVoice request failed.",
            ) from exc

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AmiVoiceError(
                "invalid_response",
                "AmiVoice response is not valid JSON.",
            ) from exc
        return self._parse_response(payload)

    def _build_multipart_body(
        self,
        *,
        boundary: str,
        pcm16le: bytes,
        api_key: str,
        profile_id: str,
    ) -> bytes:
        parameters = "grammarFileNames=-a-general"
        if profile_id:
            # 設定にはプロフィール名だけを保持し、AmiVoiceの参照構文は送信境界で組み立てる。
            parameters += f" profileId=:{profile_id}"
        parts = [
            self._text_part(boundary, "u", api_key),
            self._text_part(boundary, "d", parameters),
            self._text_part(boundary, "c", "LSB16K"),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="a"; filename="audio.pcm"\r\n'
                "Content-Type: application/octet-stream\r\n"
                "\r\n"
            ).encode("utf-8")
            + pcm16le
            + b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
        return b"".join(parts)

    def _text_part(self, boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            "\r\n"
            f"{value}\r\n"
        ).encode("utf-8")

    def _parse_response(self, payload: Any) -> AmiVoiceResult:
        if not isinstance(payload, dict):
            raise AmiVoiceError(
                "invalid_response",
                "AmiVoice response must be an object.",
            )
        provider_code = payload.get("code", "")
        provider_message = payload.get("message", "")
        if not isinstance(provider_code, str) or not isinstance(provider_message, str):
            raise AmiVoiceError(
                "invalid_response",
                "AmiVoice code and message must be strings.",
            )
        if provider_code or provider_message:
            raise AmiVoiceError(
                "provider_error",
                "AmiVoice rejected the utterance.",
                provider_code=provider_code,
            )
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AmiVoiceError(
                "empty_transcript",
                "AmiVoice transcript is empty.",
            )
        confidence_value = payload.get("confidence")
        confidence = (
            float(confidence_value)
            if type(confidence_value) in {int, float}
            else None
        )
        return AmiVoiceResult(
            text=text,
            confidence=confidence,
            provider_code=provider_code,
        )
