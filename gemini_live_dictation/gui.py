"""A Windows desktop interface for record-then-transcribe dictation."""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys
import wave
from array import array
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from dotenv import dotenv_values, set_key
from google import genai
from google.genai import types
from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QFontDatabase, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .cli import BLOCKSIZE, MODEL as LIVE_MODEL, SAMPLE_RATE, decode_file_to_pcm, read_vocabulary


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
VOCABULARY_PATH = PROJECT_DIR / "config" / "vocabulary.txt"
TRANSCRIPTS_DIR = PROJECT_DIR / "transcripts"
RECORDINGS_DIR = PROJECT_DIR / "recordings"
BATCH_MODEL = "gemini-3.5-transcribe"
LIVE_RECORDING_SECONDS = 510  # 8m30s leaves room within the Live model's 10-minute session.
BATCH_RECORDING_SECONDS = 1200
LIVE_FINALIZATION_SECONDS = 10


def clock_text(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining:02d}"


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


@dataclass(frozen=True)
class Microphone:
    index: int
    name: str
    sample_rate: int
    host_api: str


def microphone_options() -> list[Microphone]:
    """Show one usable entry per microphone, preferring modern Windows drivers."""
    host_apis = sd.query_hostapis()
    preferred: dict[str, Microphone] = {}
    priority = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2, "Windows WDM-KS": 3}
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        raw_name = str(device["name"])
        if any(
            term in raw_name.lower()
            for term in ("sound mapper", "primary sound capture", "stereo mix", "line in", "声音映射", "主声音捕获")
        ):
            continue
        sample_rate = round(device["default_samplerate"])
        try:
            sd.check_input_settings(device=index, samplerate=sample_rate, channels=1, dtype="int16")
            with sd.RawInputStream(device=index, samplerate=sample_rate, channels=1, dtype="int16"):
                pass
        except (sd.PortAudioError, ValueError):
            continue
        if "@System32" in raw_name:
            match = re.search(r";\(([^)]+)\)", raw_name)
            clean_name = f"Headset ({match.group(1)})" if match else "Bluetooth headset"
        else:
            clean_name = raw_name.strip()
        api_name = host_apis[device["hostapi"]]["name"]
        option = Microphone(index, clean_name, sample_rate, api_name)
        existing = preferred.get(clean_name.casefold())
        if existing is None or priority.get(api_name, 9) < priority.get(existing.host_api, 9):
            preferred[clean_name.casefold()] = option
    return sorted(preferred.values(), key=lambda item: ("headset" in item.name.lower(), item.name.casefold()))


class LocalRecorder:
    """Capture locally; no API call is made until finish() returns a WAV file."""

    def __init__(self, microphone: Microphone, path: Path) -> None:
        self.microphone = microphone
        self.path = path
        self.samples = bytearray()
        self.stream: sd.RawInputStream | None = None
        self.warning = ""
        self.level = 0

    def start(self) -> None:
        def callback(indata: object, _frames: int, _time: object, status: object) -> None:
            if status:
                self.warning = str(status)
            chunk = bytes(indata)
            self.samples.extend(chunk)
            samples = array("h")
            samples.frombytes(chunk)
            if samples:
                rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
                dbfs = 20 * math.log10(max(rms / 32768, 1e-6))
                self.level = max(0, min(100, round((dbfs + 55) * 100 / 47)))

        self.stream = sd.RawInputStream(
            samplerate=self.microphone.sample_rate,
            device=self.microphone.index,
            channels=1,
            dtype="int16",
            callback=callback,
        )
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            self.stream = None
            raise

    def finish(self) -> Path:
        if self.stream is None:
            raise RuntimeError("录音尚未开始。")
        try:
            self.stream.stop()
        finally:
            self.stream.close()
            self.stream = None
        if not self.samples:
            raise ValueError("没有录到声音，请检查麦克风选择。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self.path), "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(self.microphone.sample_rate)
            recording.writeframes(self.samples)
        self.samples.clear()
        self.level = 0
        return self.path


class AudioLevelMeter(QWidget):
    """A local-only rolling microphone level display."""

    def __init__(self) -> None:
        super().__init__()
        self.levels = [0] * 66
        self.setMinimumHeight(44)

    def push_level(self, level: int) -> None:
        self.levels = self.levels[1:] + [max(0, min(100, level))]
        self.update()

    def clear(self) -> None:
        self.levels = [0] * len(self.levels)
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        step = width / len(self.levels)
        bar_width = max(2.0, min(5.0, step * 0.55))
        for index, level in enumerate(self.levels):
            bar_height = max(3.0, (height - 5) * level / 100)
            painter.setBrush(QColor("#51d6c4") if level > 3 else QColor("#486078"))
            painter.setPen(Qt.PenStyle.NoPen)
            rect = QRectF(index * step + (step - bar_width) / 2, (height - bar_height) / 2, bar_width, bar_height)
            painter.drawRoundedRect(rect, bar_width / 2, bar_width / 2)


class BatchTranscribeWorker(QThread):
    status_changed = Signal(str)
    text_ready = Signal(str)
    request_succeeded = Signal()
    failed = Signal(str)

    def __init__(self, api_key: str, mode: str, audio_path: Path, vocabulary: list[str]) -> None:
        super().__init__()
        self.api_key = api_key
        self.mode = mode
        self.audio_path = audio_path
        self.vocabulary = vocabulary

    def run(self) -> None:
        try:
            client = genai.Client(api_key=self.api_key)
            self.status_changed.emit("录音已结束，正在上传整段音频…")
            audio_file = client.files.upload(file=str(self.audio_path))
            self.status_changed.emit("正在转写整段录音…")
            mode = "smart" if self.mode == "SMART" else {"type": "verbatim"}
            interaction = client.interactions.create(
                model=BATCH_MODEL,
                input=[
                    {
                        "type": "audio",
                        "uri": audio_file.uri,
                        "mime_type": audio_file.mime_type,
                    }
                ],
                generation_config={
                    "transcription_config": {
                        "mode": mode,
                        "custom_vocabulary": self.vocabulary,
                        "language_codes": [],
                    }
                },
            )
            text = (interaction.output_text or "").strip()
            if not text:
                raise RuntimeError("Gemini 没有返回文字，请检查录音后重试。")
            self.text_ready.emit(text)
            self.request_succeeded.emit()
        except Exception as error:
            self.failed.emit(str(error).replace(self.api_key, "[hidden]"))
        finally:
            self.api_key = ""


class LiveReplayWorker(QThread):
    """Replay a finished local recording to Live without showing interim text."""

    status_changed = Signal(str)
    text_ready = Signal(str)
    request_succeeded = Signal()
    failed = Signal(str)

    def __init__(self, api_key: str, mode: str, audio_path: Path, vocabulary: list[str]) -> None:
        super().__init__()
        self.api_key = api_key
        self.mode = mode
        self.audio_path = audio_path
        self.vocabulary = vocabulary

    def run(self) -> None:
        try:
            asyncio.run(self._transcribe())
        except Exception as error:
            self.failed.emit(str(error).replace(self.api_key, "[hidden]"))
        finally:
            self.api_key = ""

    async def _transcribe(self) -> None:
        pcm = decode_file_to_pcm(self.audio_path)
        audio_seconds = len(pcm) / (SAMPLE_RATE * 2)
        if audio_seconds > LIVE_RECORDING_SECONDS + 2:
            raise ValueError("这段录音超过 Live 安全时长，请改用普通 Transcribe。")
        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[], custom_vocabulary=self.vocabulary, mode=self.mode
            ),
        )
        client = genai.Client(api_key=self.api_key)
        self.status_changed.emit("正在连接 Gemini Transcribe Live…")
        final_parts: list[str] = []
        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            async def receive() -> None:
                async for response in session.receive():
                    content = response.server_content
                    if content and content.input_transcription and content.input_transcription.text:
                        final_parts.append(content.input_transcription.text.strip())

            receiver = asyncio.create_task(receive())
            try:
                self.status_changed.emit(f"正在用 Live 转写录音 · 约需 {clock_text(math.ceil(audio_seconds))}")
                chunk_size = BLOCKSIZE * 2
                loop = asyncio.get_running_loop()
                started = loop.time()
                for offset in range(0, len(pcm), chunk_size):
                    if receiver.done():
                        await receiver
                        raise RuntimeError("Live 连接在录音发送完之前关闭。")
                    await asyncio.sleep(max(0, started + offset / (SAMPLE_RATE * 2) - loop.time()))
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm[offset : offset + chunk_size], mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
                    )
                await session.send_realtime_input(audio_stream_end=True)
                self.status_changed.emit("录音已送完，正在收取最终转写…")
                try:
                    await asyncio.wait_for(asyncio.shield(receiver), timeout=LIVE_FINALIZATION_SECONDS)
                except TimeoutError:
                    pass  # The Live connection usually stays open after the final text arrives.
            finally:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
        text = "\n".join(part for part in final_parts if part).strip()
        if not text:
            raise RuntimeError("Live 没有返回最终文字；录音仍保存在本机。")
        self.text_ready.emit(text)
        self.request_succeeded.emit()


class DictationWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.worker: BatchTranscribeWorker | LiveReplayWorker | None = None
        self.recorder: LocalRecorder | None = None
        self.microphones: list[Microphone] = []
        self.elapsed_seconds = 0
        self.close_when_finished = False
        self.had_error = False
        self.key_verified = False
        self.session_path: Path | None = None
        self.setWindowTitle("Gemini Dictation")
        self.setMinimumSize(880, 980)
        self.resize(1000, 1020)
        self._build_ui()
        self._load_microphones()
        self._refresh_key_status()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.level_timer = QTimer(self)
        self.level_timer.setInterval(75)
        self.level_timer.timeout.connect(self._update_level)

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
            QComboBox QAbstractItemView { background: #172338; color: #e9f0f7;
                                          selection-background-color: #27566a;
                                          border: 1px solid #35506c; padding: 4px; }
            QScrollBar:vertical { background: #142237; width: 9px; margin: 2px; border: 0; }
            QScrollBar::handle:vertical { background: #4a728a; min-height: 28px;
                                          border-radius: 4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
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
        intro = QLabel("先在本机录音，结束后才发送；Live 转写完成前不显示文字。")
        intro.setObjectName("muted")
        root.addWidget(intro)

        settings = QFrame()
        settings.setObjectName("card")
        settings.setFixedHeight(375)
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(20, 16, 20, 18)
        settings_layout.setSpacing(8)
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
        choices_container.setFixedHeight(82)
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

        engine_column = QVBoxLayout()
        engine_label = QLabel("转写引擎")
        engine_label.setObjectName("muted")
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Transcribe Live · 推荐", "LIVE")
        self.engine_combo.addItem("普通 Transcribe · 整段", "BATCH")
        self.engine_combo.currentIndexChanged.connect(self._update_limit_hint)
        engine_column.addWidget(engine_label)
        engine_column.addWidget(self.engine_combo)
        choices.addLayout(engine_column, 1)

        mic_column = QVBoxLayout()
        mic_label = QLabel("麦克风")
        mic_label.setObjectName("muted")
        self.mic_combo = QComboBox()
        self.mic_combo.setView(QListView())
        self.mic_combo.setMaxVisibleItems(6)
        mic_column.addWidget(mic_label)
        mic_column.addWidget(self.mic_combo)
        choices.addLayout(mic_column, 1)
        settings_layout.addWidget(choices_container)
        self.limit_label = QLabel()
        self.limit_label.setObjectName("muted")
        settings_layout.addWidget(self.limit_label)
        self.mic_status = QLabel()
        self.mic_status.setObjectName("muted")
        settings_layout.addWidget(self.mic_status)
        vocab_row = QHBoxLayout()
        self.vocabulary_status = QLabel()
        self.vocabulary_status.setObjectName("muted")
        vocab_row.addWidget(self.vocabulary_status, 1)
        self.vocabulary_button = QPushButton("查看 / 编辑词表")
        self.vocabulary_button.clicked.connect(self._show_vocabulary)
        vocab_row.addWidget(self.vocabulary_button)
        settings_layout.addLayout(vocab_row)
        root.addWidget(settings)
        root.addSpacing(8)

        action_row = QHBoxLayout()
        self.record_button = QPushButton("开始录音")
        self.record_button.setObjectName("record")
        self.record_button.clicked.connect(self._toggle_recording)
        self.record_button.setMinimumWidth(180)
        action_row.addWidget(self.record_button)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("muted")
        action_row.addWidget(self.status_label, 1)
        self.timer_label = QLabel("00:00 / 08:30")
        action_row.addWidget(self.timer_label)
        root.addLayout(action_row)

        meter_row = QHBoxLayout()
        level_label = QLabel("麦克风声量")
        level_label.setObjectName("muted")
        meter_row.addWidget(level_label)
        self.level_meter = AudioLevelMeter()
        meter_row.addWidget(self.level_meter, 1)
        self.level_status = QLabel("录音时显示")
        self.level_status.setObjectName("muted")
        meter_row.addWidget(self.level_status)
        root.addLayout(meter_row)

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
        self.transcript.setPlaceholderText("结束录音并完成整段转写后，文字会出现在这里。")
        transcript_layout.addWidget(self.transcript, 1)
        self.detail_label = QLabel("默认使用 Transcribe Live：录音时不上传，结束后按播放速度发送录音。")
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        transcript_layout.addWidget(self.detail_label)
        root.addWidget(transcript_card, 1)

        footer = QLabel("结束录音后，音频和词表会发送给 Gemini；API key、录音和转写文件不会提交到 Git。")
        footer.setObjectName("muted")
        root.addWidget(footer)
        self._update_limit_hint()

    def _recording_limit(self) -> int:
        return LIVE_RECORDING_SECONDS if self.engine_combo.currentData() == "LIVE" else BATCH_RECORDING_SECONDS

    def _update_limit_hint(self) -> None:
        if self.engine_combo.currentData() == "LIVE":
            self.limit_label.setText("Live 单次会话上限 10 分钟；请在 08:30 内结束，届时会自动停止并提交。")
        else:
            self.limit_label.setText("普通 Transcribe 可一次处理整段录音；本程序最多录 20 分钟，会消耗其请求额度。")
        if self.recorder is None:
            self.timer_label.setText(f"00:00 / {clock_text(self._recording_limit())}")

    def _load_microphones(self) -> None:
        try:
            self.microphones = microphone_options()
            default_name = sd.query_devices(sd.default.device[0])["name"]
            for option in self.microphones:
                self.mic_combo.addItem(option.name, option.index)
                self.mic_combo.setItemData(
                    self.mic_combo.count() - 1,
                    f"{option.host_api} · {option.sample_rate} Hz · 录音时再次确认设备可打开",
                    Qt.ItemDataRole.ToolTipRole,
                )
                if option.name in default_name:
                    self.mic_combo.setCurrentIndex(self.mic_combo.count() - 1)
            self.mic_status.setText(f"已验证 {len(self.microphones)} 个当前可打开的麦克风。")
        except Exception as error:
            self.mic_status.setText(f"无法检查麦克风：{error}")
        if not self.mic_combo.count():
            self.record_button.setEnabled(False)

    def _refresh_key_status(self) -> None:
        if saved_api_key():
            state = "本次转写已成功验证" if self.key_verified else "已从本机读取；尚未向 Google 验证"
            self.key_status.setText(f"API key：{state}")
        else:
            self.key_status.setText("API key：尚未设置")
        try:
            count = len(read_vocabulary(VOCABULARY_PATH)) if VOCABULARY_PATH.is_file() else 0
            self.vocabulary_status.setText(f"专业词表：{count} 项（本机文件，不会提交到 Git）")
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
        self.key_verified = False
        self._refresh_key_status()
        self.status_label.setText("API key 已保存到本机，等待首次转写验证")

    def _show_vocabulary(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("专业词表 · 查看与编辑")
        dialog.resize(560, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("每行一个词或短语；以 # 开头的行是注释。保存后，下次录音会使用新词表。"))
        editor = QPlainTextEdit()
        editor.setPlainText(VOCABULARY_PATH.read_text(encoding="utf-8") if VOCABULARY_PATH.is_file() else "")
        layout.addWidget(editor, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存词表")
        buttons.addWidget(save)
        layout.addLayout(buttons)

        def save_vocabulary() -> None:
            content = editor.toPlainText()
            terms = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
            if len(set(terms)) > 1000:
                QMessageBox.warning(dialog, "词表太长", "Gemini 最多接受 1000 个不同的词条。")
                return
            try:
                VOCABULARY_PATH.parent.mkdir(parents=True, exist_ok=True)
                VOCABULARY_PATH.write_text(content.rstrip() + "\n", encoding="utf-8")
            except OSError as error:
                QMessageBox.critical(dialog, "保存失败", str(error))
                return
            dialog.accept()

        save.clicked.connect(save_vocabulary)
        if dialog.exec():
            self._refresh_key_status()
            self.status_label.setText("专业词表已更新，下次录音生效")

    def _toggle_recording(self) -> None:
        if self.recorder is not None:
            self._stop_recording()
            return
        if self.worker and self.worker.isRunning():
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
        selected_index = self.mic_combo.currentData()
        microphone = next((item for item in self.microphones if item.index == selected_index), None)
        if microphone is None:
            QMessageBox.critical(self, "麦克风", "没有找到可用的麦克风。")
            return
        recording_path = RECORDINGS_DIR / f"recording-{datetime.now():%Y%m%d-%H%M%S-%f}.wav"
        recorder = LocalRecorder(microphone, recording_path)
        try:
            recorder.start()
        except Exception as error:
            QMessageBox.critical(self, "麦克风无法打开", f"这个设备目前不能录音：{error}")
            return
        self.recorder = recorder
        self._session_key = key
        self._session_vocabulary = vocabulary
        self._session_mode = self.mode_combo.currentData()
        self._session_engine = self.engine_combo.currentData()
        self.transcript.clear()
        self.transcript.setReadOnly(True)
        self.copy_button.setEnabled(False)
        self.had_error = False
        self.elapsed_seconds = 0
        self.timer_label.setText(f"00:00 / {clock_text(self._recording_limit())}")
        self.session_path = TRANSCRIPTS_DIR / f"session-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
        self.record_button.setText("结束录音")
        self.record_button.setProperty("recording", True)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.mode_combo.setEnabled(False)
        self.engine_combo.setEnabled(False)
        self.mic_combo.setEnabled(False)
        self.vocabulary_button.setEnabled(False)
        self.status_label.setText("正在本地录音 · 尚未发送给 Gemini")
        self.detail_label.setText("录音正在本机缓存。点击“结束录音”后，才开始向 Gemini 发送音频。")
        self.timer.start()
        self.level_meter.clear()
        self.level_status.setText("正在拾音")
        self.level_timer.start()

    def _stop_recording(self) -> None:
        self.timer.stop()
        self.level_timer.stop()
        self.level_status.setText("录音已结束")
        self.record_button.setEnabled(False)
        recorder = self.recorder
        self.recorder = None
        assert recorder is not None
        try:
            audio_path = recorder.finish()
        except Exception as error:
            self._reset_controls()
            QMessageBox.critical(self, "录音失败", str(error))
            return
        if self._session_engine == "LIVE":
            self.detail_label.setText(f"录音已保存在本机：{audio_path.name}。正在按播放速度发送给 Live；完成后显示全文。")
            worker_class = LiveReplayWorker
        else:
            self.detail_label.setText(f"录音已保存在本机：{audio_path.name}。正在提交给普通 Transcribe。")
            worker_class = BatchTranscribeWorker
        self.worker = worker_class(
            self._session_key, self._session_mode, audio_path, self._session_vocabulary
        )
        self._session_key = ""
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.text_ready.connect(self._show_result)
        self.worker.request_succeeded.connect(self._mark_key_verified)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _tick(self) -> None:
        self.elapsed_seconds += 1
        limit = self._recording_limit()
        self.timer_label.setText(f"{clock_text(self.elapsed_seconds)} / {clock_text(limit)}")
        if limit - self.elapsed_seconds == 30:
            self.status_label.setText("还剩 30 秒，届时会自动结束录音并提交")
        if self.elapsed_seconds >= limit and self.recorder is not None:
            self._stop_recording()

    def _update_level(self) -> None:
        if self.recorder is not None:
            self.level_meter.push_level(self.recorder.level)
            self.level_status.setText("正在拾音" if self.recorder.level > 5 else "声音偏小 / 安静")

    def _show_result(self, text: str) -> None:
        if text:
            self.transcript.setPlainText(text)
            self.copy_button.setEnabled(True)

    def _mark_key_verified(self) -> None:
        self.key_verified = True
        self._refresh_key_status()

    def _show_error(self, message: str) -> None:
        self.had_error = True
        self.status_label.setText("转写失败；录音仍保存在本机")
        QMessageBox.critical(self, "转写失败", f"{message}\n\n录音保存在本机，可以稍后重试。")

    def _reset_controls(self) -> None:
        self.record_button.setEnabled(bool(self.microphones))
        self.record_button.setText("开始录音")
        self.record_button.setProperty("recording", False)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.mode_combo.setEnabled(True)
        self.engine_combo.setEnabled(True)
        self.mic_combo.setEnabled(True)
        self.vocabulary_button.setEnabled(True)
        self.transcript.setReadOnly(False)
        self.level_status.setText("录音时显示")

    def _worker_finished(self) -> None:
        self._reset_controls()
        text = self.transcript.toPlainText().strip()
        if text and self.session_path:
            try:
                self.session_path.parent.mkdir(parents=True, exist_ok=True)
                self.session_path.write_text(text + "\n", encoding="utf-8")
                self.status_label.setText("转写完成，文字已保存在本机")
            except OSError as error:
                self.status_label.setText(f"转写完成，但保存失败：{error}")
        elif not self.had_error:
            self.status_label.setText("录音已结束，但没有收到文字；录音仍保存在本机")
        self.worker = None
        if self.close_when_finished:
            self.close()

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.transcript.toPlainText())
        self.status_label.setText("已复制到剪贴板")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.recorder is not None:
            try:
                self.recorder.finish()
            except Exception:
                pass
            self.recorder = None
            self.timer.stop()
            self.level_timer.stop()
            event.accept()
            return
        if self.worker and self.worker.isRunning():
            self.close_when_finished = True
            self.record_button.setEnabled(False)
            self.status_label.setText("整段转写进行中，请稍候…")
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
