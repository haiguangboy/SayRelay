"""Exercise cancellation, silence and output routing without touching the real clipboard."""

import tempfile
import threading
import time
import unittest
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from opencc import OpenCC
from gi.repository import GLib

from voice_input.audio import read_pcm
from voice_input.config import Config
from voice_input.recognizer import Recognizer
from voice_input.punctuation import MODEL_PATH, merge_punctuation
from voice_input.service import Controller


def spin(until, timeout=4):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not until() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)
    if not until():
        raise AssertionError("asynchronous pipeline did not finish")


class Keyboard:
    def __init__(self, changed):
        self.session = None
        self.connecting = False
        self.restore_token = ""
        self.pastes = []
        self.connections = 0

    def connect(self):
        self.connecting = True
        self.connections += 1

    def paste(self, terminal=False):
        self.pastes.append(terminal)

    def close(self):
        pass


class FakeRecognizer:
    gate = None

    def __init__(self, config):
        pass

    def transcribe(self, pcm, language, punctuation=True, decoding="fast", vocabulary=""):
        if self.gate:
            self.gate.wait(3)
        return {"text": "公开测试文字", "audio_seconds": 1.0, "recognition_seconds": 0.01}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch("voice_input.service.Config.load", return_value=Config()),
                        patch("voice_input.service.Recognizer", FakeRecognizer),
                        patch("voice_input.service.PortalKeyboard", Keyboard),
                        patch("voice_input.service.notify"),
                        patch("voice_input.service.copy_text")]
        self.mocks = [p.start() for p in self.patches]
        FakeRecognizer.gate = None
        self.controller = Controller()
        spin(lambda: self.controller.state == "idle")

    def tearDown(self):
        if FakeRecognizer.gate:
            FakeRecognizer.gate.set()
        self.controller.close()
        for p in reversed(self.patches):
            p.stop()

    def recognize(self, deliver=True):
        self.controller._new_job(deliver)
        self.controller._recognize(lambda: np.ones(16000, dtype=np.float32) * 0.05)

    def test_cancel_during_transcription_never_copies_or_pastes(self):
        FakeRecognizer.gate = threading.Event()
        self.controller.keyboard.session = "granted"
        self.recognize()
        self.controller.cancel()
        FakeRecognizer.gate.set()
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["state"], "cancelled")
        self.mocks[-1].assert_not_called()
        self.assertEqual(self.controller.keyboard.pastes, [])

    def test_no_permission_preserves_text_in_clipboard(self):
        self.recognize()
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["delivery"], "clipboard")
        self.mocks[-1].assert_called_once_with("公开测试文字")
        self.assertEqual(self.controller.keyboard.pastes, [])
        self.mocks[-2].assert_called_with("未自动粘贴：文字已保留", self.controller.message, persistent=True)

    def test_recording_preflight_requests_keyboard_without_recording(self):
        with patch.object(self.controller.microphone, "start") as start:
            response = self.controller.start()
        start.assert_not_called()
        self.assertFalse(response["recording_started"])
        self.assertEqual(response["reason"], "keyboard_authorization")
        self.assertEqual(self.controller.keyboard.connections, 1)
        self.assertEqual(self.controller.state, "idle")
        self.assertIsNone(self.controller.job)

    def test_saved_authorization_is_restored_on_startup(self):
        class RememberedKeyboard(Keyboard):
            def __init__(self, changed):
                super().__init__(changed)
                self.restore_token = "remembered"

        with patch("voice_input.service.PortalKeyboard", RememberedKeyboard):
            controller = Controller()
        try:
            spin(lambda: controller.state == "idle" and controller.keyboard.connections == 1)
            self.assertTrue(controller.status()["keyboard_remembered"])
        finally:
            controller.close()

    def test_clipboard_mode_can_record_without_keyboard_permission(self):
        self.controller.config.output = "clipboard"
        with patch.object(self.controller.microphone, "start") as start:
            response = self.controller.start()
        start.assert_called_once()
        self.assertEqual(response["state"], "recording")
        self.assertEqual(self.controller.keyboard.connections, 0)

    def test_permission_lost_during_inference_keeps_text_and_warns(self):
        FakeRecognizer.gate = threading.Event()
        self.controller.keyboard.session = "granted"
        self.recognize()
        self.controller.keyboard.session = None
        FakeRecognizer.gate.set()
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["delivery"], "clipboard")
        self.assertEqual(self.controller.last_text, "公开测试文字")
        self.assertEqual(self.controller.keyboard.pastes, [])
        self.assertIn("未自动粘贴", self.controller.message)
        self.mocks[-2].assert_called_with("未自动粘贴：文字已保留", self.controller.message, persistent=True)

    def test_paste_failure_reports_preserved_text(self):
        self.controller.keyboard.session = "granted"
        with patch.object(self.controller.keyboard, "paste", side_effect=RuntimeError("session ended")):
            self.recognize()
            spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["delivery"], "clipboard")
        self.mocks[-2].assert_called_with("未自动粘贴：文字已保留",
                                         "录音已转写，请回到输入框按 Ctrl+V。下次录音前会检查授权。", persistent=True)

    def test_file_api_does_not_change_clipboard_without_deliver(self):
        self.recognize(deliver=False)
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.last_text, "公开测试文字")
        self.mocks[-1].assert_not_called()

    def test_terminal_mode_uses_terminal_paste(self):
        self.controller.config.output = "terminal"
        self.controller.keyboard.session = "granted"
        self.recognize()
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.keyboard.pastes, [True])

    def test_cancel_after_copy_prevents_scheduled_paste(self):
        self.controller.keyboard.session = "granted"
        self.recognize()
        spin(lambda: self.controller.state == "delivering")
        self.controller.cancel()
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["state"], "cancelled")
        self.assertEqual(self.controller.keyboard.pastes, [])

    def test_busy_service_rejects_a_second_recording(self):
        FakeRecognizer.gate = threading.Event()
        self.recognize()
        with self.assertRaises(RuntimeError):
            self.controller.start()
        FakeRecognizer.gate.set()
        spin(lambda: self.controller.state == "idle")


class AudioTests(unittest.TestCase):
    def test_silence_never_invokes_whisper(self):
        recognizer = Recognizer.__new__(Recognizer)
        self.assertEqual(recognizer.transcribe(np.zeros(16000, dtype=np.float32))["text"], "")

    def test_audio_interface_rejects_wrong_sample_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 8000)
            with self.assertRaises(ValueError):
                read_pcm(path)


class TranscriptTests(unittest.TestCase):
    @unittest.skipUnless(MODEL_PATH.is_file(), "local punctuation model is not installed")
    def test_mixed_names_survive_normalization_with_opt_in_decoding(self):
        from unittest.mock import Mock
        from voice_input.punctuation import Punctuator
        recognizer = Recognizer.__new__(Recognizer)
        recognizer.simplifier = OpenCC("t2s")
        recognizer.punctuator = Punctuator()
        recognizer.model = Mock()
        recognizer.model.transcribe.return_value = (
            iter([SimpleNamespace(text="今天我們談到 Vance 和 Washington, D.C.，"),
                  SimpleNamespace(text="CNN 報導了 NASDAQ 與 Intel。")]), None)
        result = recognizer.transcribe(np.full(16000, 0.05, dtype=np.float32),
                                       decoding="careful", vocabulary="Vance, Washington, D.C.")
        self.assertIn("今天我们谈到 Vance 和 Washington, D.C.，", result["text"])
        self.assertIn("CNN 报导了 NASDAQ 与 Intel。", result["text"])
        options = recognizer.model.transcribe.call_args.kwargs
        self.assertEqual(options["beam_size"], 5)
        self.assertEqual(options["hotwords"], "Vance, Washington, D.C.")
        self.assertEqual(options["task"], "transcribe")

    def test_traditional_output_is_simplified_before_returning(self):
        recognizer = Recognizer.__new__(Recognizer)
        recognizer.simplifier = OpenCC("t2s")
        # Separate segments exercise normalization across the completed sentence.
        segments = [SimpleNamespace(text="請將語音辨識結果貼到 "),
                    SimpleNamespace(text="Codex 對話框，檔案為 /home/demo/app.py；Git 分支 feature/asr。")]
        from unittest.mock import Mock
        recognizer.model = Mock()
        recognizer.model.transcribe.return_value = (iter(segments), None)
        result = recognizer.transcribe(np.full(16000, 0.05, dtype=np.float32), punctuation=False)
        self.assertEqual(result["text"], "请将语音辨识结果贴到 Codex 对话框，档案为 /home/demo/app.py；Git 分支 feature/asr。")
        self.assertEqual(recognizer.model.transcribe.call_args.kwargs["beam_size"], 1)
        self.assertIsNone(recognizer.model.transcribe.call_args.kwargs["hotwords"])


class ConfigurationTests(unittest.TestCase):
    def test_previous_configuration_keeps_the_fast_baseline(self):
        config = Config(**{"model": "small", "language": "zh", "punctuation": True}).validate()
        self.assertEqual((config.decoding, config.vocabulary), ("fast", ""))

    def test_configuration_rejects_invalid_decoder_and_unbounded_prompts(self):
        for values in [{"decoding": "unknown"}, {"vocabulary": ["Vance"]},
                       {"vocabulary": "x" * 401}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values).validate()


class PunctuationTransferTests(unittest.TestCase):

    def test_only_marks_transfer_while_original_paths_and_spacing_survive(self):
        original = "先打开 /home/demo/app.py 再检查 v3.12 和 feature/asr 然后测试"
        predicted = "先打开 / home， / demo / app . py，再检查 v3 . 12 和 feature， / asr，然后测试。"
        self.assertEqual(merge_punctuation(original, predicted),
                         "先打开 /home/demo/app.py， 再检查 v3.12 和 feature/asr， 然后测试。")

    def test_prediction_cannot_modify_drop_or_duplicate_words(self):
        original = "请先阅读代码然后进行测试"
        for predicted in ["请先阅读代码。", "请先阅读文档，然后进行测试。", "请先阅读代码代码，然后进行测试。"]:
            with self.subTest(predicted=predicted):
                self.assertEqual(merge_punctuation(original, predicted), original)

    def test_existing_marks_and_paragraph_boundaries_survive(self):
        original = "你好，世界！\n请问可以吗？"
        predicted = "你好,世界。请问可以吗?"
        self.assertEqual(merge_punctuation(original, predicted), original)


if __name__ == "__main__":
    unittest.main()
