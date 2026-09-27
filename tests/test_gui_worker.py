"""Offline checks for record-first dictation and whole-file transcription."""

from __future__ import annotations

import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from gemini_live_dictation.gui import BatchTranscribeWorker, DictationWindow, LocalRecorder, Microphone


class FakeInputStream:
    def __init__(self, *, callback, **_kwargs) -> None:
        self.callback = callback
        self.started = False

    def start(self) -> None:
        self.started = True
        self.callback(bytes(3200), 1600, None, None)

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        pass


class GuiWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_audio_stays_local_until_recording_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.wav"
            recorder = LocalRecorder(Microphone(1, "Test microphone", 48000, "WASAPI"), path)
            with patch("gemini_live_dictation.gui.sd.RawInputStream", FakeInputStream):
                recorder.start()
                self.assertFalse(path.exists())
                output = recorder.finish()
            with wave.open(str(output), "rb") as recording:
                self.assertEqual(recording.getframerate(), 48000)
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(len(recording.readframes(1600)), 3200)

    def test_batch_worker_submits_whole_file_once_with_vocabulary(self) -> None:
        uploaded = SimpleNamespace(uri="files/test.wav", mime_type="audio/wav")
        client = SimpleNamespace(
            files=SimpleNamespace(upload=Mock(return_value=uploaded)),
            interactions=SimpleNamespace(create=Mock(return_value=SimpleNamespace(output_text="测试 QUBIG"))),
        )
        worker = BatchTranscribeWorker("test-key", "VERBATIM", Path("test.wav"), ["QUBIG"])
        texts: list[str] = []
        errors: list[str] = []
        worker.text_ready.connect(texts.append)
        worker.failed.connect(errors.append)
        with patch("gemini_live_dictation.gui.genai.Client", return_value=client):
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(texts, ["测试 QUBIG"])
        client.files.upload.assert_called_once_with(file="test.wav")
        request = client.interactions.create.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.5-transcribe")
        self.assertEqual(request["generation_config"]["transcription_config"]["custom_vocabulary"], ["QUBIG"])
        self.assertEqual(request["generation_config"]["transcription_config"]["mode"], {"type": "verbatim"})

    def test_first_click_only_records_and_second_click_starts_transcription(self) -> None:
        microphone = Microphone(1, "Test microphone", 48000, "WASAPI")
        fake_recorder = Mock()
        fake_recorder.finish.return_value = Path("test.wav")
        with (
            patch("gemini_live_dictation.gui.microphone_options", return_value=[microphone]),
            patch("gemini_live_dictation.gui.sd.query_devices", return_value={"name": "Test microphone"}),
            patch("gemini_live_dictation.gui.saved_api_key", return_value="test-key"),
            patch("gemini_live_dictation.gui.read_vocabulary", return_value=["QUBIG"]),
            patch("gemini_live_dictation.gui.LocalRecorder", return_value=fake_recorder),
            patch.object(BatchTranscribeWorker, "start") as start_worker,
        ):
            window = DictationWindow()
            window._toggle_recording()
            self.assertIsNone(window.worker)
            fake_recorder.start.assert_called_once()
            start_worker.assert_not_called()
            window._toggle_recording()
            fake_recorder.finish.assert_called_once()
            start_worker.assert_called_once()
            window.close()


if __name__ == "__main__":
    unittest.main()
