"""Offline checks for record-first dictation and whole-file transcription."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
import wave
from array import array
from contextlib import ExitStack
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
    retry_delay,
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

    def test_discarded_take_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.wav"
            recorder = LocalRecorder(Microphone(1, "Test microphone", 48000, "WASAPI"), path)
            with patch("gemini_live_dictation.gui.sd.RawInputStream", FakeInputStream):
                recorder.start()
                recorder.discard()
            self.assertIsNone(recorder.stream)
            self.assertEqual(len(recorder.samples), 0)
            self.assertFalse(path.exists())

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

    def _recording_window(self, stack: ExitStack) -> tuple[DictationWindow, Mock, Mock]:
        """A window with a fake microphone; worker threads are never actually started."""
        microphone = Microphone(1, "Test microphone", 48000, "WASAPI")
        fake_recorder = Mock()
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        fake_recorder.finish.return_value = Path(directory) / "test.wav"
        for target, value in (
            ("microphone_options", [microphone]),
            ("sd.query_devices", {"name": "Test microphone"}),
            ("saved_api_key", "test-key"),
            ("read_vocabulary", ["QUBIG"]),
            ("LocalRecorder", fake_recorder),
        ):
            stack.enter_context(patch(f"gemini_live_dictation.gui.{target}", return_value=value))
        stack.enter_context(patch("gemini_live_dictation.gui.TRANSCRIPTS_DIR", Path(directory)))
        start_worker = stack.enter_context(patch.object(LiveReplayWorker, "start"))
        return DictationWindow(), fake_recorder, start_worker

    @staticmethod
    def _settle(job, *, text: str = "", error: str = "") -> None:
        job._set_text(text)
        job._set_error(error)
        job._worker_finished()

    def test_can_record_next_segment_while_previous_transcribes(self) -> None:
        with ExitStack() as stack:
            window, fake_recorder, start_worker = self._recording_window(stack)
            self.assertEqual(window.engine_combo.currentData(), "LIVE")
            window._toggle_recording()
            self.assertEqual(window.jobs, [])
            self.assertTrue(window.level_timer.isActive())
            fake_recorder.start.assert_called_once()
            start_worker.assert_not_called()
            window._toggle_recording()
            self.assertFalse(window.level_timer.isActive())
            fake_recorder.finish.assert_called_once()
            start_worker.assert_called_once()
            # The first segment is still transcribing, yet recording again is allowed at once.
            self.assertEqual(window.jobs[0].state, "running")
            self.assertTrue(window.record_button.isEnabled())
            window._toggle_recording()
            self.assertEqual(fake_recorder.start.call_count, 2)
            # One click submits the current segment and keeps recording the next one.
            window._submit_and_record_next()
            self.assertEqual(len(window.jobs), 2)
            self.assertIsNotNone(window.recorder)
            self.assertEqual(fake_recorder.start.call_count, 3)
            self.assertEqual(start_worker.call_count, 2)

    def test_discard_throws_away_the_take_without_submitting(self) -> None:
        with ExitStack() as stack:
            window, fake_recorder, start_worker = self._recording_window(stack)
            self.assertFalse(window.discard_button.isVisibleTo(window))
            window._toggle_recording()
            self.assertTrue(window.discard_button.isVisibleTo(window))
            window._discard_recording()
            fake_recorder.discard.assert_called_once()
            fake_recorder.finish.assert_not_called()
            start_worker.assert_not_called()
            self.assertEqual(window.jobs, [])
            self.assertIsNone(window.recorder)
            self.assertFalse(window.level_timer.isActive())
            self.assertFalse(window.discard_button.isVisibleTo(window))
            self.assertTrue(window.record_button.isEnabled())
            self.assertIn("放弃", window.status_label.text())
            # The discarded take does not use up a segment number.
            window._toggle_recording()
            window._toggle_recording()
            self.assertEqual(window.jobs[0].number, 1)

    def test_queue_limits_parallel_jobs_and_waits_out_quota_refusals(self) -> None:
        with ExitStack() as stack:
            window, _recorder, _start = self._recording_window(stack)
            for _ in range(4):
                window._toggle_recording()
                window._toggle_recording()
            self.assertEqual([job.state for job in window.jobs], ["running"] * 3 + ["queued"])

            first, second, _third, fourth = window.jobs
            self._settle(first, error="1011 None. You exceeded your current quota, please check your plan.")
            self.assertEqual(first.state, "retry")
            self.assertEqual(fourth.state, "queued", "a quota refusal pauses new starts")
            self.assertIn("限流冷却", window.queue_label.text())

            first.retry_at = window.cooldown_until = 0.0
            window._dispatch_jobs()
            self.assertEqual(first.state, "running")
            self.assertEqual(first.attempts, 2)
            self.assertEqual(fourth.state, "queued", "still three in flight")

            self._settle(second, text="第二段")
            self.assertEqual(second.state, "done")
            self.assertEqual(second.transcript_path.read_text(encoding="utf-8"), "第二段\n")
            self.assertEqual(fourth.state, "running")
            self.assertEqual(window.transcript.toPlainText(), "第二段")

            self._settle(first, text="第一段")
            window._copy_all()
            self.assertEqual(QApplication.clipboard().text(), "第一段\n\n第二段")

    def test_list_removal_keeps_files_and_skips_jobs_still_transcribing(self) -> None:
        with ExitStack() as stack:
            window, _recorder, _start = self._recording_window(stack)
            for _ in range(4):
                window._toggle_recording()
                window._toggle_recording()
            first, second, third, fourth = window.jobs
            self._settle(first, text="第一段")
            self._settle(second, error="400 API key not valid")
            self._settle(third, text="第三段")
            self.assertEqual(second.state, "failed")

            # An edit made in the window is written to disk before its row is removed.
            window.job_list.setCurrentItem(window._job_item(first))
            window.transcript.setPlainText("第一段（改）")
            for job in (first, second, fourth):
                window._job_item(job).setSelected(True)
            window._remove_selected()
            self.assertEqual(window.jobs, [third, fourth], "the running job stays in the list")
            self.assertEqual(window.job_list.count(), 2)
            self.assertIn("1 段还在转写", window.status_label.text())
            self.assertEqual(first.transcript_path.read_text(encoding="utf-8"), "第一段（改）\n")

            window._copy_all()
            self.assertEqual(QApplication.clipboard().text(), "第三段")
            window._clear_done()
            self.assertEqual(window.jobs, [fourth])
            self.assertTrue(third.transcript_path.exists())

            # Numbering stays unique after removals, and rows still map to the right job.
            window._toggle_recording()
            window._toggle_recording()
            self.assertEqual(window.jobs[-1].number, 5)
            self._settle(window.jobs[-1], text="第五段")
            self.assertTrue(window._job_item(window.jobs[-1]).text().endswith("✓ 第五段"))

    def test_recording_is_deleted_once_its_transcript_is_saved(self) -> None:
        with ExitStack() as stack:
            window, fake_recorder, _start = self._recording_window(stack)
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            takes = iter(directory / name for name in ("one.wav", "two.wav"))

            def finish() -> Path:
                path = next(takes)
                path.write_bytes(b"audio")
                return path

            fake_recorder.finish.side_effect = finish
            for _ in range(2):
                window._toggle_recording()
                window._toggle_recording()
            done, failed = window.jobs
            self._settle(done, text="第一段")
            self._settle(failed, error="400 API key not valid")
            self.assertEqual(done.transcript_path.read_text(encoding="utf-8"), "第一段\n")
            self.assertFalse(done.audio_path.exists())
            self.assertTrue(failed.audio_path.exists(), "a failed take is kept for retrying")

    def test_retry_delay_follows_server_hint_and_skips_hopeless_errors(self) -> None:
        self.assertEqual(retry_delay("1011 None. You exceeded your current quota", 1), 10)
        self.assertEqual(retry_delay("429 RESOURCE_EXHAUSTED. Please retry in 44.2s.", 1), 46)
        self.assertEqual(retry_delay("Live 连接在录音发送完之前关闭。", 2), 30)
        self.assertIsNone(retry_delay("1011 quota", 4))
        self.assertIsNone(retry_delay("400 API key not valid. Please pass a valid API key.", 1))
        self.assertIsNone(retry_delay("这段录音超过 Live 安全时长，请改用普通 Transcribe。", 1))
        self.assertIsNone(retry_delay("Audio file was not found", 1))

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
