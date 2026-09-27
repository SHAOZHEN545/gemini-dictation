"""Offline check of the recording stop and finalization path."""

from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from gemini_live_dictation.gui import LiveWorker


class FakeSession:
    def __init__(self) -> None:
        self.sent_audio = 0
        self.stream_ended = asyncio.Event()

    async def send_realtime_input(self, *, audio=None, audio_stream_end=False) -> None:
        if audio is not None:
            self.sent_audio += 1
        if audio_stream_end:
            self.stream_ended.set()

    async def receive(self):
        await self.stream_ended.wait()
        yield SimpleNamespace(
            server_content=SimpleNamespace(
                interim_input_transcription=None,
                input_transcription=SimpleNamespace(text="测试 QUBIG"),
            )
        )
        await asyncio.Event().wait()


class FakeConnection:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


class FakeInputStream:
    def __init__(self, *, callback, **_kwargs) -> None:
        self.callback = callback

    def __enter__(self):
        self.callback(bytes(3200), 1600, None, None)
        return self

    def __exit__(self, *_args) -> None:
        return None


class GuiWorkerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    async def test_stop_sends_audio_end_and_collects_final_text(self) -> None:
        session = FakeSession()
        client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **_: FakeConnection(session))))
        worker = LiveWorker("test-key", "VERBATIM", 1, ["QUBIG"])
        finals: list[str] = []
        worker.final_received.connect(finals.append)
        timer = threading.Timer(0.2, worker.request_stop)
        with (
            patch("gemini_live_dictation.gui.genai.Client", return_value=client),
            patch("gemini_live_dictation.gui.sd.RawInputStream", FakeInputStream),
            patch("gemini_live_dictation.gui.FINALIZATION_WAIT_SECONDS", 0.1),
        ):
            timer.start()
            try:
                await worker._transcribe()
            finally:
                timer.join()
        self.assertGreater(session.sent_audio, 0)
        self.assertTrue(session.stream_ended.is_set())
        self.assertEqual(finals, ["测试 QUBIG"])


if __name__ == "__main__":
    unittest.main()
