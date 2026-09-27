"""Offline checks for record-first dictation and whole-file transcription."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from gemini_live_dictation.cli import decode_file_to_pcm
from gemini_live_dictation.gui import (
    BatchTranscribeWorker,
    DictationWindow,
    LiveReplayWorker,
    LocalRecorder,
    Microphone,
)


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


class FakeLiveSession:
    def __init__(self) -> None:
        self.audio_chunks: list[bytes] = []
        self.stream_ended = asyncio.Event()

    async def send_realtime_input(self, *, audio=None, audio_stream_end=False) -> None:
        if audio is not None:
            self.audio_chunks.append(audio.data)
        if audio_stream_end:
            self.stream_ended.set()

    async def receive(self):
        await self.stream_ended.wait()
        yield SimpleNamespace(server_content=SimpleNamespace(input_transcription=SimpleNamespace(text="测试 QUBIG")))
        await asyncio.Event().wait()


class FakeLiveConnection:
    def __init__(self, session: FakeLiveSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeLiveSession:
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


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
                recorder.stream.callback(array("h", [10000] * 1600).tobytes(), 1600, None, None)
                self.assertGreater(recorder.level, 0)
                output = recorder.finish()
            with wave.open(str(output), "rb") as recording:
                self.assertEqual(recording.getframerate(), 48000)
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(len(recording.readframes(3200)), 6400)
            self.assertGreater(len(decode_file_to_pcm(output)), 0)

    def test_live_replay_uses_saved_audio_and_only_emits_final_text(self) -> None:
        session = FakeLiveSession()
        connect = Mock(return_value=FakeLiveConnection(session))
        client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect)))
        worker = LiveReplayWorker("test-key", "SMART", Path("test.wav"), ["QUBIG"])
        texts: list[str] = []
        worker.text_ready.connect(texts.append)
        with (
            patch("gemini_live_dictation.gui.decode_file_to_pcm", return_value=bytes(3200)),
            patch("gemini_live_dictation.gui.genai.Client", return_value=client),
            patch("gemini_live_dictation.gui.LIVE_FINALIZATION_SECONDS", 0.05),
        ):
            worker.run()
        self.assertTrue(session.stream_ended.is_set())
        self.assertEqual(len(session.audio_chunks), 1)
        self.assertEqual(texts, ["测试 QUBIG"])
        request = connect.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.5-transcribe-live")
        self.assertEqual(request["config"].input_audio_transcription.custom_vocabulary, ["QUBIG"])
        self.assertEqual(request["config"].input_audio_transcription.mode, "SMART")

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
            patch.object(LiveReplayWorker, "start") as start_worker,
        ):
            window = DictationWindow()
            self.assertEqual(window.engine_combo.currentData(), "LIVE")
            window._toggle_recording()
            self.assertIsNone(window.worker)
            self.assertTrue(window.level_timer.isActive())
            fake_recorder.start.assert_called_once()
            start_worker.assert_not_called()
            window._toggle_recording()
            self.assertFalse(window.level_timer.isActive())
            fake_recorder.finish.assert_called_once()
            start_worker.assert_called_once()
            window.close()

    def test_live_limit_warns_and_auto_stops(self) -> None:
        microphone = Microphone(1, "Test microphone", 48000, "WASAPI")
        with (
            patch("gemini_live_dictation.gui.microphone_options", return_value=[microphone]),
            patch("gemini_live_dictation.gui.sd.query_devices", return_value={"name": "Test microphone"}),
        ):
            window = DictationWindow()
            window.recorder = Mock()
            window.elapsed_seconds = 479
            with patch.object(window, "_stop_recording") as stop:
                window._tick()
                self.assertIn("30 秒", window.status_label.text())
                stop.assert_not_called()
                window.elapsed_seconds = 509
                window._tick()
                stop.assert_called_once()
            window.recorder = None
            window.close()


if __name__ == "__main__":
    unittest.main()
