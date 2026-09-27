"""A small Windows desktop interface for Gemini Live dictation."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from dotenv import dotenv_values, set_key
from google import genai
from google.genai import types
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .cli import BLOCKSIZE, MODEL, SAMPLE_RATE, read_vocabulary


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
VOCABULARY_PATH = PROJECT_DIR / "config" / "vocabulary.txt"
TRANSCRIPTS_DIR = PROJECT_DIR / "transcripts"
SESSION_SECONDS = 540
FINALIZATION_WAIT_SECONDS = 5


def saved_api_key() -> str:
    """Prefer this project's ignored .env, then an existing environment variable."""
    key = (dotenv_values(ENV_PATH).get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    return "" if key == "replace_with_your_own_key" else key


def configure_font(app: QApplication) -> None:
    """Load a Windows font with Chinese glyphs, including in minimal Qt setups."""
    for name in ("Noto Sans SC (TrueType).otf", "msyh.ttc", "simhei.ttf"):
        path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    app.setFont(QFont(families[0], 10))
                    return
    app.setFont(QFont("Segoe UI", 10))


class LiveWorker(QThread):
    status_changed = Signal(str)
    interim_changed = Signal(str)
    final_received = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, mode: str, device: int, vocabulary: list[str]) -> None:
        super().__init__()
        self.api_key = api_key
        self.mode = mode
        self.device = device
        self.vocabulary = vocabulary
        self.stop_requested = threading.Event()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def run(self) -> None:
        try:
            asyncio.run(self._transcribe())
        except Exception as error:  # Report worker failures in the window, not the terminal.
            self.failed.emit(str(error).replace(self.api_key, "[hidden]"))
        finally:
            self.api_key = ""

    async def _receive(self, session: object) -> None:
        async for response in session.receive():
            content = response.server_content
            if content is None:
                continue
            interim = content.interim_input_transcription
            if interim and interim.text:
                self.interim_changed.emit(interim.text.strip())
            final = content.input_transcription
            if final and final.text:
                self.interim_changed.emit("")
                self.final_received.emit(final.text.strip())

    async def _transcribe(self) -> None:
        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[],
                custom_vocabulary=self.vocabulary,
                mode=self.mode,
            ),
        )
        client = genai.Client(api_key=self.api_key)
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        overflow = threading.Event()
        loop = asyncio.get_running_loop()

        def add_audio(chunk: bytes) -> None:
            if queue.full():
                overflow.set()
            else:
                queue.put_nowait(chunk)

        def callback(indata: object, _frames: int, _time: object, status: object) -> None:
            if status:
                overflow.set()
            loop.call_soon_threadsafe(add_audio, bytes(indata))

        self.status_changed.emit("正在连接 Gemini…")
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            receiver = asyncio.create_task(self._receive(session))
            try:
                self.status_changed.emit("正在录音 · 再点一次结束")
                started = loop.time()
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCKSIZE,
                    device=self.device,
                    channels=1,
                    dtype="int16",
                    callback=callback,
                ):
                    while not self.stop_requested.is_set() and loop.time() - started < SESSION_SECONDS:
                        if overflow.is_set():
                            raise RuntimeError("麦克风音频未能及时发送，请检查设备或网络后重试。")
                        if receiver.done():
                            await receiver
                            raise RuntimeError("Gemini 连接已提前结束，请重试。")
                        try:
                            chunk = await asyncio.wait_for(queue.get(), timeout=0.15)
                        except TimeoutError:
                            continue
                        await session.send_realtime_input(
                            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
                        )

                self.status_changed.emit("正在完成转写…")
                await session.send_realtime_input(audio_stream_end=True)
                await asyncio.sleep(FINALIZATION_WAIT_SECONDS)
            finally:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver


class DictationWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.worker: LiveWorker | None = None
        self.elapsed_seconds = 0
        self.close_when_finished = False
        self.had_error = False
        self.session_path: Path | None = None
        self.setWindowTitle("Gemini Dictation")
        self.setMinimumSize(780, 820)
        self.resize(900, 860)
        self._build_ui()
        self._load_microphones()
        self._refresh_key_status()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #0d1524; color: #e9f0f7; font-size: 14px; }
            QWidget#choices { background: transparent; }
            QLabel { background: transparent; }
            QFrame#card { background: #172338; border: 1px solid #263954;
                          border-radius: 14px; }
            QLabel#eyebrow { color: #65d6c4; font-size: 12px; font-weight: 700; }
            QLabel#title { font-size: 28px; font-weight: 700; }
            QLabel#muted { color: #9fb0c5; }
            QLabel#section { font-size: 16px; font-weight: 700; }
            QLineEdit, QComboBox, QPlainTextEdit {
                background: #0e192b; border: 1px solid #35506c; border-radius: 8px;
                padding: 9px; selection-background-color: #2c857e;
            }
            QComboBox { min-height: 24px; }
            QComboBox QAbstractItemView { background: #172338; color: #e9f0f7; }
            QPushButton { background: #294362; border: 0; border-radius: 9px;
                          padding: 10px 16px; font-weight: 650; }
            QPushButton:hover { background: #345679; }
            QPushButton:disabled { background: #263346; color: #75869c; }
            QPushButton#record { background: #35bba6; color: #09211e;
                                 font-size: 16px; padding: 14px 20px; }
            QPushButton#record:hover { background: #58d5c1; }
            QPushButton#record[recording="true"] { background: #ed7777; color: #2b1010; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 24)
        root.setSpacing(16)

        eyebrow = QLabel("PERSONAL TRANSCRIPTION STUDIO")
        eyebrow.setObjectName("eyebrow")
        root.addWidget(eyebrow)
        title = QLabel("Gemini Dictation")
        title.setObjectName("title")
        root.addWidget(title)
        intro = QLabel("点一下开始说话，再点一下结束；最终文字可以直接复制。")
        intro.setObjectName("muted")
        root.addWidget(intro)

        settings = QFrame()
        settings.setObjectName("card")
        settings.setFixedHeight(285)
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(20, 16, 20, 18)
        settings_layout.setSpacing(12)
        section = QLabel("基本设置")
        section.setObjectName("section")
        settings_layout.addWidget(section)

        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("粘贴 API key，点击保存（仅保存在本机）")
        self.save_key_button = QPushButton("保存 Key")
        self.save_key_button.clicked.connect(self._save_key)
        key_row.addWidget(self.key_input, 1)
        key_row.addWidget(self.save_key_button)
        settings_layout.addLayout(key_row)
        self.key_status = QLabel()
        self.key_status.setObjectName("muted")
        settings_layout.addWidget(self.key_status)

        choices_container = QWidget()
        choices_container.setObjectName("choices")
        choices_container.setFixedHeight(100)
        choices = QHBoxLayout(choices_container)
        choices.setContentsMargins(0, 0, 0, 0)
        mode_column = QVBoxLayout()
        mode_label = QLabel("转写模式")
        mode_label.setObjectName("muted")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("VERBATIM · 保留原话和纠正", "VERBATIM")
        self.mode_combo.addItem("SMART · 自动整理语句", "SMART")
        mode_column.addWidget(mode_label)
        mode_column.addWidget(self.mode_combo)
        choices.addLayout(mode_column, 1)

        mic_column = QVBoxLayout()
        mic_label = QLabel("麦克风")
        mic_label.setObjectName("muted")
        self.mic_combo = QComboBox()
        mic_column.addWidget(mic_label)
        mic_column.addWidget(self.mic_combo)
        choices.addLayout(mic_column, 1)
        settings_layout.addWidget(choices_container)
        self.vocabulary_status = QLabel()
        self.vocabulary_status.setObjectName("muted")
        settings_layout.addWidget(self.vocabulary_status)
        root.addWidget(settings)
        root.addSpacing(16)

        action_row = QHBoxLayout()
        self.record_button = QPushButton("开始录音")
        self.record_button.setObjectName("record")
        self.record_button.clicked.connect(self._toggle_recording)
        self.record_button.setMinimumWidth(180)
        action_row.addWidget(self.record_button)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("muted")
        action_row.addWidget(self.status_label, 1)
        self.timer_label = QLabel("00:00")
        action_row.addWidget(self.timer_label)
        root.addLayout(action_row)

        transcript_card = QFrame()
        transcript_card.setObjectName("card")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(20, 16, 20, 18)
        transcript_layout.setSpacing(10)
        transcript_header = QHBoxLayout()
        heading = QLabel("转写结果")
        heading.setObjectName("section")
        transcript_header.addWidget(heading)
        transcript_header.addStretch()
        self.copy_button = QPushButton("复制全文")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_text)
        transcript_header.addWidget(self.copy_button)
        transcript_layout.addLayout(transcript_header)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("开始录音后，最终转写会出现在这里。")
        transcript_layout.addWidget(self.transcript, 1)
        self.interim_label = QLabel(" ")
        self.interim_label.setObjectName("muted")
        self.interim_label.setWordWrap(True)
        transcript_layout.addWidget(self.interim_label)
        root.addWidget(transcript_card, 1)

        footer = QLabel("录音时音频会发送给 Gemini；API key、词表和本地转写文件不会提交到 Git。")
        footer.setObjectName("muted")
        root.addWidget(footer)

    def _load_microphones(self) -> None:
        try:
            default_index = sd.default.device[0]
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    self.mic_combo.addItem(f"{device['name']}  [{index}]", index)
                    if index == default_index:
                        self.mic_combo.setCurrentIndex(self.mic_combo.count() - 1)
        except Exception as error:
            self.status_label.setText(f"无法列出麦克风：{error}")
        if not self.mic_combo.count():
            self.record_button.setEnabled(False)

    def _refresh_key_status(self) -> None:
        self.key_status.setText("API key 已保存在本机" if saved_api_key() else "尚未设置 API key")
        try:
            count = len(read_vocabulary(VOCABULARY_PATH)) if VOCABULARY_PATH.is_file() else 0
            self.vocabulary_status.setText(f"专业词表：{count} 项（本机文件，不会提交）")
        except Exception as error:
            self.vocabulary_status.setText(f"词表需要检查：{error}")

    def _save_key(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.information(self, "API key", "请先输入要保存的 API key。")
            return
        try:
            set_key(str(ENV_PATH), "GEMINI_API_KEY", key, quote_mode="auto")
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self.key_input.clear()
        self._refresh_key_status()
        self.status_label.setText("API key 已保存")

    def _toggle_recording(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.record_button.setEnabled(False)
            self.status_label.setText("正在结束录音…")
            return
        key = saved_api_key()
        if not key:
            QMessageBox.information(self, "需要 API key", "请先填写并保存 API key。")
            self.key_input.setFocus()
            return
        try:
            vocabulary = read_vocabulary(VOCABULARY_PATH) if VOCABULARY_PATH.is_file() else []
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "词表错误", str(error))
            return
        device = self.mic_combo.currentData()
        if device is None:
            QMessageBox.critical(self, "麦克风", "没有找到可用的麦克风。")
            return

        self.transcript.clear()
        self.interim_label.setText(" ")
        self.copy_button.setEnabled(False)
        self.had_error = False
        self.elapsed_seconds = 0
        self.timer_label.setText("00:00")
        self.session_path = TRANSCRIPTS_DIR / f"session-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
        self.worker = LiveWorker(key, self.mode_combo.currentData(), device, vocabulary)
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.interim_changed.connect(self._show_interim)
        self.worker.final_received.connect(self._append_final)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._worker_finished)
        self.record_button.setText("结束录音")
        self.record_button.setProperty("recording", True)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.mode_combo.setEnabled(False)
        self.mic_combo.setEnabled(False)
        self.timer.start()
        self.worker.start()

    def _tick(self) -> None:
        self.elapsed_seconds += 1
        minutes, seconds = divmod(self.elapsed_seconds, 60)
        self.timer_label.setText(f"{minutes:02d}:{seconds:02d}")

    def _show_interim(self, text: str) -> None:
        self.interim_label.setText(text or " ")

    def _append_final(self, text: str) -> None:
        if text:
            current = self.transcript.toPlainText().strip()
            self.transcript.setPlainText(f"{current}\n{text}" if current else text)
            self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
            self.copy_button.setEnabled(True)

    def _show_error(self, message: str) -> None:
        self.had_error = True
        self.status_label.setText("转写遇到问题")
        QMessageBox.critical(self, "转写失败", message)

    def _worker_finished(self) -> None:
        self.timer.stop()
        self.record_button.setEnabled(True)
        self.record_button.setText("开始录音")
        self.record_button.setProperty("recording", False)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.mode_combo.setEnabled(True)
        self.mic_combo.setEnabled(True)
        self.interim_label.setText(" ")
        text = self.transcript.toPlainText().strip()
        if text and self.session_path:
            try:
                self.session_path.parent.mkdir(parents=True, exist_ok=True)
                self.session_path.write_text(text + "\n", encoding="utf-8")
                self.status_label.setText(
                    "连接中断，已保存收到的文字" if self.had_error else "转写完成，文字已保存在本机"
                )
            except OSError as error:
                self.status_label.setText(f"转写完成，但保存失败：{error}")
        elif not self.had_error:
            self.status_label.setText("录音已结束，没有收到最终文字")
        self.worker = None
        if self.close_when_finished:
            self.close()

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.transcript.toPlainText())
        self.status_label.setText("已复制到剪贴板")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and self.worker.isRunning():
            self.close_when_finished = True
            self.worker.request_stop()
            self.record_button.setEnabled(False)
            self.status_label.setText("正在结束录音，请稍候…")
            event.ignore()
        else:
            event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    configure_font(app)
    window = DictationWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
