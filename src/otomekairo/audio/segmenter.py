from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from otomekairo.audio.models import AudioModelError, SileroVadSession


SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2
VAD_WINDOW_SAMPLES = 512
PREBUFFER_SAMPLES = SAMPLE_RATE // 2
END_SILENCE_SAMPLES = SAMPLE_RATE // 2
MINIMUM_VOICED_SAMPLES = SAMPLE_RATE // 2
MAXIMUM_UTTERANCE_SAMPLES = SAMPLE_RATE * 15


@dataclass(frozen=True)
class SegmentedUtterance:
    amivoice_pcm16le: bytes
    speaker_pcm16le: bytes
    voiced_samples: int
    forced_split: bool

    @property
    def voiced_duration_seconds(self) -> float:
        return self.voiced_samples / SAMPLE_RATE


@dataclass(frozen=True)
class SegmenterUpdate:
    utterances: list[SegmentedUtterance]
    vad_probability: float | None
    vad_speaking: bool
    dbfs: float


class AudioSegmenter:
    def __init__(
        self,
        vad: SileroVadSession,
        *,
        probability_threshold: float,
    ) -> None:
        self._vad = vad
        self._probability_threshold = probability_threshold
        self.reset()

    def reset(self) -> None:
        # PCM、VAD状態、発話構築状態を同じリース境界で破棄する。
        self._vad.reset()
        self._pending_samples: deque[int] = deque()
        self._prebuffer: deque[int] = deque(maxlen=PREBUFFER_SAMPLES)
        self._speaking = False
        self._amivoice_samples: list[int] = []
        self._speaker_samples: list[int] = []
        self._first_positive_offset = 0
        self._last_positive_end = 0
        self._silence_samples = 0

    def feed_frame(self, pcm16le: bytes) -> SegmenterUpdate:
        if len(pcm16le) != FRAME_BYTES:
            raise AudioModelError("Audio frame must contain exactly 640 bytes.")
        samples = self._pcm_to_ints(pcm16le)
        self._pending_samples.extend(samples)
        utterances: list[SegmentedUtterance] = []
        probability: float | None = None
        while len(self._pending_samples) >= VAD_WINDOW_SAMPLES:
            window = [
                self._pending_samples.popleft()
                for _ in range(VAD_WINDOW_SAMPLES)
            ]
            float_samples = self._vad_samples(window)
            probability = self._vad.infer(float_samples)
            utterance = self._consume_window(
                window,
                is_positive=probability >= self._probability_threshold,
            )
            if utterance is not None:
                utterances.append(utterance)
        return SegmenterUpdate(
            utterances=utterances,
            vad_probability=probability,
            vad_speaking=self._speaking,
            dbfs=self._calculate_dbfs(samples),
        )

    def _consume_window(
        self,
        window: list[int],
        *,
        is_positive: bool,
    ) -> SegmentedUtterance | None:
        if not self._speaking:
            if not is_positive:
                self._prebuffer.extend(window)
                return None
            self._speaking = True
            self._amivoice_samples = [*self._prebuffer, *window]
            self._speaker_samples = list(window)
            self._first_positive_offset = 0
            self._last_positive_end = len(window)
            self._silence_samples = 0
            self._prebuffer.clear()
            return None

        self._amivoice_samples.extend(window)
        self._speaker_samples.extend(window)
        if is_positive:
            self._last_positive_end = len(self._speaker_samples)
            self._silence_samples = 0
        else:
            self._silence_samples += len(window)

        if len(self._speaker_samples) >= MAXIMUM_UTTERANCE_SAMPLES:
            return self._finish_utterance(forced_split=True)
        if self._silence_samples >= END_SILENCE_SAMPLES:
            return self._finish_utterance(forced_split=False)
        return None

    def _finish_utterance(self, *, forced_split: bool) -> SegmentedUtterance | None:
        voiced_samples = self._last_positive_end - self._first_positive_offset
        speaker_samples = self._speaker_samples[
            self._first_positive_offset : self._last_positive_end
        ]
        if forced_split:
            amivoice_samples = self._amivoice_samples
            self._prebuffer.clear()
        else:
            trailing_start = max(0, len(self._amivoice_samples) - END_SILENCE_SAMPLES)
            amivoice_samples = self._amivoice_samples[:]
            self._prebuffer = deque(
                self._amivoice_samples[trailing_start:],
                maxlen=PREBUFFER_SAMPLES,
            )

        self._speaking = False
        self._amivoice_samples = []
        self._speaker_samples = []
        self._first_positive_offset = 0
        self._last_positive_end = 0
        self._silence_samples = 0
        if voiced_samples < MINIMUM_VOICED_SAMPLES:
            return None
        return SegmentedUtterance(
            amivoice_pcm16le=self._ints_to_pcm(amivoice_samples),
            speaker_pcm16le=self._ints_to_pcm(speaker_samples),
            voiced_samples=voiced_samples,
            forced_split=forced_split,
        )

    def _vad_samples(self, samples: list[int]) -> Any:
        # VAD runtimeと同じnumpy moduleを経由してfloat32へ変換する。
        return self._vad.float32_samples(samples)

    def _pcm_to_ints(self, pcm16le: bytes) -> list[int]:
        return [
            int.from_bytes(pcm16le[offset : offset + 2], "little", signed=True)
            for offset in range(0, len(pcm16le), 2)
        ]

    def _ints_to_pcm(self, samples: list[int]) -> bytes:
        return b"".join(
            sample.to_bytes(2, "little", signed=True)
            for sample in samples
        )

    def _calculate_dbfs(self, samples: list[int]) -> float:
        if not samples:
            return -math.inf
        mean_square = sum(sample * sample for sample in samples) / len(samples)
        if mean_square <= 0.0:
            return -math.inf
        return 20.0 * math.log10(math.sqrt(mean_square) / 32768.0)
