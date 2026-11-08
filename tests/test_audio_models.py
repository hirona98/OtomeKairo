import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import kaldi_native_fbank as knf
import numpy as np

from otomekairo.audio.models import (
    AudioModelError,
    AudioModelRuntime,
    SPEAKER_EMBEDDING_DIMENSION,
    SileroVadSession,
)
from otomekairo.store.config import ConfigStore


class SileroVadSessionTests(unittest.TestCase):
    def test_onnx_receives_context_and_recurrent_state_until_reset(self) -> None:
        # ONNXの契約: 512 sampleごとに直前64 sampleとstateを引き継ぐ。
        calls = []

        def run(output_names, inputs):
            calls.append({key: value.copy() for key, value in inputs.items()})
            return np.asarray([[0.75]], dtype=np.float32), inputs["state"] + 1

        session = SileroVadSession(SimpleNamespace(run=run), np)
        first = np.linspace(-0.5, 0.5, 512, dtype=np.float32)
        second = np.linspace(0.25, -0.25, 512, dtype=np.float32)

        self.assertEqual(session.infer(first), 0.75)
        session.infer(second)
        session.reset()
        session.infer(second)

        self.assertEqual(calls[0]["input"].shape, (1, 576))
        self.assertEqual(calls[0]["input"].dtype, np.float32)
        np.testing.assert_array_equal(calls[0]["input"][0, :64], np.zeros(64))
        np.testing.assert_array_equal(calls[0]["input"][0, 64:], first)
        np.testing.assert_array_equal(calls[1]["input"][0, :64], first[-64:])
        np.testing.assert_array_equal(calls[1]["input"][0, 64:], second)
        np.testing.assert_array_equal(calls[1]["state"], np.ones((2, 1, 128)))
        np.testing.assert_array_equal(calls[2]["input"][0, :64], np.zeros(64))
        np.testing.assert_array_equal(calls[2]["state"], np.zeros((2, 1, 128)))


class SpeakerFeatureInputTests(unittest.TestCase):
    def test_fbank_receives_normalized_float_samples_and_povey_window(self) -> None:
        pcm_samples = np.asarray([-32768, -100, -1, 0, 1, 100, 32767], dtype="<i2")
        fbank = Mock(num_frames_ready=1)
        fbank.get_frame.return_value = np.zeros(80, dtype=np.float32)
        runtime = AudioModelRuntime.__new__(AudioModelRuntime)
        runtime._np = np
        runtime._knf = SimpleNamespace(
            FbankOptions=knf.FbankOptions,
            OnlineFbank=Mock(return_value=fbank),
        )
        runtime._speaker_lock = threading.Lock()
        runtime._speaker_session = Mock()
        runtime._speaker_session.run.return_value = [
            np.ones((1, SPEAKER_EMBEDDING_DIMENSION), dtype=np.float32)
        ]

        embedding = runtime.extract_speaker_embedding(pcm_samples.tobytes())

        sample_rate, received_samples = fbank.accept_waveform.call_args.args
        self.assertEqual(sample_rate, 16000.0)
        np.testing.assert_array_equal(received_samples, pcm_samples.astype(np.float32) / 32768)
        options = runtime._knf.OnlineFbank.call_args.args[0]
        self.assertEqual(options.frame_opts.window_type, "povey")
        self.assertTrue(options.frame_opts.snip_edges)
        output_names, inputs = runtime._speaker_session.run.call_args.args
        self.assertEqual(output_names, ["embedding"])
        self.assertEqual(set(inputs), {"x"})
        self.assertEqual(inputs["x"].shape, (1, 1, 80))
        self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0)
        vad = SileroVadSession(Mock(), np)
        np.testing.assert_array_equal(
            vad.float32_samples(pcm_samples.tolist()),
            pcm_samples.astype(np.float32) / 32768.0,
        )


class BundledSpeakerModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = AudioModelRuntime()

    def test_embedding_is_finite_normalized_and_repeatable(self) -> None:
        samples = np.random.default_rng(7).integers(-1000, 1001, 16000, dtype="<i2")
        first = self.runtime.extract_speaker_embedding(samples.tobytes())
        second = self.runtime.extract_speaker_embedding(samples.tobytes())
        self.assertEqual(len(first), 192)
        self.assertTrue(np.isfinite(first).all())
        self.assertAlmostEqual(float(np.linalg.norm(first)), 1.0, places=5)
        np.testing.assert_array_equal(first, second)

    def test_shared_weights_keep_vad_state_independent(self) -> None:
        second = AudioModelRuntime()
        self.assertIs(self.runtime._speaker_session, second._speaker_session)
        self.assertIs(self.runtime._silero_session, second._silero_session)
        self.assertIs(self.runtime._speaker_lock, second._speaker_lock)
        self.runtime.vad.reset()
        self.runtime.vad.infer(np.linspace(-0.5, 0.5, 512, dtype=np.float32))
        state = self.runtime.vad._state.copy()
        context = self.runtime.vad._context.copy()
        second.vad.infer(np.zeros(512, dtype=np.float32))
        second.vad.reset()
        np.testing.assert_array_equal(self.runtime.vad._state, state)
        np.testing.assert_array_equal(self.runtime.vad._context, context)

    def test_old_model_registration_is_excluded_from_identification(self) -> None:
        probe = [1.0] + [0.0] * (SPEAKER_EMBEDDING_DIMENSION - 1)
        old = {
            "person_ref": "person:voice:old",
            "registration_status": "registered",
            "model_id": "wespeaker-resnet34-voxceleb-v1",
            "embedding": [1.0] + [0.0] * 255,
        }
        self.assertFalse(self.runtime.identify_speaker(probe, [old], threshold=0.4).accepted)
        current = {
            **old,
            "person_ref": "person:voice:current",
            "model_id": self.runtime.speaker_model_id,
            "embedding": probe,
        }
        result = self.runtime.identify_speaker(probe, [old, current], threshold=0.4)
        self.assertTrue(result.accepted)
        self.assertEqual(result.person_ref, current["person_ref"])

    def test_malformed_current_registration_fails_explicitly(self) -> None:
        probe = [1.0] + [0.0] * (SPEAKER_EMBEDDING_DIMENSION - 1)
        speaker = {
            "person_ref": "person:voice:test",
            "registration_status": "registered",
            "model_id": self.runtime.speaker_model_id,
            "embedding": [0.0] * 256,
        }
        with self.assertRaises(AudioModelError):
            self.runtime.identify_speaker(probe, [speaker], threshold=0.4)

    def test_preprocessing_metadata_mismatch_fails_explicitly(self) -> None:
        original = self.runtime._speaker_session
        session = Mock(wraps=original)
        metadata = dict(original.get_modelmeta().custom_metadata_map)
        metadata["normalize_samples"] = "0"
        session.get_modelmeta.return_value = SimpleNamespace(custom_metadata_map=metadata)
        with patch.object(self.runtime, "_speaker_session", session):
            with self.assertRaises(AudioModelError):
                self.runtime._validate_sessions()

    def test_reregistration_survives_store_and_model_recreation(self) -> None:
        pcm = np.random.default_rng(9).integers(-1000, 1001, 16000, dtype="<i2").tobytes()
        embedding = self.runtime.extract_speaker_embedding(pcm)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ConfigStore(root)
            store.create_conversation_display_name(
                conversation_display_name_id="name:test", display_name="テスト",
            )
            registration = {
                "person_ref": "person:voice:test",
                "conversation_display_name_id": "name:test",
            }
            store.replace_voice_speaker_registration(
                **registration,
                embedding=[1.0] + [0.0] * 255,
                model_id="wespeaker-resnet34-voxceleb-v1",
            )
            store.replace_voice_speaker_registration(
                **registration, embedding=embedding, model_id=self.runtime.speaker_model_id,
            )
            speakers = ConfigStore(root).list_voice_speakers(
                registered_only=True, include_embedding=True,
            )
            self.assertEqual(len(speakers), 1)
            self.assertEqual(speakers[0]["person_ref"], registration["person_ref"])
            self.assertEqual(len(speakers[0]["embedding"]), 192)
            restarted = AudioModelRuntime()
            result = restarted.identify_speaker(
                restarted.extract_speaker_embedding(pcm), speakers, threshold=0.6,
            )
            self.assertTrue(result.accepted)
            self.assertEqual(result.person_ref, registration["person_ref"])
