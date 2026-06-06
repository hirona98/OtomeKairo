from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    pass


class JsonApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        tls_verify: bool,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context()
        if not tls_verify:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                context=self.ssl_context,
                timeout=self.timeout_seconds,
            ) as response:
                status_code = int(response.getcode())
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            raw_body = exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise HttpError(f"POST {path} failed: {exc.reason}") from exc

        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HttpError(f"POST {path} returned invalid JSON.") from exc
        if not isinstance(envelope, dict):
            raise HttpError(f"POST {path} returned a non-object envelope.")
        if status_code >= 400 or envelope.get("ok") is not True:
            error = envelope.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                raise HttpError(f"POST {path} failed: HTTP {status_code} {code}: {message}")
            raise HttpError(f"POST {path} failed: HTTP {status_code}")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise HttpError(f"POST {path} returned a non-object data payload.")
        return data
