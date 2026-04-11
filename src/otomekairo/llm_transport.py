from __future__ import annotations

import json
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from otomekairo.llm_contracts import LLMError
from otomekairo.llm_parsing import extract_embedding_vectors, extract_http_error_detail, extract_response_text


# 定数
OPENROUTER_DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_TIMEOUT_SECONDS = 600


# text completion を model 差分込みで実行する。
def complete_text(
    *,
    role_definition: dict,
    messages: list[dict[str, str]],
) -> str:
    completion = _load_litellm_completion()
    request_kwargs: dict[str, Any] = {
        "model": _resolve_litellm_model(role_definition),
        "messages": messages,
    }
    api_base = _resolve_api_base(role_definition)
    if isinstance(api_base, str) and api_base.strip():
        request_kwargs["api_base"] = api_base.strip()
    api_key = _resolve_api_key(role_definition)
    if api_key is not None:
        request_kwargs["api_key"] = api_key
    reasoning_effort = role_definition.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        request_kwargs["reasoning_effort"] = reasoning_effort.strip()
    max_output_tokens = _resolve_max_output_tokens(role_definition)
    if max_output_tokens is not None:
        request_kwargs["max_tokens"] = max_output_tokens
    web_search_options = _resolve_web_search_options(role_definition)
    if web_search_options is not None:
        request_kwargs["web_search_options"] = web_search_options

    try:
        response = completion(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"LiteLLM call failed: {exc}") from exc
    return extract_response_text(response)


# embedding を model 差分込みで実行する。
def generate_embeddings(
    *,
    role_definition: dict,
    texts: list[str],
    expected_dimension: int,
) -> list[list[float]]:
    if _is_openrouter_embedding_role_definition(role_definition):
        response = _request_openrouter_embeddings(role_definition=role_definition, texts=texts)
        return extract_embedding_vectors(
            response,
            expected_count=len(texts),
            expected_dimension=expected_dimension,
            source_label="OpenRouter",
        )

    embedding = _load_litellm_embedding()
    request_kwargs: dict[str, Any] = {
        "model": _resolve_litellm_model(role_definition),
        "input": texts,
    }
    api_base = _resolve_api_base(role_definition)
    if isinstance(api_base, str) and api_base.strip():
        request_kwargs["api_base"] = api_base.strip()
    api_key = _resolve_api_key(role_definition)
    if api_key is not None:
        request_kwargs["api_key"] = api_key

    try:
        response = embedding(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"LiteLLM embedding call failed: {exc}") from exc
    return extract_embedding_vectors(
        response,
        expected_count=len(texts),
        expected_dimension=expected_dimension,
        source_label="LiteLLM",
    )


def _load_litellm_completion() -> Callable[..., Any]:
    try:
        from litellm import completion
    except ImportError as exc:
        raise LLMError("LiteLLM is not installed. Run ./scripts/setup_venv.sh to install dependencies.") from exc
    return completion


def _load_litellm_embedding() -> Callable[..., Any]:
    try:
        from litellm import embedding
    except ImportError as exc:
        raise LLMError("LiteLLM is not installed. Run ./scripts/setup_venv.sh to install dependencies.") from exc
    return embedding


def _is_openrouter_embedding_role_definition(role_definition: dict) -> bool:
    return _model_provider_name(role_definition) == "openrouter"


def _resolve_litellm_model(role_definition: dict) -> str:
    model = role_definition.get("model")
    if not isinstance(model, str) or not model.strip():
        raise LLMError("role_definition.model is missing.")
    return model.strip()


def _resolve_openrouter_embedding_model(role_definition: dict) -> str:
    model = _resolve_litellm_model(role_definition)
    if model.startswith("openrouter/"):
        return model.removeprefix("openrouter/")
    return model


def _resolve_openrouter_api_base(role_definition: dict) -> str:
    configured_api_base = _resolve_api_base(role_definition)
    if configured_api_base is not None:
        return configured_api_base
    return OPENROUTER_DEFAULT_API_BASE


def _request_openrouter_embeddings(
    *,
    role_definition: dict,
    texts: list[str],
) -> dict[str, Any]:
    api_key = _resolve_api_key(role_definition)
    if api_key is None:
        raise LLMError("OpenRouter embedding requires auth token.")

    api_base = _resolve_openrouter_api_base(role_definition)
    payload = {
        "model": _resolve_openrouter_embedding_model(role_definition),
        "input": texts,
        "encoding_format": "float",
    }
    request = urllib_request.Request(
        url=f"{api_base}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=OPENROUTER_DEFAULT_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        detail = extract_http_error_detail(error_body)
        raise LLMError(f"OpenRouter embedding call failed: {exc.code} {detail}") from exc
    except urllib_error.URLError as exc:
        raise LLMError(f"OpenRouter embedding call failed: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"OpenRouter embedding response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMError("OpenRouter embedding response did not return an object.")
    return payload


def _resolve_api_base(role_definition: dict) -> str | None:
    configured_api_base = role_definition.get("api_base")
    if isinstance(configured_api_base, str) and configured_api_base.strip():
        return configured_api_base.strip()
    if _model_provider_name(role_definition) == "openrouter":
        return OPENROUTER_DEFAULT_API_BASE
    return None


def _model_provider_name(role_definition: dict) -> str:
    model = role_definition.get("model")
    if not isinstance(model, str):
        return ""
    normalized_model = model.strip()
    if not normalized_model:
        return ""
    return normalized_model.split("/", 1)[0]


def _resolve_api_key(role_definition: dict) -> str | None:
    value = role_definition.get("api_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_max_output_tokens(role_definition: dict) -> int | None:
    value = role_definition.get("max_output_tokens")
    if isinstance(value, int) and value >= 1:
        return value
    return None


def _resolve_web_search_options(role_definition: dict) -> dict[str, Any] | None:
    value = role_definition.get("web_search_enabled")
    if value is True:
        return {}
    return None
