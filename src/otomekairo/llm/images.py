from __future__ import annotations

import base64
import hashlib
import io

from otomekairo.llm.contracts import LLMError


VISUAL_OBSERVATION_MAX_EDGE = 1024
VISUAL_OBSERVATION_JPEG_QUALITY = 80


def image_content_hash(data_uri: str) -> str:
    raw_bytes, _media_type = _decode_image_data_uri(data_uri)
    return hashlib.sha256(raw_bytes).hexdigest()


def prepare_visual_observation_image(data_uri: str) -> str:
    raw_bytes, media_type = _decode_image_data_uri(data_uri)
    try:
        from PIL import Image
    except ImportError as exc:
        raise LLMError("画像の解釈準備には Pillow が必要です。") from exc
    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            image.load()
            width, height = image.size
            if width < 1 or height < 1:
                raise LLMError("視覚入力の画像サイズが不正です。")
            if width <= VISUAL_OBSERVATION_MAX_EDGE and height <= VISUAL_OBSERVATION_MAX_EDGE:
                return data_uri
            prepared = image.convert("RGB") if image.mode != "RGB" else image
            prepared.thumbnail(
                (VISUAL_OBSERVATION_MAX_EDGE, VISUAL_OBSERVATION_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            buffer = io.BytesIO()
            prepared.save(buffer, format="JPEG", quality=VISUAL_OBSERVATION_JPEG_QUALITY, optimize=True)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"視覚入力の画像を解釈用に変換できませんでした: {exc}") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _decode_image_data_uri(data_uri: str) -> tuple[bytes, str]:
    if not isinstance(data_uri, str) or not data_uri.startswith("data:image/"):
        raise LLMError("視覚入力は image data URI である必要があります。")
    header, separator, body = data_uri.partition(",")
    if separator != "," or not body.strip():
        raise LLMError("視覚入力の data URI が空です。")
    if ";base64" not in header:
        raise LLMError("視覚入力の data URI は base64 である必要があります。")
    media_type = header[5:].split(";", 1)[0].strip()
    if not media_type.startswith("image/"):
        raise LLMError("視覚入力の media type が不正です。")
    try:
        raw_bytes = base64.b64decode(body, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise LLMError("視覚入力の base64 を復号できませんでした。") from exc
    if not raw_bytes:
        raise LLMError("視覚入力の画像バイトが空です。")
    return raw_bytes, media_type
