"""Offline checks for record-first dictation and whole-file transcription."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
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
    VocabularyDialog,
    join_live_transcripts,
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
        self.events: list[str] = []
        self.audio_chunks: list[bytes] = []
        self.stream_ended = asyncio.Event()

    async def send_realtime_input(self, *, audio=None, activity_start=None, activity_end=None) -> None:
        if activity_start is not None:
            self.events.append("start")
        if audio is not None:
            self.events.append("audio")
            self.audio_chunks.append(audio.data)
        if activity_end is not None:
            self.events.append("end")
            self.stream_ended.set()

    async def receive(self):
        await self.stream_ended.wait()
        yield SimpleNamespace(server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text="测试 QUBIG"), generation_complete=None
        ))
        yield SimpleNamespace(server_content=SimpleNamespace(input_transcription=None, generation_complete=True))
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

    def test_live_chunks_do_not_create_paragraphs(self) -> None:
        self.assertEqual(join_live_transcripts(["其实当时的时候我们已经开始这个", "就是"]),
                         "其实当时的时候我们已经开始这个就是")
        self.assertEqual(join_live_transcripts(["Hello", "world.", "Again"]),
                         "Hello world. Again")

    def test_level_status_keeps_meter_width_when_message_changes(self) -> None:
        with patch("gemini_live_dictation.gui.microphone_options", return_value=[]):
            window = DictationWindow()
            window.show()
            self.app.processEvents()
            meter_width = window.level_meter.width()
            status_width = window.level_status.width()
            for message in ("正在拾音", "声音偏小 / 安静", "录音已结束"):
                window.level_status.setText(message)
                self.app.processEvents()
                self.assertEqual(window.level_status.width(), status_width)
                self.assertEqual(window.level_meter.width(), meter_width)
            window.close()

    def test_vocabulary_dialog_sorts_flags_duplicates_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vocabulary.txt"
            path.write_text("# note\nzeeman\n量子\nQUBIG\nfrancium\nQUBIG\n", encoding="utf-8")
            dialog = VocabularyDialog(None, path)
            rows = [dialog.list.item(row).text() for row in range(dialog.list.count())]
            self.assertEqual(rows, ["francium", "QUBIG", "zeeman", "量子"])

            dialog.entry.setText("qubig")
            self.assertFalse(dialog.add_button.isEnabled())
            self.assertIn("QUBIG", dialog.feedback.text())
            dialog._add()
            self.assertEqual(len(dialog.terms), 4)

            dialog.entry.setText("Autler-Townes")
            self.assertTrue(dialog.add_button.isEnabled())
            dialog._add()
            self.assertEqual(dialog.list.item(0).text(), "Autler-Townes")
            self.assertEqual(dialog.entry.text(), "")

            dialog.list.setCurrentRow(dialog.terms.index("zeeman"))
            dialog._delete_selected()
            dialog._save()
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "# note\nAutler-Townes\nfrancium\nQUBIG\n量子\n")

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
            patch("gemini_live_dictation.gui.LIVE_FINALIZATION_SECONDS", 5),
        ):
            started = time.monotonic()
            worker.run()
        self.assertLess(time.monotonic() - started, 2, "generation_complete should end the wait early")
        # The whole recording is one manual activity, so Live finalizes it with full context.
        self.assertEqual(session.events, ["start", "audio", "end"])
        self.assertEqual(texts, ["测试 QUBIG"])
        request = connect.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.5-transcribe-live")
        self.assertTrue(request["config"].realtime_input_config.automatic_activity_detection.disabled)
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
