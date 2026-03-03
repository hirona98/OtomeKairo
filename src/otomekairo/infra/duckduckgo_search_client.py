"""Minimal web search adapter using DuckDuckGo Instant Answer."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from otomekairo.gateway.search_client import SearchClient, SearchRequest, SearchResponse


# Block: Adapter constants
DUCKDUCKGO_ENDPOINT = "https://api.duckduckgo.com/"


# Block: Search adapter
@dataclass(frozen=True, slots=True)
class DuckDuckGoSearchClient(SearchClient):
    timeout_ms: int = 5_000

    # Block: Search execution
    def search(self, request: SearchRequest) -> SearchResponse:
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        query_text = request.query.strip()
        if not query_text:
            raise RuntimeError("search query must be non-empty")
        started_at = _now_ms()
        payload = _fetch_payload(query_text=query_text, timeout_ms=self.timeout_ms)
        summary_text = _extract_summary_text(payload)
        finished_at = _now_ms()
        return SearchResponse(
            summary_text=summary_text,
            raw_result_ref={
                "provider": "duckduckgo_instant_answer",
                "query": query_text,
                "endpoint": DUCKDUCKGO_ENDPOINT,
            },
            adapter_trace_ref={
                "provider": "duckduckgo_instant_answer",
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "answer_type": payload.get("Type"),
                "answer_url": payload.get("AbstractURL"),
            },
        )


# Block: Remote payload fetch
def _fetch_payload(*, query_text: str, timeout_ms: int) -> dict[str, Any]:
    query_string = urlencode(
        {
            "q": query_text,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "1",
        }
    )
    request = Request(
        url=f"{DUCKDUCKGO_ENDPOINT}?{query_string}",
        headers={
            "User-Agent": "OtomeKairo/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_ms / 1000.0) as response:
        body = response.read()
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("duckduckgo response must be object")
    return payload


# Block: Summary extraction
def _extract_summary_text(payload: dict[str, Any]) -> str:
    summary_text = payload.get("AbstractText")
    if not isinstance(summary_text, str) or not summary_text.strip():
        raise RuntimeError("duckduckgo response did not include AbstractText")
    summary_url = payload.get("AbstractURL")
    if not isinstance(summary_url, str) or not summary_url.strip():
        raise RuntimeError("duckduckgo response did not include AbstractURL")
    return f"{summary_text.strip()} ({summary_url.strip()})"


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
