"""A Windows desktop interface for record-then-transcribe dictation."""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys
import time
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
from PySide6.QtCore import QObject, QRectF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .cli import BLOCKSIZE, MODEL as LIVE_MODEL, SAMPLE_RATE, decode_file_to_pcm, read_vocabulary


PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "app-icon.ico"
ENV_PATH = PROJECT_DIR / ".env"
VOCABULARY_PATH = PROJECT_DIR / "config" / "vocabulary.txt"
TRANSCRIPTS_DIR = PROJECT_DIR / "transcripts"
RECORDINGS_DIR = PROJECT_DIR / "recordings"
BATCH_MODEL = "gemini-3.5-transcribe"
LIVE_RECORDING_SECONDS = 510  # 8m30s leaves room within the Live model's 10-minute session.
BATCH_RECORDING_SECONDS = 1200
LIVE_FINALIZATION_SECONDS = 30
# Whole-recording replay: one manual activity keeps the full context in a single final transcript.
# Live transcribes at roughly 4x playback speed and closes the connection once about 80-90 s of
# unprocessed audio piles up (16x failed after 113 s of audio, 8x after 179 s). So send a short
# head start, then pace at the processing speed: the backlog stays small and nothing is slower.
LIVE_SEND_SPEED = 4
LIVE_HEAD_START_SECONDS = 15
LIVE_PROCESSING_SPEED = 4
# Each finished recording is its own job, so the next recording can start right away. Live's
# session quota is undocumented: bursts of 10 simultaneous sessions on this key had 5-8 accepted and
# the rest refused at once with "1011 ... exceeded your current quota", which clears after a wait.
# A job takes about a quarter of its recording length, so three parallel jobs is ample for
# back-to-back dictation; a refused job waits and retries without blocking the others.
MAX_PARALLEL_JOBS = 3
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = (10, 30, 60)
QUOTA_ERROR = re.compile(r"\b(429|409)\b|resource.?exhausted|quota|rate.?limit", re.IGNORECASE)
TRANSIENT_ERROR = re.compile(
    r"\b(1006|1011|1013|500|502|503|504)\b|unavailable|overloaded|deadline|timed? ?out|connection|连接|没有返回",
    re.IGNORECASE,
)
FATAL_ERROR = re.compile(r"api.?key|permission|\b(400|401|403)\b|安全时长", re.IGNORECASE)


def clock_text(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining:02d}"


def join_live_transcripts(parts: list[str]) -> str:
    """Join Live final chunks without treating chunk boundaries as paragraphs."""
    result = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if result and part[0].isascii() and part[0].isalnum() and result[-1].isascii() and (
            result[-1].isalnum() or result[-1] in ".!?,:;"
        ):
            result += " "
        result += part
    return result


def retry_delay(message: str, attempts: int) -> int | None:
    """Seconds to wait before retrying a failed job, or None when retrying cannot help."""
    if attempts >= MAX_ATTEMPTS or FATAL_ERROR.search(message):
        return None
    if not (QUOTA_ERROR.search(message) or TRANSIENT_ERROR.search(message)):
        return None
    suggested = re.search(r"retry (?:in|after) (\d+(?:\.\d+)?)\s*s|retryDelay\W+(\d+)s", message, re.IGNORECASE)
    if suggested:
        return math.ceil(float(suggested.group(1) or suggested.group(2))) + 1
    return RETRY_BACKOFF_SECONDS[min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1]


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

    def _close_stream(self) -> None:
        if self.stream is None:
            raise RuntimeError("录音尚未开始。")
        try:
            self.stream.stop()
        finally:
            self.stream.close()
            self.stream = None

    def discard(self) -> None:
        """Stop without writing a file, so nothing from this take is kept or sent."""
        self._close_stream()
        self.samples.clear()
        self.level = 0

    def finish(self) -> Path:
        self._close_stream()
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
            self.status_changed.emit("上传中…")
            audio_file = client.files.upload(file=str(self.audio_path))
            self.status_changed.emit("转写中…")
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
        except Exception as error:
            self.failed.emit(str(error).replace(self.api_key, "[hidden]"))
        finally:
            self.api_key = ""


class LiveReplayWorker(QThread):
    """Replay a finished local recording to Live without showing interim text."""

    status_changed = Signal(str)
    text_ready = Signal(str)
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
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
            ),
        )
        client = genai.Client(api_key=self.api_key)
        self.status_changed.emit("连接中…")
        final_parts: list[str] = []
        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            async def receive() -> None:
                async for response in session.receive():
                    content = response.server_content
                    if content and content.input_transcription and content.input_transcription.text:
                        final_parts.append(content.input_transcription.text.strip())
                    if content and content.generation_complete:
                        return

            receiver = asyncio.create_task(receive())
            try:
                estimate = math.ceil(audio_seconds / LIVE_PROCESSING_SPEED) + 5
                await session.send_realtime_input(activity_start=types.ActivityStart())
                chunk_size = BLOCKSIZE * 2
                loop = asyncio.get_running_loop()
                started = loop.time()
                reported = -5
                for offset in range(0, len(pcm), chunk_size):
                    percent = offset * 100 // len(pcm)
                    if percent >= reported + 5:
                        reported = percent
                        remaining = max(0, round(estimate - (loop.time() - started)))
                        self.status_changed.emit(f"发送中 {percent}% · 约剩 {clock_text(remaining)}")
                    if receiver.done():
                        await receiver
                        raise RuntimeError("Live 连接在录音发送完之前关闭。")
                    audio_position = offset / (SAMPLE_RATE * 2)
                    send_at = started + max(0, audio_position - LIVE_HEAD_START_SECONDS) / LIVE_SEND_SPEED
                    await asyncio.sleep(max(0, send_at - loop.time()))
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm[offset : offset + chunk_size], mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
                    )
                await session.send_realtime_input(activity_end=types.ActivityEnd())
                self.status_changed.emit("录音已送完，等待定稿…")
                try:
                    await asyncio.wait_for(
                        asyncio.shield(receiver),
                        timeout=LIVE_FINALIZATION_SECONDS + audio_seconds / LIVE_PROCESSING_SPEED * 2,
                    )
                except TimeoutError:
                    pass  # Keep any final text that arrived; an empty result is reported below.
            finally:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
        text = join_live_transcripts(final_parts)
        if not text:
            raise RuntimeError("Live 没有返回最终文字；录音仍保存在本机。")
        self.text_ready.emit(text)


class TranscriptionJob(QObject):
    """One finished recording on its way through transcription; lives in the GUI thread."""

    changed = Signal()
    settled = Signal(object)  # Emitted with the job after each worker run ends.

    def __init__(
        self, number: int, audio_path: Path, engine: str, mode: str, vocabulary: list[str], seconds: int
    ) -> None:
        super().__init__()
        self.number = number
        self.audio_path = audio_path
        self.engine = engine
        self.mode = mode
        self.vocabulary = vocabulary
        self.seconds = seconds
        self.created = datetime.now()
        self.transcript_path = TRANSCRIPTS_DIR / f"session-{self.created:%Y%m%d-%H%M%S-%f}.txt"
        self.state = "queued"  # queued -> running -> done | retry | failed
        self.progress = ""
        self.text = ""
        self.error = ""
        self.attempts = 0
        self.retry_at = 0.0
        self.retry_reason = ""
        self.worker:BatchTranscribeWorker | LiveReplayWorker | None = None

    def start(self, api_key: str) -> None:
        worker_class = LiveReplayWorker if self.engine == "LIVE" else BatchTranscribeWorker
        self.worker = worker_class(api_key, self.mode, self.audio_path, self.vocabulary)
        # Bound methods of this QObject make Qt queue the worker's signals to the GUI thread.
        self.worker.status_changed.connect(self._set_progress)
        self.worker.text_ready.connect(self._set_text)
        self.worker.failed.connect(self._set_error)
        self.worker.finished.connect(self._worker_finished)
        self.attempts += 1
        self.state = "running"
        self.progress = "准备中…"
        self.error = ""
        self.worker.start()
        self.changed.emit()

    def _set_progress(self, text: str) -> None:
        self.progress = text
        self.changed.emit()

    def _set_text(self, text: str) -> None:
        self.text = text

    def _set_error(self, message: str) -> None:
        self.error = message

    def _worker_finished(self) -> None:
        if self.worker is not None:
            self.worker.wait()  # finished fires just before the thread exits.
            self.worker = None
        self.settled.emit(self)


def vocabulary_sort_key(term: str) -> tuple[bool, str]:
    """Latin terms A-Z first (case-insensitive), then Chinese and other scripts."""
    return (not term[:1].isascii(), term.casefold())


class VocabularyDialog(QDialog):
    """One sorted term per row; typing filters the list and flags duplicates."""

    def __init__(self, parent: QWidget | None, path: Path) -> None:
        super().__init__(parent)
        self.path = path
        self.original_text = path.read_text(encoding="utf-8") if path.is_file() else None
        lines = self.original_text.splitlines() if self.original_text is not None else []
        self.comments = [line for line in lines if line.lstrip().startswith("#")]
        terms = (line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
        self.terms = sorted(dict.fromkeys(terms), key=vocabulary_sort_key)

        self.setWindowTitle("个人词库 · 查看与编辑")
        self.resize(460, 640)
        layout = QVBoxLayout(self)
        hint = QLabel("按首字母排列。输入时即时筛选并提示重复；双击词条可修改，选中后按 Delete 删除。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        entry_row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("输入新词条，回车添加")
        self.entry.textChanged.connect(self._filter)
        self.entry.returnPressed.connect(self._add)
        entry_row.addWidget(self.entry, 1)
        self.add_button = QPushButton("添加")
        self.add_button.clicked.connect(self._add)
        entry_row.addWidget(self.add_button)
        layout.addLayout(entry_row)
        self.feedback = QLabel()
        self.feedback.setObjectName("muted")
        layout.addWidget(self.feedback)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        self.list.itemDoubleClicked.connect(self._edit)
        delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._delete_selected)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        delete = QPushButton("删除所选")
        delete.clicked.connect(self._delete_selected)
        buttons.addWidget(delete)
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存词库")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._render()

    def _existing(self, term: str, ignore: str | None = None) -> str | None:
        """Return the stored spelling if the term is already present, ignoring case."""
        folded = term.casefold()
        return next((t for t in self.terms if t.casefold() == folded and t != ignore), None)

    def _render(self, select: str | None = None) -> None:
        self.terms.sort(key=vocabulary_sort_key)
        self.list.clear()
        self.list.addItems(self.terms)
        self._filter(self.entry.text())
        if select in self.terms:
            item = self.list.item(self.terms.index(select))
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        matches = 0
        for row, term in enumerate(self.terms):
            hidden = bool(query) and query not in term.casefold()
            self.list.item(row).setHidden(hidden)
            matches += not hidden
        existing = self._existing(text.strip()) if query else None
        self.add_button.setEnabled(bool(query) and existing is None)
        self.feedback.setStyleSheet("color: #f0b86e;" if existing is not None else "")
        if existing is not None:
            self.feedback.setText(f"已在个人词库中：{existing}")
            self.list.setCurrentRow(self.terms.index(existing))
        elif query:
            self.feedback.setText(f"没有重复 · 有 {matches} 项包含这段文字")
        else:
            self.feedback.setText(f"共 {len(self.terms)} 项")

    def _add(self) -> None:
        term = self.entry.text().strip()
        if not term or self._existing(term) is not None:
            return
        self.entry.clear()  # Clear first: this re-filters the list before the new term exists.
        self.terms.append(term)
        self._render(select=term)
        self.feedback.setText(f"已添加：{term} · 共 {len(self.terms)} 项")

    def _edit(self, item: QListWidgetItem) -> None:
        old = item.text()
        new, ok = QInputDialog.getText(self, "修改词条", "词条：", text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        existing = self._existing(new, ignore=old)
        if existing is not None:
            QMessageBox.information(self, "重复词条", f"个人词库中已经有：{existing}")
            return
        self.terms[self.terms.index(old)] = new
        self._render(select=new)

    def _delete_selected(self) -> None:
        selected = {item.text() for item in self.list.selectedItems() if not item.isHidden()}
        if not selected:
            return
        self.terms = [term for term in self.terms if term not in selected]
        self._render()

    def _save(self) -> None:
        if len(self.terms) > 1000:
            QMessageBox.warning(self, "词库太长", "Gemini 最多接受 1000 个不同的词条。")
            return
        try:
            current_text = self.path.read_text(encoding="utf-8") if self.path.is_file() else None
            if current_text != self.original_text:
                QMessageBox.warning(self, "词库已更改", "词库文件已被其他设备或程序修改。请关闭并重新打开词库后再编辑。")
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.comments + self.terms) + "\n", encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self.accept()


class DictationWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        saved_path = QSettings("Gemini Dictation", "Gemini Dictation").value("vocabulary_path", "")
        self.vocabulary_path = Path(saved_path) if saved_path else VOCABULARY_PATH
        self.recorder: LocalRecorder | None = None
        self.jobs: list[TranscriptionJob] = []
        self.job_items: dict[int, QListWidgetItem] = {}
        self.next_job_number = 1  # Numbers stay unique after rows are removed from the list.
        self.cooldown_until = 0.0  # After a quota refusal, queued jobs wait instead of piling on.
        self.microphones: list[Microphone] = []
        self.elapsed_seconds = 0
        self.close_when_finished = False
        self.key_verified = False
        self.setWindowTitle("Gemini Dictation")
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
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
        self.queue_timer = QTimer(self)  # Starts due retries and refreshes their countdowns.
        self.queue_timer.setInterval(1000)
        self.queue_timer.timeout.connect(self._dispatch_jobs)
        self.queue_timer.start()

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
            QListWidget { background: #0e192b; alternate-background-color: #122036;
                          border: 1px solid #35506c; border-radius: 8px; padding: 4px; }
            QListWidget::item { padding: 6px 8px; border-radius: 5px; }
            QListWidget::item:selected { background: #27566a; color: #e9f0f7; }
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
            QPushButton#discard { background: transparent; border: 1px solid #8a4a55; color: #f2a0a0; }
            QPushButton#discard:hover { background: #3a1f28; }
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
        intro = QLabel("先在本机录音，结束后才发送；每段在后台转写，录完一段就可以接着录下一段。")
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
        self.vocabulary_button = QPushButton("查看 / 编辑词库")
        self.vocabulary_button.clicked.connect(self._show_vocabulary)
        vocab_row.addWidget(self.vocabulary_button)
        choose_vocabulary = QPushButton("选择词库文件")
        choose_vocabulary.clicked.connect(self._choose_vocabulary)
        vocab_row.addWidget(choose_vocabulary)
        settings_layout.addLayout(vocab_row)
        root.addWidget(settings)
        root.addSpacing(8)

        action_row = QHBoxLayout()
        self.record_button = QPushButton("开始录音")
        self.record_button.setObjectName("record")
        self.record_button.clicked.connect(self._toggle_recording)
        self.record_button.setMinimumWidth(180)
        action_row.addWidget(self.record_button)
        self.next_button = QPushButton("提交，录下一段")
        self.next_button.setToolTip("提交当前这段去转写，并立刻开始录下一段")
        self.next_button.clicked.connect(self._submit_and_record_next)
        self.next_button.setVisible(False)
        action_row.addWidget(self.next_button)
        self.discard_button = QPushButton("× 放弃本段")
        self.discard_button.setObjectName("discard")
        self.discard_button.setToolTip("扔掉正在录的这段：不保存录音，也不发送给 Gemini")
        self.discard_button.clicked.connect(self._discard_recording)
        self.discard_button.setVisible(False)
        action_row.addWidget(self.discard_button)
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
        self.level_status.setFixedWidth(
            max(self.level_status.fontMetrics().horizontalAdvance(message) for message in (
                "录音时显示", "正在拾音", "声音偏小 / 安静", "录音已结束"
            )) + 8
        )
        self.level_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        self.queue_label = QLabel()
        self.queue_label.setObjectName("muted")
        transcript_header.addWidget(self.queue_label)
        transcript_header.addStretch()
        self.retry_button = QPushButton("重试")
        self.retry_button.clicked.connect(self._retry_selected)
        transcript_header.addWidget(self.retry_button)
        self.copy_button = QPushButton("复制本段")
        self.copy_button.clicked.connect(self._copy_text)
        transcript_header.addWidget(self.copy_button)
        self.copy_all_button = QPushButton("复制全部")
        self.copy_all_button.setToolTip("按录音顺序复制所有已完成的段落")
        self.copy_all_button.clicked.connect(self._copy_all)
        transcript_header.addWidget(self.copy_all_button)
        transcript_layout.addLayout(transcript_header)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.job_list = QListWidget()
        self.job_list.setFixedWidth(290)
        self.job_list.setMinimumHeight(80)
        self.job_list.setWordWrap(True)
        self.job_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.job_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.job_list.currentItemChanged.connect(self._show_selected_job)
        self.job_list.itemSelectionChanged.connect(self._update_job_buttons)
        remove_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.job_list)
        remove_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove_shortcut.activated.connect(self._remove_selected)
        list_column = QVBoxLayout()
        list_column.setSpacing(8)
        list_column.addWidget(self.job_list, 1)
        list_buttons = QHBoxLayout()
        list_buttons.setSpacing(8)
        self.remove_button = QPushButton("移除所选")
        self.remove_button.setToolTip("只从列表中移除（也可按 Delete，可多选）；录音和转写文件仍保存在本机")
        self.remove_button.clicked.connect(self._remove_selected)
        list_buttons.addWidget(self.remove_button)
        self.clear_done_button = QPushButton("清除已完成")
        self.clear_done_button.setToolTip("从列表中移除所有已完成的段落；文件仍保存在本机")
        self.clear_done_button.clicked.connect(self._clear_done)
        list_buttons.addWidget(self.clear_done_button)
        list_column.addLayout(list_buttons)
        body.addLayout(list_column)
        self.transcript = QPlainTextEdit()
        self.transcript.setMinimumHeight(120)
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("结束录音并完成整段转写后，文字会出现在这里。")
        self.transcript.textChanged.connect(self._transcript_edited)
        body.addWidget(self.transcript, 1)
        transcript_layout.addLayout(body, 1)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("muted")
        # One line, clipped if long: a word-wrapped label would size the whole card by its
        # preferred height and push the record button over the settings card.
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        transcript_layout.addWidget(self.detail_label)
        root.addWidget(transcript_card, 1)

        footer = QLabel("结束录音后，音频和个人词库中的词条会发送给 Gemini；API key、录音和转写文件不会提交到 Git。")
        footer.setObjectName("muted")
        root.addWidget(footer)
        self._update_limit_hint()
        self._refresh_jobs()
        self._show_selected_job()

    def _recording_limit(self) -> int:
        return LIVE_RECORDING_SECONDS if self.engine_combo.currentData() == "LIVE" else BATCH_RECORDING_SECONDS

    def _update_limit_hint(self) -> None:
        parallel = f"最多同时转写 {MAX_PARALLEL_JOBS} 段，其余排队，限流时自动重试"
        if self.engine_combo.currentData() == "LIVE":
            self.limit_label.setText(f"Live 每段上限 08:30（会话限 10 分钟），到时自动提交；{parallel}。")
        else:
            self.limit_label.setText(f"普通 Transcribe 每段最多 20 分钟，受每分钟 / 每天请求额度限制；{parallel}。")
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
            if self.vocabulary_path != VOCABULARY_PATH and not self.vocabulary_path.is_file():
                raise FileNotFoundError(f"找不到词库文件：{self.vocabulary_path}")
            count = len(read_vocabulary(self.vocabulary_path)) if self.vocabulary_path.is_file() else 0
            self.vocabulary_status.setText(f"个人词库：{count} 项 · {self.vocabulary_path}")
        except Exception as error:
            self.vocabulary_status.setText(f"个人词库需要检查：{error}")

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
        self._dispatch_jobs()

    def _show_vocabulary(self) -> None:
        try:
            dialog = VocabularyDialog(self, self.vocabulary_path)
        except OSError as error:
            QMessageBox.critical(self, "无法读取词库", str(error))
            return
        if dialog.exec():
            self._refresh_key_status()
            self.status_label.setText("个人词库已更新，下次录音生效")

    def _choose_vocabulary(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择 Google Drive 桌面版同步的词库文本文件", str(self.vocabulary_path.parent), "文本文件 (*.txt);;所有文件 (*)"
        )
        if selected:
            self.vocabulary_path = Path(selected)
            QSettings("Gemini Dictation", "Gemini Dictation").setValue("vocabulary_path", selected)
            self._refresh_key_status()

    def _toggle_recording(self) -> None:
        if self.recorder is not None:
            self._stop_recording()
        else:
            self._start_recording()

    def _submit_and_record_next(self) -> None:
        if self.recorder is not None and self._stop_recording():
            self._start_recording()

    def _discard_recording(self) -> None:
        if self.recorder is None:
            return
        self.timer.stop()
        self.level_timer.stop()
        recorder = self.recorder
        self.recorder = None
        with suppress(Exception):
            recorder.discard()
        self._reset_controls()
        self.level_meter.clear()
        self.timer_label.setText(f"00:00 / {clock_text(self._recording_limit())}")
        self.status_label.setText("已放弃这段录音 · 没有保存，也没有发送")

    def _start_recording(self) -> None:
        if not saved_api_key():
            QMessageBox.information(self, "需要 API key", "请先填写并保存 API key。")
            self.key_input.setFocus()
            return
        try:
            if self.vocabulary_path != VOCABULARY_PATH and not self.vocabulary_path.is_file():
                raise FileNotFoundError(f"找不到词库文件：{self.vocabulary_path}")
            vocabulary = read_vocabulary(self.vocabulary_path) if self.vocabulary_path.is_file() else []
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "个人词库错误", str(error))
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
        self._session_vocabulary = vocabulary
        self._session_mode = self.mode_combo.currentData()
        self._session_engine = self.engine_combo.currentData()
        self.elapsed_seconds = 0
        self.timer_label.setText(f"00:00 / {clock_text(self._recording_limit())}")
        self.record_button.setText("结束并提交")
        self.record_button.setProperty("recording", True)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.next_button.setVisible(True)
        self.discard_button.setVisible(True)
        self.mode_combo.setEnabled(False)
        self.engine_combo.setEnabled(False)
        self.mic_combo.setEnabled(False)
        self.vocabulary_button.setEnabled(False)
        self.status_label.setText(f"正在录第 {self.next_job_number} 段 · 仅在本机，尚未发送")
        self.timer.start()
        self.level_meter.clear()
        self.level_status.setText("正在拾音")
        self.level_timer.start()

    def _stop_recording(self) -> bool:
        """Queue the finished recording for transcription; the next one can start at once."""
        self.timer.stop()
        self.level_timer.stop()
        recorder = self.recorder
        self.recorder = None
        assert recorder is not None
        try:
            audio_path = recorder.finish()
        except Exception as error:
            self._reset_controls()
            QMessageBox.critical(self, "录音失败", str(error))
            return False
        job = TranscriptionJob(
            self.next_job_number, audio_path, self._session_engine, self._session_mode,
            self._session_vocabulary, self.elapsed_seconds,
        )
        self.next_job_number += 1
        job.changed.connect(self._refresh_jobs)
        job.settled.connect(self._job_settled)
        self.jobs.append(job)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, job.number)
        self.job_list.insertItem(0, item)
        self.job_items[job.number] = item
        if self._selected_job() is None:
            self.job_list.setCurrentItem(item)
        self._reset_controls()
        self.level_status.setText("录音已结束")
        self.status_label.setText(f"第 {job.number} 段已提交转写 · 可以直接录下一段")
        self._dispatch_jobs()
        return True

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

    def _active_jobs(self) -> list[TranscriptionJob]:
        return [job for job in self.jobs if job.state in ("queued", "running", "retry")]

    def _dispatch_jobs(self) -> None:
        """Start waiting jobs in recording order, up to the parallel limit."""
        now = time.monotonic()
        running = sum(job.state == "running" for job in self.jobs)
        waiting = [
            job for job in self.jobs
            if job.state == "queued" or (job.state == "retry" and now >= job.retry_at)
        ]
        if waiting and running < MAX_PARALLEL_JOBS and now >= self.cooldown_until:
            key = saved_api_key()
            if key:
                for job in waiting[: MAX_PARALLEL_JOBS - running]:
                    job.start(key)
            else:
                self.status_label.setText("API key 未设置；保存 Key 后开始转写排队的录音")
        self._refresh_jobs()

    def _job_settled(self, job: TranscriptionJob) -> None:
        if job.text:
            selected = self._selected_job()
            follow = selected is None or selected.state != "done" or selected is self._latest_done_job()
            job.state = "done"
            job.error = ""
            if self._save_transcript(job):
                # The text is safely on disk, so the recording has served its purpose.
                with suppress(OSError):
                    job.audio_path.unlink(missing_ok=True)
                self.status_label.setText(f"第 {job.number} 段转写完成，已保存在本机")
            else:
                self.status_label.setText(f"第 {job.number} 段转写完成，但保存失败")
            self._mark_key_verified()
            # Follow new results unless the user went back to an older one or is editing.
            if follow and not self.transcript.hasFocus():
                self.job_list.setCurrentItem(self._job_item(job))
        else:
            job.error = job.error or "没有收到文字。"
            delay = retry_delay(job.error, job.attempts)
            if delay is None:
                job.state = "failed"
                self.status_label.setText(f"第 {job.number} 段转写失败；录音仍保存在本机，可点“重试”")
            else:
                job.state = "retry"
                job.retry_at = time.monotonic() + delay
                if QUOTA_ERROR.search(job.error):
                    self.cooldown_until = max(self.cooldown_until, job.retry_at)
                    job.retry_reason = "额度限流"
                else:
                    job.retry_reason = "连接中断"
                self.status_label.setText(f"第 {job.number} 段遇到{job.retry_reason}，{delay} 秒后自动重试")
        self._dispatch_jobs()
        if self._selected_job() is job:
            self._show_selected_job()
        if self.close_when_finished and not self._active_jobs():
            self.close()

    def _save_transcript(self, job: TranscriptionJob) -> bool:
        try:
            job.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            job.transcript_path.write_text(job.text.strip() + "\n", encoding="utf-8")
            return True
        except OSError as error:
            job.error = f"保存失败：{error}"
            return False

    def _latest_done_job(self) -> TranscriptionJob | None:
        return next((job for job in reversed(self.jobs) if job.state == "done"), None)

    def _job_item(self, job: TranscriptionJob) -> QListWidgetItem:
        return self.job_items[job.number]

    def _item_job(self, item: QListWidgetItem) -> TranscriptionJob:
        number = item.data(Qt.ItemDataRole.UserRole)
        return next(job for job in self.jobs if job.number == number)

    def _selected_job(self) -> TranscriptionJob | None:
        item = self.job_list.currentItem()
        return self._item_job(item) if item else None

    def _job_row_text(self, job: TranscriptionJob, now: float) -> tuple[str, str]:
        header = f"第 {job.number} 段 · {job.created:%H:%M:%S} · 录音 {clock_text(job.seconds)}"
        if job.state == "done":
            preview = " ".join(job.text.split())
            return f"{header}\n✓ {preview[:15]}{'…' if len(preview) > 15 else ''}", "#9fe3d5"
        if job.state == "running":
            return f"{header}\n{job.progress}", "#e9f0f7"
        if job.state == "retry":
            wait = clock_text(max(0, math.ceil(job.retry_at - now)))
            return f"{header}\n{job.retry_reason} · {wait} 后第 {job.attempts + 1} 次尝试", "#f0b86e"
        if job.state == "failed":
            return f"{header}\n× 转写失败 · 可点“重试”", "#ed7777"
        if self.cooldown_until > now:
            return f"{header}\n排队中 · 限流冷却 {clock_text(math.ceil(self.cooldown_until - now))}", "#f0b86e"
        return f"{header}\n排队中 · 等待空位", "#9fb0c5"

    def _refresh_jobs(self) -> None:
        now = time.monotonic()
        for job in self.jobs:
            item = self._job_item(job)
            text, color = self._job_row_text(job, now)
            if item.text() != text:
                item.setText(text)
                item.setForeground(QColor(color))
                item.setToolTip(job.error)
        counts = {state: sum(job.state == state for job in self.jobs) for state in ("running", "done")}
        waiting = sum(job.state in ("queued", "retry") for job in self.jobs)
        summary = f"转写中 {counts['running']} · 排队 {waiting} · 完成 {counts['done']}"
        if self.cooldown_until > now and waiting:
            summary += f" · 限流冷却 {clock_text(math.ceil(self.cooldown_until - now))}"
        self.queue_label.setText(summary if self.jobs else "")
        self._update_job_buttons()

    def _update_job_buttons(self) -> None:
        job = self._selected_job()
        self.copy_button.setEnabled(job is not None and job.state == "done" and bool(job.text.strip()))
        self.copy_all_button.setEnabled(any(job.state == "done" for job in self.jobs))
        self.retry_button.setVisible(job is not None and job.state in ("retry", "failed"))
        self.retry_button.setText("立即重试" if job is not None and job.state == "retry" else "重试")
        selected = [self._item_job(item) for item in self.job_list.selectedItems()]
        self.remove_button.setEnabled(any(job.state in ("done", "failed") for job in selected))
        self.clear_done_button.setEnabled(any(job.state == "done" for job in self.jobs))

    def _show_selected_job(self) -> None:
        job = self._selected_job()
        # Read-only while swapping text, so textChanged does not write it into a job.
        self.transcript.setReadOnly(True)
        self.transcript.setPlainText(job.text if job and job.state == "done" else "")
        self.transcript.setReadOnly(job is None or job.state != "done")
        if job is None:
            self.transcript.setPlaceholderText("结束录音并完成整段转写后，文字会出现在这里。")
            self.detail_label.setText(
                "录完一段就提交，不必等待转写，可以马上录下一段；每段独立转写，完成后在左侧列表中查看。"
            )
        elif job.state == "done":
            saved = job.error or f"已保存在本机：transcripts/{job.transcript_path.name}"
            self.detail_label.setText(f"第 {job.number} 段 · {saved}。可以直接修改后复制。")
        elif job.state == "failed":
            self.transcript.setPlaceholderText(
                f"这一段没有转写成功，可以点“重试”。\n\n失败原因：{job.error}"
            )
            self.detail_label.setText(f"录音仍保存在本机：recordings/{job.audio_path.name}")
        else:
            last_error = f"\n\n上次尝试：{job.error}" if job.error else ""
            self.transcript.setPlaceholderText(
                f"这一段正在后台转写，完成后会显示在这里。你可以继续录下一段。{last_error}"
            )
            self.detail_label.setText(f"录音暂存在本机，转写完成后自动删除：recordings/{job.audio_path.name}")
        self.detail_label.setToolTip(self.detail_label.text())
        self._update_job_buttons()

    def _transcript_edited(self) -> None:
        job = self._selected_job()
        if job is not None and job.state == "done" and not self.transcript.isReadOnly():
            job.text = self.transcript.toPlainText()
            self._refresh_jobs()

    def _retry_selected(self) -> None:
        job = self._selected_job()
        if job is None or job.state not in ("retry", "failed"):
            return
        if job.state == "failed":
            job.attempts = 0  # A manual retry gets a fresh set of automatic retries.
        job.state = "queued"
        self.cooldown_until = 0.0
        self._dispatch_jobs()
        self._show_selected_job()

    def _remove_selected(self) -> None:
        """Drop finished or failed rows from the list; the files on disk are kept."""
        selected = [self._item_job(item) for item in self.job_list.selectedItems()]
        removable = [job for job in selected if job.state in ("done", "failed")]
        if not removable:
            return
        self._remove_jobs(removable)
        message = f"已从列表移除 {len(removable)} 段 · 录音和转写文件仍在本机"
        if len(selected) > len(removable):
            message += f"；{len(selected) - len(removable)} 段还在转写，未移除"
        self.status_label.setText(message)

    def _clear_done(self) -> None:
        done = [job for job in self.jobs if job.state == "done"]
        if done:
            self._remove_jobs(done)
            self.status_label.setText(f"已清除 {len(done)} 段已完成的记录 · 转写文件仍在 transcripts/")

    def _remove_jobs(self, jobs: list[TranscriptionJob]) -> None:
        self.job_list.blockSignals(True)  # Rows and jobs are briefly out of step while removing.
        try:
            for job in jobs:
                if job.state == "done":
                    self._save_transcript(job)  # Keep edits made in the window.
                self.jobs.remove(job)
                self.job_list.takeItem(self.job_list.row(self.job_items.pop(job.number)))
        finally:
            self.job_list.blockSignals(False)
        self._show_selected_job()
        self._refresh_jobs()

    def _mark_key_verified(self) -> None:
        self.key_verified = True
        self._refresh_key_status()

    def _reset_controls(self) -> None:
        self.record_button.setEnabled(bool(self.microphones) and not self.close_when_finished)
        self.record_button.setText("开始录音")
        self.record_button.setProperty("recording", False)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.next_button.setVisible(False)
        self.discard_button.setVisible(False)
        self.mode_combo.setEnabled(True)
        self.engine_combo.setEnabled(True)
        self.mic_combo.setEnabled(True)
        self.vocabulary_button.setEnabled(True)
        self.level_status.setText("录音时显示")

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.transcript.toPlainText())
        self.status_label.setText("已复制本段到剪贴板")

    def _copy_all(self) -> None:
        done = [job.text.strip() for job in self.jobs if job.state == "done" and job.text.strip()]
        QApplication.clipboard().setText("\n\n".join(done))
        self.status_label.setText(f"已按录音顺序复制 {len(done)} 段到剪贴板")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.recorder is not None:
            try:
                self.recorder.finish()  # Keep the unfinished recording on disk, but do not send it.
            except Exception:
                pass
            self.recorder = None
            self.timer.stop()
            self.level_timer.stop()
            self._reset_controls()
        active = self._active_jobs()
        if active:
            self.close_when_finished = True
            self.record_button.setEnabled(False)
            self.status_label.setText(f"还有 {len(active)} 段在转写，完成后自动关闭窗口…")
            event.ignore()
        else:
            event.accept()

def main() -> None:
    if sys.platform == "win32":
        # Give the taskbar its own identity instead of grouping with other Python apps.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GeminiDictation.Desktop")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    configure_font(app)
    window = DictationWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
