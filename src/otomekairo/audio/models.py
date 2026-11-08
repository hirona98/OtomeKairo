from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any


MODEL_PACKAGE = "otomekairo.audio.model_assets"
SILERO_MODEL_ID = "silero-vad-v5-16k-opset15"
SPEAKER_EMBEDDING_DIMENSION = 192


class AudioModelError(RuntimeError):
    pass


_MODEL_LOAD_LOCK = threading.Lock()


def _build_cpu_session(model_bytes: bytes) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])


@lru_cache(maxsize=1)
def _shared_model_sessions(
    vad_file: str, vad_hash: str, speaker_file: str, speaker_hash: str,
) -> tuple[Any, Any, Any, Any]:
    # 重みはprocess内で一度だけ読み込み、推論状態は各runtimeに持たせる。
    sessions = []
    for file_name, expected_hash in ((vad_file, vad_hash), (speaker_file, speaker_hash)):
        try:
            data = resources.files(MODEL_PACKAGE).joinpath(file_name).read_bytes()
        except FileNotFoundError as exc:
            raise AudioModelError(f"Audio model file is unavailable: {file_name}.") from exc
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise AudioModelError(f"Audio model hash mismatch: {file_name}.")
        sessions.append(_build_cpu_session(data))
    return sessions[0], sessions[1], threading.Lock(), threading.Lock()


@dataclass(frozen=True)
class SpeakerIdentification:
    person_ref: str | None
    accepted: bool
    top1_person_ref: str | None
    top1_similarity: float | None
    top2_person_ref: str | None
    top2_similarity: float | None


class SileroVadSession:
    def __init__(self, session: Any, numpy_module: Any, *, inference_lock: Any = None) -> None:
        self._session = session
        self._np = numpy_module
        self._lock = threading.Lock()
        self._inference_lock = inference_lock if inference_lock is not None else threading.Lock()
        self.reset()

    def reset(self) -> None:
        # リースごとの recurrent state と直前64 sampleを同時に破棄する。
        with self._lock:
            self._state = self._np.zeros((2, 1, 128), dtype=self._np.float32)
            self._context = self._np.zeros((1, 64), dtype=self._np.float32)

    def infer(self, samples: Any) -> float:
        if samples.shape != (512,):
            raise AudioModelError("Silero VAD input must contain 512 samples.")
        with self._lock, self._inference_lock:
            # Silero v5のONNX入力は直前64 sample + 今回512 sample。
            model_input = self._np.concatenate(
                (self._context, samples.reshape(1, 512)),
                axis=1,
            ).astype(self._np.float32, copy=False)
            output, next_state = self._session.run(
                ["output", "stateN"],
                {
                    "input": model_input,
                    "state": self._state,
                    "sr": self._np.asarray(16000, dtype=self._np.int64),
                },
            )
            self._state = next_state.astype(self._np.float32, copy=False)
            self._context = model_input[:, -64:].copy()
        probability = float(output.reshape(-1)[0])
        if not 0.0 <= probability <= 1.0:
            raise AudioModelError("Silero VAD returned an invalid probability.")
        return probability

    def float32_samples(self, samples: list[int]) -> Any:
        # Segmenterの整数PCMをモデル入力表現へ変換する。
        return self._np.asarray(samples, dtype=self._np.float32) / 32768.0


class AudioModelRuntime:
    def __init__(self) -> None:
        # 重い依存はここで読み込み、失敗をaudio runtimeだけへ閉じる。
        try:
            import kaldi_native_fbank as knf
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise AudioModelError(
                f"Audio runtime dependency is unavailable: {exc.name}."
            ) from exc

        self._knf = knf
        self._np = np
        self._ort = ort
        manifest = self._read_manifest()
        try:
            vad_model = manifest["silero_vad"]
            speaker_model = manifest["speaker_embedding"]
            with _MODEL_LOAD_LOCK:
                self._silero_session, self._speaker_session, vad_lock, self._speaker_lock = _shared_model_sessions(
                    vad_model["file"], vad_model["sha256"],
                    speaker_model["file"], speaker_model["sha256"],
                )
        except (KeyError, TypeError) as exc:
            raise AudioModelError("Audio model manifest is invalid.") from exc
        self._validate_sessions()
        self.silero_model_id = manifest["silero_vad"]["model_id"]
        self.speaker_model_id = manifest["speaker_embedding"]["model_id"]
        self.vad = SileroVadSession(self._silero_session, self._np, inference_lock=vad_lock)

    def extract_speaker_embedding(self, pcm16le: bytes) -> list[float]:
        # PCMから公式Kaldi互換Fbankを生成し、発話CMNを適用する。
        if len(pcm16le) % 2 != 0 or not pcm16le:
            raise AudioModelError("Speaker PCM must be non-empty PCM16LE.")
        # ERes2NetV2は正規化float32とPovey窓のFbankを受け取る。
        samples = self._np.frombuffer(pcm16le, dtype="<i2").astype(self._np.float32) / 32768.0
        options = self._knf.FbankOptions()
        options.frame_opts.samp_freq = 16000.0
        options.frame_opts.frame_length_ms = 25.0
        options.frame_opts.frame_shift_ms = 10.0
        options.frame_opts.window_type = "povey"
        options.frame_opts.dither = 0.0
        options.frame_opts.snip_edges = True
        options.mel_opts.num_bins = 80
        options.use_energy = False
        fbank = self._knf.OnlineFbank(options)
        fbank.accept_waveform(16000.0, samples.tolist())
        fbank.input_finished()
        frame_count = fbank.num_frames_ready
        if frame_count <= 0:
            raise AudioModelError("Speaker audio produced no Fbank frames.")
        features = self._np.asarray(
            [fbank.get_frame(index) for index in range(frame_count)],
            dtype=self._np.float32,
        )
        if features.ndim != 2 or features.shape[1] != 80:
            raise AudioModelError("Speaker Fbank shape is invalid.")
        features -= features.mean(axis=0, keepdims=True)

        with self._speaker_lock:
            output = self._speaker_session.run(
                ["embedding"],
                {"x": features[self._np.newaxis, :, :]},
            )[0]
        embedding = output.reshape(-1).astype(self._np.float32, copy=False)
        if embedding.shape != (SPEAKER_EMBEDDING_DIMENSION,):
            raise AudioModelError("Speaker embedding dimension is invalid.")
        norm = float(self._np.linalg.norm(embedding))
        if not self._np.isfinite(norm) or norm <= 1.0e-6:
            raise AudioModelError("Speaker embedding norm is invalid.")
        return (embedding / norm).tolist()

    def identify_speaker(
        self,
        embedding: list[float],
        speakers: list[dict[str, Any]],
        *,
        threshold: float,
    ) -> SpeakerIdentification:
        # 登録embeddingは保存時に正規化済みなので内積をcosine similarityとする。
        probe = self._np.asarray(embedding, dtype=self._np.float32)
        if probe.shape != (SPEAKER_EMBEDDING_DIMENSION,):
            raise AudioModelError("Speaker probe embedding dimension is invalid.")
        scored: list[tuple[float, str]] = []
        for speaker in speakers:
            registered = self._np.asarray(
                speaker.get("embedding"),
                dtype=self._np.float32,
            )
            if (
                speaker.get("registration_status") != "registered"
                or speaker.get("model_id") != self.speaker_model_id
            ):
                continue
            if registered.shape != (SPEAKER_EMBEDDING_DIMENSION,) or not self._np.isfinite(registered).all():
                raise AudioModelError("Registered speaker embedding is invalid.")
            scored.append(
                (
                    float(self._np.dot(probe, registered)),
                    speaker["person_ref"],
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]))
        top1 = scored[0] if scored else None
        top2 = scored[1] if len(scored) >= 2 else None
        accepted = (
            top1 is not None
            and top1[0] >= threshold
            and (top2 is None or top1[0] - top2[0] >= 0.05)
        )
        return SpeakerIdentification(
            person_ref=top1[1] if accepted and top1 is not None else None,
            accepted=accepted,
            top1_person_ref=top1[1] if top1 is not None else None,
            top1_similarity=top1[0] if top1 is not None else None,
            top2_person_ref=top2[1] if top2 is not None else None,
            top2_similarity=top2[0] if top2 is not None else None,
        )

    def mean_embedding(self, embeddings: list[list[float]]) -> list[float]:
        # 登録サンプルを平均し、保存する代表embeddingを再度L2正規化する。
        if len(embeddings) != 3:
            raise AudioModelError("Speaker enrollment requires exactly 3 embeddings.")
        matrix = self._np.asarray(embeddings, dtype=self._np.float32)
        if matrix.shape != (3, SPEAKER_EMBEDDING_DIMENSION):
            raise AudioModelError("Speaker enrollment embedding shape is invalid.")
        mean = matrix.mean(axis=0)
        norm = float(self._np.linalg.norm(mean))
        if not self._np.isfinite(norm) or norm <= 1.0e-6:
            raise AudioModelError("Speaker enrollment mean norm is invalid.")
        return (mean / norm).tolist()

    def _read_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(
                resources.files(MODEL_PACKAGE)
                .joinpath("manifest.json")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise AudioModelError("Audio model manifest is unavailable.") from exc

    def _build_session(self, model_bytes: bytes) -> Any:
        return _build_cpu_session(model_bytes)

    def _validate_sessions(self) -> None:
        silero_inputs = {
            item.name: (item.type, item.shape)
            for item in self._silero_session.get_inputs()
        }
        silero_outputs = {
            item.name: (item.type, item.shape)
            for item in self._silero_session.get_outputs()
        }
        if set(silero_inputs) != {"input", "state", "sr"}:
            raise AudioModelError("Silero VAD input names are invalid.")
        if set(silero_outputs) != {"output", "stateN"}:
            raise AudioModelError("Silero VAD output names are invalid.")
        speaker_inputs = self._speaker_session.get_inputs()
        speaker_outputs = self._speaker_session.get_outputs()
        if (
            len(speaker_inputs) != 1
            or speaker_inputs[0].name != "x"
            or speaker_inputs[0].type != "tensor(float)"
            or speaker_inputs[0].shape[-1] != 80
            or len(speaker_outputs) != 1
            or speaker_outputs[0].name != "embedding"
            or speaker_outputs[0].type != "tensor(float)"
            or speaker_outputs[0].shape[-1] != SPEAKER_EMBEDDING_DIMENSION
        ):
            raise AudioModelError("Speaker model shape is invalid.")
        metadata = self._speaker_session.get_modelmeta().custom_metadata_map
        required = {
            "framework": "3d-speaker",
            "sample_rate": "16000",
            "normalize_samples": "1",
            "feature_normalize_type": "global-mean",
            "output_dim": str(SPEAKER_EMBEDDING_DIMENSION),
        }
        if any(metadata.get(key) != value for key, value in required.items()):
            raise AudioModelError("Speaker model preprocessing metadata is invalid.")
