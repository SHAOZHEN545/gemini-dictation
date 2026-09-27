"""Command-line microphone transcription with Gemini Live Transcription."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Iterable

import av
import sounddevice as sd
from dotenv import load_dotenv
from google import genai
from google.genai import types


MODEL = "gemini-3.5-transcribe-live"
SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCKSIZE = 1_600  # 100 ms of 16-bit PCM: responsive without excessive requests.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live Chinese-English microphone transcription.")
    parser.add_argument("--vocabulary", type=Path, help="Private text file: one custom term per line.")
    parser.add_argument("--output", type=Path, help="Append finalized transcription to this UTF-8 file.")
    parser.add_argument("--device", help="Microphone device index or a matching name.")
    parser.add_argument("--file", type=Path, help="Stream a saved audio file instead of the microphone.")
    parser.add_argument("--mode", choices=("SMART", "VERBATIM"), default="SMART")
    parser.add_argument(
        "--duration",
        type=int,
        default=540,
        help="Maximum session length in seconds (1-540; default: 540).",
    )
    parser.add_argument("--list-devices", action="store_true", help="Print microphones and exit.")
    return parser.parse_args()


def read_vocabulary(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.is_file():
        raise FileNotFoundError(f"Vocabulary file was not found: {path}")
    terms = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    terms = [term for term in terms if term and not term.startswith("#")]
    # Preserve order while suppressing accidental duplicate entries.
    unique_terms = list(dict.fromkeys(terms))
    if len(unique_terms) > 1_000:
        raise ValueError("Gemini Live accepts at most 1,000 custom vocabulary terms.")
    return unique_terms


def print_input_devices(devices: Iterable[dict]) -> None:
    for index, device in enumerate(devices):
        if device["max_input_channels"]:
            print(f"[{index}] {device['name']} ({device['max_input_channels']} input channels)")


def append_final_transcript(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as destination:
        destination.write(text.strip() + "\n")


def decode_file_to_pcm(path: Path) -> bytes:
    """Decode an audio file locally to raw 16 kHz mono signed 16-bit PCM."""
    if not path.is_file():
        raise FileNotFoundError(f"Audio file was not found: {path}")
    pcm = bytearray()
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"Audio file has no audio stream: {path}")
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                pcm.extend(bytes(converted.planes[0])[: converted.samples * 2])
        for converted in resampler.resample(None):
            pcm.extend(bytes(converted.planes[0])[: converted.samples * 2])
    if not pcm:
        raise ValueError(f"Audio file contains no decoded samples: {path}")
    return bytes(pcm)


async def stream_file_audio(session: object, pcm: bytes) -> None:
    """Send 100 ms chunks at playback speed, then finalize the stream."""
    chunk_size = BLOCKSIZE * 2  # 16-bit mono samples.
    loop = asyncio.get_running_loop()
    started = loop.time()
    for offset in range(0, len(pcm), chunk_size):
        await asyncio.sleep(max(0, started + offset / (SAMPLE_RATE * 2) - loop.time()))
        await session.send_realtime_input(
            audio=types.Blob(data=pcm[offset : offset + chunk_size], mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
        )
    await session.send_realtime_input(audio_stream_end=True)


async def stream_audio(session: object, audio_queue: asyncio.Queue[bytes], device: str | None) -> None:
    """Capture raw PCM in the sounddevice thread and forward it to Gemini."""
    loop = asyncio.get_running_loop()

    def callback(indata: object, _frames: int, _time: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"\nAudio warning: {status}", file=sys.stderr)
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCKSIZE,
        device=device,
        channels=CHANNELS,
        dtype="int16",
        callback=callback,
    ):
        while True:
            audio = await audio_queue.get()
            await session.send_realtime_input(
                audio=types.Blob(data=audio, mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
            )


async def receive_transcripts(session: object, output_path: Path | None) -> None:
    """Render low-latency partial text and persist only finalized segments."""
    interim_length = 0
    async for response in session.receive():
        content = response.server_content
        if content is None:
            continue
        interim = content.interim_input_transcription
        if interim and interim.text:
            text = interim.text.strip()
            padding = max(0, interim_length - len(text))
            print("\r" + text + " " * padding, end="", flush=True)
            interim_length = len(text)
        final = content.input_transcription
        if final and final.text:
            text = final.text.strip()
            print("\r" + " " * interim_length + "\r" + text)
            interim_length = 0
            append_final_transcript(output_path, text)


async def run(args: argparse.Namespace) -> None:
    if not 1 <= args.duration <= 540:
        raise ValueError("--duration must be between 1 and 540 seconds.")
    load_dotenv()
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is missing. Create an ignored .env file from .env.example.")

    vocabulary = read_vocabulary(args.vocabulary)
    pcm = decode_file_to_pcm(args.file) if args.file else None
    if pcm is not None:
        audio_seconds = len(pcm) / (SAMPLE_RATE * 2)
        if audio_seconds + 15 > args.duration:
            raise ValueError(
                f"Audio is {audio_seconds:.1f} seconds; --duration must allow at least 15 more seconds."
            )
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=[],  # Let Gemini identify Chinese-English code switching.
            custom_vocabulary=vocabulary,
            mode=args.mode,
        ),
    )
    client = genai.Client()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
    device: str | int | None = int(args.device) if args.device and args.device.isdigit() else args.device

    print(f"Connecting to {MODEL} with {len(vocabulary)} vocabulary terms…")
    try:
        async with asyncio.timeout(args.duration):
            async with client.aio.live.connect(model=MODEL, config=config) as session:
                if pcm is not None:
                    print(f"Connected. Streaming {args.file.name} ({audio_seconds:.1f} seconds).\n")
                    receiver = asyncio.create_task(receive_transcripts(session, args.output))
                    try:
                        await stream_file_audio(session, pcm)
                        print("\nAudio sent; waiting 10 seconds for final text…")
                        await asyncio.sleep(10)
                    finally:
                        receiver.cancel()
                        with suppress(asyncio.CancelledError):
                            await receiver
                else:
                    print("Connected. Listening; press Ctrl+C to stop.\n")
                    async with asyncio.TaskGroup() as group:
                        group.create_task(stream_audio(session, audio_queue, device))
                        group.create_task(receive_transcripts(session, args.output))
    except TimeoutError:
        print(f"\nStopped after {args.duration} seconds.")


def main() -> None:
    args = parse_args()
    if args.list_devices:
        print_input_devices(sd.query_devices())
        return
    try:
        asyncio.run(run(args))
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
