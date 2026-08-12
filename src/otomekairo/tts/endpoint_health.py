from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from otomekairo.service.common import debug_log


# VOICEVOX 系 engine の優先/サブ endpoint を周期プローブし、健全な1つを選ぶ。
VOICEVOX_HEALTH_INTERVAL_SECONDS = 10.0
VOICEVOX_HEALTH_PROBE_TIMEOUT_SECONDS = 2.0
VOICEVOX_HEALTH_PROBE_PATH = "/version"


def normalize_endpoint_url(url: str) -> str:
    return url.strip().rstrip("/")


class VoicevoxEndpointHealth:
    """優先/サブ VOICEVOX endpoint の健全性を監視し、合成先を決める。"""

    def __init__(
        self,
        *,
        interval_seconds: float = VOICEVOX_HEALTH_INTERVAL_SECONDS,
        probe_timeout_seconds: float = VOICEVOX_HEALTH_PROBE_TIMEOUT_SECONDS,
        probe_fn: Callable[[str], bool] | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        # テスト差し替え用。未指定時は GET /version。
        self._probe_fn = probe_fn
        self._lock = threading.RLock()
        # 正規化 URL -> 健全か。未プローブは True（優先を仮健全として初回発話を止めない）。
        self._healthy: dict[str, bool] = {}
        # 合成失敗 mark 後、この時刻までは probe 成功でも健全へ戻さない。
        self._recover_after_monotonic: dict[str, float] = {}
        self._watched_primary: str | None = None
        self._watched_secondary: str | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="otomekairo-voicevox-health",
            daemon=True,
        )
        self._worker.start()

    def watch(self, primary: str, secondary: str) -> None:
        """合成設定の URL を監視対象にする。サブ空なら優先のみ。"""
        primary_url = normalize_endpoint_url(primary)
        secondary_raw = secondary.strip() if isinstance(secondary, str) else ""
        secondary_url = normalize_endpoint_url(secondary_raw) if secondary_raw else None
        with self._lock:
            changed = (
                primary_url != self._watched_primary
                or secondary_url != self._watched_secondary
            )
            self._watched_primary = primary_url
            self._watched_secondary = secondary_url
            if primary_url not in self._healthy:
                self._healthy[primary_url] = True
            if secondary_url is not None and secondary_url not in self._healthy:
                self._healthy[secondary_url] = True
        if changed:
            self._wake_event.set()

    def select_endpoint(self, primary: str, secondary: str) -> str:
        """両方健全なら優先。優先 down かつサブ up ならサブ。それ以外は優先。"""
        self.watch(primary, secondary)
        primary_url = normalize_endpoint_url(primary)
        secondary_raw = secondary.strip() if isinstance(secondary, str) else ""
        if not secondary_raw:
            return primary_url
        secondary_url = normalize_endpoint_url(secondary_raw)
        with self._lock:
            primary_ok = self._healthy.get(primary_url, True)
            secondary_ok = self._healthy.get(secondary_url, True)
        if primary_ok:
            return primary_url
        if secondary_ok:
            return secondary_url
        return primary_url

    def mark_unhealthy(self, endpoint: str) -> None:
        """合成失敗など、確定した不通を即反映する。

        次の合成から接続先選択に効かせる。同一 delivery 内の付け替えはしない。
        健全復帰は少なくとも1監視間隔後の probe 成功を要する。
        """
        url = normalize_endpoint_url(endpoint)
        if not url:
            return
        with self._lock:
            previous = self._healthy.get(url)
            self._healthy[url] = False
            self._recover_after_monotonic[url] = (
                time.monotonic() + self._interval_seconds
            )
        if previous is not False:
            debug_log(
                "TTS",
                f"voicevox endpoint marked unhealthy endpoint={url}",
                level="WARNING",
            )

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            # 待機前に clear し、待機中の watch 変更だけを次周期で拾う。
            self._wake_event.clear()
            primary, secondary = self._watched_targets()
            if primary is not None:
                self._probe_all(primary, secondary)
            self._wake_event.wait(timeout=self._interval_seconds)

    def _watched_targets(self) -> tuple[str | None, str | None]:
        with self._lock:
            return self._watched_primary, self._watched_secondary

    def _probe_all(self, primary: str, secondary: str | None) -> None:
        targets = [primary]
        if secondary is not None:
            targets.append(secondary)
        now = time.monotonic()
        for url in targets:
            if self._stop_event.is_set():
                return
            ok = self._probe(url)
            with self._lock:
                recover_after = self._recover_after_monotonic.get(url)
                if ok and recover_after is not None and now < recover_after:
                    # 合成失敗直後の sticky unhealthy。
                    continue
                if ok and recover_after is not None and now >= recover_after:
                    self._recover_after_monotonic.pop(url, None)
                previous = self._healthy.get(url)
                self._healthy[url] = ok
            if previous is not None and previous != ok:
                state = "healthy" if ok else "unhealthy"
                debug_log(
                    "TTS",
                    f"voicevox endpoint health changed endpoint={url} state={state}",
                    level="INFO",
                )

    def _probe(self, endpoint: str) -> bool:
        if self._probe_fn is not None:
            try:
                return bool(self._probe_fn(endpoint))
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "TTS",
                    f"voicevox health probe failed endpoint={endpoint} error={type(exc).__name__}",
                    level="WARNING",
                )
                return False
        url = f"{endpoint}{VOICEVOX_HEALTH_PROBE_PATH}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(  # noqa: S310
                request,
                timeout=self._probe_timeout_seconds,
            ) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                return 200 <= int(status) < 300
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            debug_log(
                "TTS",
                f"voicevox health probe failed endpoint={endpoint} error={type(exc).__name__}",
                level="WARNING",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "TTS",
                f"voicevox health probe failed endpoint={endpoint} error={type(exc).__name__}",
                level="WARNING",
            )
            return False
