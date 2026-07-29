from __future__ import annotations

import json
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


TTS_REQUEST_TIMEOUT_SECONDS = 60
TTS_MAX_AUDIO_BYTES = 32 * 1024 * 1024
TTS_MEDIA_TYPE = "audio/wav"
_FACE_TAG = re.compile(r"^\[face:(?:Joy|Angry|Sorrow|Fun)\]")


class TtsProviderError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    data: bytes
    media_type: str = TTS_MEDIA_TYPE


def speech_text_for_tts(value: str) -> str:
    # 表情指定は閉じた出力protocolとして先頭の既知tagだけを除外する。
    return _FACE_TAG.sub("", value, count=1)


class TtsProvider:
    def synthesize(self, text: str, definition: dict[str, Any]) -> SynthesizedAudio:
        normalized_text = speech_text_for_tts(text)
        if not normalized_text.strip():
            raise TtsProviderError(
                "tts_response_invalid",
                "The speech text is empty after removing the expression protocol tag.",
            )

        engine = definition.get("engine")
        deadline = time.monotonic() + TTS_REQUEST_TIMEOUT_SECONDS
        try:
            if engine == "voicevox":
                audio = self._synthesize_voicevox(
                    normalized_text,
                    definition["voicevox_config"],
                    deadline,
                )
            elif engine == "style-bert-vits2":
                audio = self._synthesize_style_bert_vits2(
                    normalized_text,
                    definition["style_bert_vits2_config"],
                    deadline,
                )
            elif engine == "aivis-cloud":
                audio = self._synthesize_aivis_cloud(
                    normalized_text,
                    definition["aivis_cloud_config"],
                    deadline,
                )
            else:
                raise TtsProviderError(
                    "tts_request_failed",
                    f"Unsupported TTS engine: {engine}",
                )
        except TtsProviderError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TtsProviderError(
                "tts_request_failed",
                f"TTS provider request failed: {type(exc).__name__}",
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise TtsProviderError(
                "tts_response_invalid",
                f"TTS provider response is invalid: {type(exc).__name__}",
            ) from exc

        if not audio:
            raise TtsProviderError(
                "tts_response_invalid",
                "TTS provider returned an empty audio response.",
            )
        if len(audio) > TTS_MAX_AUDIO_BYTES:
            raise TtsProviderError(
                "tts_response_too_large",
                "TTS provider returned audio larger than 32 MiB.",
            )
        self._validate_wav(audio)
        return SynthesizedAudio(data=audio)

    def _synthesize_voicevox(
        self,
        text: str,
        config: dict[str, Any],
        deadline: float,
    ) -> bytes:
        endpoint = self._endpoint(config)
        speaker_id = config["speaker_id"]
        query_url = self._url(
            endpoint,
            "/audio_query",
            {"speaker": speaker_id, "text": text},
        )
        query_bytes = self._request(
            query_url,
            method="POST",
            deadline=deadline,
        )
        query = json.loads(query_bytes.decode("utf-8"))
        if not isinstance(query, dict):
            raise TtsProviderError(
                "tts_response_invalid",
                "VOICEVOX audio_query response must be an object.",
            )
        replacements = {
            "speedScale": config["speed_scale"],
            "pitchScale": config["pitch_scale"],
            "intonationScale": config["intonation_scale"],
            "volumeScale": config["volume_scale"],
            "prePhonemeLength": config["pre_phoneme_length"],
            "postPhonemeLength": config["post_phoneme_length"],
            "outputSamplingRate": config["output_sampling_rate"],
            "outputStereo": config["output_stereo"],
        }
        missing = sorted(set(replacements) - set(query))
        if missing:
            raise TtsProviderError(
                "tts_response_invalid",
                f"VOICEVOX audio_query response is missing fields: {', '.join(missing)}.",
            )
        query.update(replacements)
        synthesis_url = self._url(
            endpoint,
            "/synthesis",
            {"speaker": speaker_id},
        )
        return self._request(
            synthesis_url,
            method="POST",
            body=json.dumps(query, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            deadline=deadline,
        )

    def _synthesize_style_bert_vits2(
        self,
        text: str,
        config: dict[str, Any],
        deadline: float,
    ) -> bytes:
        params: dict[str, Any] = {
            "text": text,
            "model_id": config["model_id"],
            "sdp_ratio": config["sdp_ratio"],
            "noise": config["noise"],
            "noisew": config["noise_w"],
            "length": config["length"],
            "language": config["language"],
            "auto_split": str(config["auto_split"]).lower(),
            "split_interval": config["split_interval"],
            "style": config["style"],
            "style_weight": config["style_weight"],
        }
        for field_name in ("model_name", "speaker_name", "reference_audio_path"):
            value = config[field_name]
            if isinstance(value, str) and value:
                params[field_name] = value
        assist_text = config["assist_text"]
        if isinstance(assist_text, str) and assist_text:
            params["assist_text"] = assist_text
            params["assist_text_weight"] = config["assist_text_weight"]
        return self._request(
            self._url(self._endpoint(config), "/voice", params),
            method="GET",
            deadline=deadline,
        )

    def _synthesize_aivis_cloud(
        self,
        text: str,
        config: dict[str, Any],
        deadline: float,
    ) -> bytes:
        api_key = config["api_key"]
        if not isinstance(api_key, str) or not api_key:
            raise TtsProviderError(
                "tts_request_failed",
                "Aivis Cloud API key is not configured.",
            )
        endpoint = config["endpoint_url"] or "https://api.aivis-project.com/v1/tts/synthesize"
        payload: dict[str, Any] = {
            "text": text,
            "model_uuid": config["model_uuid"],
            "style_id": config["style_id"],
            "use_ssml": config["use_ssml"],
            "language": config["language"],
            "speaking_rate": config["speaking_rate"],
            "emotional_intensity": config["emotional_intensity"],
            "tempo_dynamics": config["tempo_dynamics"],
            "pitch": config["pitch"],
            "volume": config["volume"],
            "output_format": "wav",
            "output_bitrate": 0,
            "output_sampling_rate": config["output_sampling_rate"],
            "output_audio_channels": config["output_audio_channels"],
        }
        if config["speaker_uuid"]:
            payload["speaker_uuid"] = config["speaker_uuid"]
        if config["style_name"]:
            payload["style_name"] = config["style_name"]
        return self._request(
            endpoint,
            method="POST",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            authorization=f"Bearer {api_key}",
            deadline=deadline,
        )

    def _request(
        self,
        url: str,
        *,
        method: str,
        body: bytes | None = None,
        content_type: str | None = None,
        authorization: str | None = None,
        deadline: float,
    ) -> bytes:
        headers: dict[str, str] = {"Accept": TTS_MEDIA_TYPE}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if authorization is not None:
            headers["Authorization"] = authorization
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        remaining_timeout = deadline - time.monotonic()
        if remaining_timeout <= 0:
            raise TimeoutError("TTS request deadline expired.")
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=remaining_timeout,
        ) as response:
            content_length = response.headers.get("Content-Length")
            if isinstance(content_length, str):
                try:
                    if int(content_length) > TTS_MAX_AUDIO_BYTES:
                        raise TtsProviderError(
                            "tts_response_too_large",
                            "TTS provider declared audio larger than 32 MiB.",
                        )
                except ValueError:
                    pass
            data = response.read(TTS_MAX_AUDIO_BYTES + 1)
        if len(data) > TTS_MAX_AUDIO_BYTES:
            raise TtsProviderError(
                "tts_response_too_large",
                "TTS provider returned audio larger than 32 MiB.",
            )
        return data

    def _validate_wav(self, audio: bytes) -> None:
        # 配送先が共通して再生できるWAV形式をprovider境界で確定する。
        if (
            len(audio) < 12
            or audio[:4] != b"RIFF"
            or audio[8:12] != b"WAVE"
        ):
            raise TtsProviderError(
                "tts_response_invalid",
                "TTS provider response is not a RIFF/WAVE file.",
            )

        audio_format = 0
        channels = 0
        sample_rate = 0
        bits_per_sample = 0
        data_size: int | None = None
        offset = 12
        while offset + 8 <= len(audio):
            chunk_id = audio[offset : offset + 4]
            chunk_size = struct.unpack_from("<I", audio, offset + 4)[0]
            chunk_start = offset + 8
            chunk_end = chunk_start + chunk_size
            padded_end = chunk_end + (chunk_size % 2)
            if chunk_end > len(audio) or padded_end > len(audio):
                raise TtsProviderError(
                    "tts_response_invalid",
                    "TTS provider WAV contains an invalid chunk size.",
                )
            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    raise TtsProviderError(
                        "tts_response_invalid",
                        "TTS provider WAV fmt chunk is incomplete.",
                    )
                (
                    audio_format,
                    channels,
                    sample_rate,
                    _byte_rate,
                    _block_align,
                    bits_per_sample,
                ) = struct.unpack_from("<HHIIHH", audio, chunk_start)
            elif chunk_id == b"data" and data_size is None:
                data_size = chunk_size
            offset = padded_end

        supported_format = (
            (audio_format == 1 and bits_per_sample == 16)
            or (audio_format == 3 and bits_per_sample == 32)
        )
        bytes_per_sample = bits_per_sample // 8
        if (
            not supported_format
            or channels <= 0
            or sample_rate <= 0
            or data_size is None
            or data_size <= 0
            or data_size % (channels * bytes_per_sample) != 0
        ):
            raise TtsProviderError(
                "tts_response_invalid",
                "TTS provider WAV must contain PCM 16-bit or IEEE float 32-bit samples.",
            )

    def _endpoint(self, config: dict[str, Any]) -> str:
        endpoint = config["endpoint_url"]
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise TtsProviderError(
                "tts_request_failed",
                "TTS endpoint URL is not configured.",
            )
        return endpoint.rstrip("/")

    def _url(
        self,
        endpoint: str,
        path: str,
        query: dict[str, Any],
    ) -> str:
        return f"{endpoint}{path}?{urllib.parse.urlencode(query)}"
