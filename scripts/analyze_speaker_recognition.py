"""公開音声で話者照合の長さ・登録方法・VAD境界を比較するオフライン調査。

音声はメモリ内だけで処理し、JSONには数値・公開音声の出典だけを出力する。
通常サーバの設定・登録・依存関係を変更しない。通常テストには含めない。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import sys
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np

from otomekairo.audio.models import AudioModelRuntime


BASE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
)
FILES = {
    "fangjun-sr-1": "33c24061180224d2350143ee19e3af031446995c676bd25996325d34bb20a4d5",
    "fangjun-sr-2": "c9209ff0cc83cb3c30b3efb376cec6df93f33ceb0f3c1a8eb28fced7a8d08409",
    "fangjun-sr-3": "5a5f2fc98cb95cde4e457c31349dcbd5cf7ec2567528aac11487aa3884795bd8",
    "fangjun-test-sr-1": "9175e523081bf6a630ce72a55b05f92148eaafaf58cbbbe743686cd81c50848e",
    "fangjun-test-sr-2": "c55912144a28817a67d5eac806e5952ffcd29b82db3925f46d33df4c5f038907",
    "leijun-sr-1": "160a3d9bf5dd5038da8191b4430e1f3f751461613ae9489401b6b35a61b488ad",
    "leijun-sr-2": "37a759c036f2520d143708dfe46d45901b38a0a6e621b52d6f1aef2c000d7fe0",
    "leijun-test-sr-1": "36cda04ee4d10e38095de73b77c99e8e7c54347232967a9cde4cf54f7d496bab",
    "leijun-test-sr-2": "84b85e7413b5348303a95bd78813e4d669b4dc70fa5a99990b9860ca6fda91e8",
    "leijun-test-sr-3": "ef026f55e457780fc066b0f5d22d944e102690b2d77c7c0536e8d84f55a034a8",
    "liudehua-sr-1": "f39ebb7357d537009a9b7e59a6c9c0199f2204fcffb3c2d9d829a6961813eca1",
    "liudehua-sr-2": "0f0afddb99d381a23c899f464864f692dc21190cebbd4edf4ff695e10a107c9c",
    "liudehua-test-sr-1": "a855706ad8e8253e341e3f65892c66d7dfb4bdc83675bcf38b863f2f8d167553",
    "liudehua-test-sr-2": "2c9ba09e7fe9deca8bea0b67776c39665cad3a1036e7a8d3407d1e45e2ede23a",
}
SPEAKERS = ("fangjun", "leijun", "liudehua")
ENROLLMENT_MODES = ("centroid_2s", "centroid_full", "concatenated_full", "max_2s_templates")
SAMPLE_RATE = 16000
WINDOW = 512


def load_public_wave(name: str) -> tuple[str, np.ndarray]:
    with urllib.request.urlopen(BASE_URL + name + ".wav", timeout=30) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != FILES[name]:
        raise RuntimeError(f"Public fixture hash mismatch: {name}")
    with wave.open(io.BytesIO(payload)) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError(f"Unexpected public fixture format: {name}")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    return name, samples


def normalize(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def features(samples: np.ndarray) -> np.ndarray:
    options = knf.FbankOptions()
    options.frame_opts.samp_freq = 16000
    options.frame_opts.frame_length_ms = 25
    options.frame_opts.frame_shift_ms = 10
    options.frame_opts.window_type = "hamming"
    options.frame_opts.dither = 0
    options.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16000, samples.astype(np.float32).tolist())
    fbank.input_finished()
    matrix = np.asarray([fbank.get_frame(i) for i in range(fbank.num_frames_ready)])
    return matrix - matrix.mean(axis=0, keepdims=True)


def infer_features(session, matrix: np.ndarray) -> np.ndarray:
    output = session.run(["embs"], {"feats": matrix[None].astype(np.float32)})[0]
    return normalize(output.reshape(-1))


def probabilities(runtime: AudioModelRuntime, samples: np.ndarray) -> list[float]:
    runtime.vad.reset()
    padded = np.pad(samples, (0, SAMPLE_RATE + (-(len(samples) + SAMPLE_RATE) % WINDOW)))
    return [
        runtime.vad.infer(padded[start : start + WINDOW].astype(np.float32) / 32768)
        for start in range(0, len(padded), WINDOW)
    ]


def bounds(values: list[float], *, exit_threshold: float, padding_ms: int = 0) -> list[tuple[int, int]]:
    # 対照群は現行の同一しきい値・500ms終了・最短500msと一致させる。
    result = []
    start = None
    last_positive = 0
    silence = 0
    for index, probability in enumerate(values):
        if start is None:
            if probability >= 0.4:
                start = index * WINDOW
                last_positive = (index + 1) * WINDOW
            continue
        if probability >= exit_threshold:
            last_positive = (index + 1) * WINDOW
            silence = 0
        else:
            silence += WINDOW
        if silence >= 8000:
            if last_positive - start >= 8000:
                padding = padding_ms * 16
                result.append((max(0, start - padding), last_positive + padding))
            start = None
            silence = 0
    return result


def metrics(rows: list[dict], threshold: float) -> dict:
    genuine = [r["genuine"] for r in rows]
    impostors = [score for r in rows for score in r["impostors"]]
    return {
        "probe_count": len(rows),
        "genuine_min": float(np.min(genuine)),
        "genuine_median": float(np.median(genuine)),
        "genuine_max": float(np.max(genuine)),
        "false_rejects": sum(score < threshold for score in genuine),
        "impostor_comparisons": len(impostors),
        "impostor_max": float(np.max(impostors)),
        "false_accepts": sum(score >= threshold for score in impostors),
        "identification_errors_with_margin": sum(
            r["genuine"] < threshold
            or r["genuine"] - max(r["impostors"]) < 0.05
            for r in rows
        ),
    }


def compare(reference_path: Path | None, candidate_model: Path | None, baseline_model: Path) -> dict:
    torch = kaldi = None
    if reference_path is not None:
        if not reference_path.is_dir():
            raise RuntimeError("Reference dependency directory does not exist.")
        sys.path.insert(0, str(reference_path))
        import torch
        from torchaudio.compliance import kaldi

        torch.set_num_threads(1)

    with ThreadPoolExecutor(max_workers=4) as executor:
        waves = dict(executor.map(load_public_wave, FILES))
    runtime = AudioModelRuntime()
    baseline_bytes = baseline_model.read_bytes()
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    if baseline_hash != "9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1":
        raise RuntimeError("Historical WeSpeaker baseline model hash mismatch.")
    baseline_session = runtime._build_session(baseline_bytes)
    timings = {"current": [], "candidate": []}

    def embed(samples: np.ndarray) -> np.ndarray:
        started = time.perf_counter()
        value = infer_features(baseline_session, features(samples))
        timings["current"].append((time.perf_counter() - started) * 1000)
        return value

    candidate_info = None
    candidate_session = None
    if candidate_model is not None:
        payload = candidate_model.read_bytes()
        candidate_session = runtime._build_session(payload)
        metadata = candidate_session.get_modelmeta().custom_metadata_map
        required = {"framework": "3d-speaker", "normalize_samples": "1", "feature_normalize_type": "global-mean", "sample_rate": "16000"}
        if any(metadata.get(key) != value for key, value in required.items()):
            raise RuntimeError("Candidate model requires unsupported preprocessing.")
        candidate_info = {"sha256": hashlib.sha256(payload).hexdigest(), "metadata": metadata}

    def candidate_embed(samples: np.ndarray) -> np.ndarray:
        # sherpa-onnx/scripts/3dspeaker/test-onnx.pyの前処理を使用する。
        started = time.perf_counter()
        options = knf.FbankOptions()
        options.frame_opts.dither = 0
        options.frame_opts.samp_freq = 16000
        options.frame_opts.snip_edges = True
        options.mel_opts.num_bins = 80
        fbank = knf.OnlineFbank(options)
        fbank.accept_waveform(16000, (samples.astype(np.float32) / 32768).tolist())
        fbank.input_finished()
        matrix = np.asarray([fbank.get_frame(i) for i in range(fbank.num_frames_ready)])
        matrix -= matrix.mean(axis=0, keepdims=True)
        output = candidate_session.run(["embedding"], {"x": matrix[None]})[0]
        timings["candidate"].append((time.perf_counter() - started) * 1000)
        return normalize(output.reshape(-1))

    enrollment = {}
    training = {}
    for speaker in SPEAKERS:
        training[speaker] = []
        for name, samples in waves.items():
            if not name.startswith(speaker + "-sr-"):
                continue
            segments = bounds(probabilities(runtime, samples), exit_threshold=0.4)
            if not segments:
                raise RuntimeError(f"No enrollment speech segment: {name}")
            training[speaker].append(samples[segments[0][0] : min(segments[-1][1], len(samples))])
        clips = training[speaker]
        combined = np.concatenate(clips)
        if len(combined) < 32000:
            raise RuntimeError(f"Insufficient enrollment audio: {speaker}")
        # 同じ登録音声群から開始・中央・末尾の2秒を使う。区間は重なり得る。
        offsets = (0, (len(combined) - 32000) // 2, len(combined) - 32000)
        short_clips = [combined[offset : offset + 32000] for offset in offsets]
        short_embeddings = [embed(clip) for clip in short_clips]
        full_embeddings = [embed(clip) for clip in clips]
        enrollment[speaker] = {
            "centroid_2s": [normalize(np.mean(short_embeddings, axis=0))],
            "centroid_full": [normalize(np.mean(full_embeddings, axis=0))],
            "concatenated_full": [embed(combined)],
            "max_2s_templates": short_embeddings,
        }
        if candidate_session is not None:
            enrollment[speaker]["candidate_centroid_2s"] = [normalize(np.mean([candidate_embed(clip) for clip in short_clips], axis=0))]
            enrollment[speaker]["candidate_centroid_full"] = [normalize(np.mean([candidate_embed(clip) for clip in clips], axis=0))]

    def score(speaker: str, embedding: np.ndarray, mode: str) -> dict:
        scores = {
            target: max(float(np.dot(embedding, template)) for template in enrollment[target][mode])
            for target in SPEAKERS
        }
        return {"genuine": scores[speaker], "impostors": [value for target, value in scores.items() if target != speaker]}

    duration_rows = {}
    segmentation_rows = {}
    parity = []
    segmentation_lengths = []
    for name, samples in waves.items():
        if "-test-" not in name:
            continue
        speaker = name.split("-")[0]
        values = probabilities(runtime, samples)
        baseline = bounds(values, exit_threshold=0.4)
        if not baseline:
            raise RuntimeError(f"No baseline speech segment: {name}")
        start, end = max(baseline, key=lambda pair: pair[1] - pair[0])
        speech = samples[start : min(end, len(samples))]
        for duration in (0.5, 0.8, 1.2, 2.0, 3.0):
            count = round(duration * SAMPLE_RATE)
            if len(speech) < count:
                continue
            for offset in sorted({0, (len(speech) - count) // 2, len(speech) - count}):
                probe = speech[offset : offset + count]
                embedding = embed(probe)
                for mode in ENROLLMENT_MODES:
                    row = {"file": name, "offset_seconds": (start + offset) / SAMPLE_RATE, **score(speaker, embedding, mode)}
                    duration_rows.setdefault(f"{duration:.1f}", {}).setdefault(mode, []).append(row)
                matrix = features(probe)
                repeat = np.tile(matrix, (int(np.ceil(200 / len(matrix))), 1))[:200] if len(matrix) < 200 else matrix
                difference = max(0, 200 - len(matrix))
                reflect = np.pad(matrix, ((difference // 2, difference - difference // 2), (0, 0)), mode="reflect")
                for mode, padded in (("probe_repeat_200frames", repeat), ("probe_reflect_200frames", reflect)):
                    row = {"file": name, "offset_seconds": (start + offset) / SAMPLE_RATE, **score(speaker, infer_features(baseline_session, padded), "centroid_2s")}
                    duration_rows[f"{duration:.1f}"].setdefault(mode, []).append(row)
                if candidate_session is not None:
                    candidate_embedding = candidate_embed(probe)
                    for mode in ("candidate_centroid_2s", "candidate_centroid_full"):
                        row = {"file": name, "offset_seconds": (start + offset) / SAMPLE_RATE, **score(speaker, candidate_embedding, mode)}
                        duration_rows[f"{duration:.1f}"].setdefault(mode, []).append(row)

        for variant, exit_threshold, padding in (
            ("baseline", 0.4, 0),
            ("pad_30ms", 0.4, 30),
            ("pad_100ms", 0.4, 100),
            ("hysteresis_pad_100ms", 0.25, 100),
        ):
            segments = bounds(values, exit_threshold=exit_threshold, padding_ms=padding)
            durations = [(min(b, len(samples)) - a) / SAMPLE_RATE for a, b in segments]
            segmentation_lengths.append({"file": name, "variant": variant, "durations": durations})
            for a, b in segments:
                embedding = embed(samples[a : min(b, len(samples))])
                segmentation_rows.setdefault(variant, []).append(score(speaker, embedding, "centroid_2s"))

        if kaldi is not None:
            matrix = features(speech)
            reference = kaldi.fbank(
                torch.from_numpy(speech.astype(np.float32)).unsqueeze(0),
                num_mel_bins=80, frame_length=25, frame_shift=10,
                dither=0, sample_frequency=16000, window_type="hamming", use_energy=False,
            ).numpy()
            reference -= reference.mean(axis=0, keepdims=True)
            parity.append({
                "file": name,
                "feature_max_abs_error": float(np.max(np.abs(matrix - reference))),
                "feature_mean_abs_error": float(np.mean(np.abs(matrix - reference))),
                "embedding_cosine": float(np.dot(embed(speech), infer_features(baseline_session, reference))),
            })
        print(f"Compared {name}", flush=True)

    thresholds = (0.3, 0.35, 0.4, 0.6)
    return {
        "model_id": "wespeaker-resnet34-voxceleb-v1",
        "baseline_model_sha256": baseline_hash,
        "package_versions": {name: importlib.metadata.version(name) for name in ("numpy", "onnxruntime", "kaldi-native-fbank")},
        "candidate": candidate_info,
        "embedding_timing_ms": {key: {"count": len(values), "median": float(np.median(values)), "max": max(values)} for key, values in timings.items() if values},
        "scope": "3 public male speakers; Chinese; overlapping crops are correlated; not a calibrated FAR/FRR estimate for production",
        "sources": [{"url": BASE_URL + name + ".wav", "sha256": digest} for name, digest in FILES.items()],
        "enrollment_seconds": {speaker: [len(clip) / SAMPLE_RATE for clip in clips] for speaker, clips in training.items()},
        "duration_summary": {
            duration: {mode: {str(threshold): metrics(rows, threshold) for threshold in thresholds} for mode, rows in modes.items()}
            for duration, modes in duration_rows.items()
        },
        "duration_scores": duration_rows,
        "segmentation_summary": {variant: metrics(rows, 0.4) for variant, rows in segmentation_rows.items()},
        "segmentation_lengths": segmentation_lengths,
        "reference_parity": parity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-model", type=Path, required=True)
    parser.add_argument("--reference-path", type=Path)
    parser.add_argument("--candidate-model", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new output path.")
    result = compare(args.reference_path, args.candidate_model, args.baseline_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    for duration, modes in result["duration_summary"].items():
        print(duration, json.dumps({mode: scores["0.4"] for mode, scores in modes.items()}))
    print("Segmentation:", json.dumps(result["segmentation_summary"]))
    print("Reference parity:", json.dumps(result["reference_parity"]))


if __name__ == "__main__":
    main()
