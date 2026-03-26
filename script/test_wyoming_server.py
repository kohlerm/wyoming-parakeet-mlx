#!/usr/bin/env python3
"""Stream mic audio to a running Wyoming server and print streaming transcripts."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import wave
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from wyoming.asr import (  # noqa: E402
    Transcribe,
    Transcript,
    TranscriptChunk,
    TranscriptStart,
    TranscriptStop,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop  # noqa: E402
from wyoming.client import AsyncClient  # noqa: E402


def list_audio_devices() -> int:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-f",
        "avfoundation",
        "-list_devices",
        "true",
        "-i",
        "",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    print(output.strip())
    return result.returncode


def _write_wav(path: Path, pcm_bytes: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_bytes)


async def _read_server_events(client: AsyncClient, timeout: float) -> str:
    final_text = ""
    while True:
        event = await asyncio.wait_for(client.read_event(), timeout=timeout)
        if event is None:
            break

        if TranscriptStart.is_type(event.type):
            print("[stream] start")
            continue

        if TranscriptChunk.is_type(event.type):
            chunk = TranscriptChunk.from_event(event)
            print(f"{chunk.text}", end="", flush=True)
            continue

        if TranscriptStop.is_type(event.type):
            print("\n[stream] stop")
            continue

        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)
            final_text = transcript.text.strip()
            print(f"[final] {final_text or '<no speech detected>'}")
            break

    return final_text


async def transcribe_realtime_via_server(
    *,
    uri: str,
    duration: float,
    input_device: str,
    chunk_ms: int,
    timeout: float,
    keep_audio_path: Path | None,
) -> str:
    bytes_per_second = 16000 * 2
    chunk_size = max(1, int(bytes_per_second * (chunk_ms / 1000.0)))
    chunk_size -= chunk_size % 2
    if chunk_size <= 0:
        chunk_size = 3200

    captured_audio = bytearray()

    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Transcribe(language="en").event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())

        print("\nServer events")
        print("-------------")
        events_task = asyncio.create_task(_read_server_events(client, timeout=timeout))

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-i",
            input_device,
            "-t",
            str(duration),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as err:
            events_task.cancel()
            raise RuntimeError(
                "ffmpeg was not found. Install it with: brew install ffmpeg"
            ) from err

        assert process.stdout is not None
        assert process.stderr is not None

        remainder = b""
        while True:
            chunk = await process.stdout.read(chunk_size)
            if not chunk:
                break

            chunk = remainder + chunk
            consume_len = len(chunk) - (len(chunk) % 2)
            if consume_len <= 0:
                remainder = chunk
                continue

            send_chunk = chunk[:consume_len]
            remainder = chunk[consume_len:]

            await client.write_event(
                AudioChunk(rate=16000, width=2, channels=1, audio=send_chunk).event()
            )
            if keep_audio_path is not None:
                captured_audio.extend(send_chunk)

        await process.wait()
        ffmpeg_stderr = (await process.stderr.read()).decode("utf-8", errors="replace")
        if process.returncode != 0:
            events_task.cancel()
            raise RuntimeError(
                "Live recording failed. Check microphone permissions and input device. "
                f"ffmpeg: {ffmpeg_stderr.strip()}"
            )

        if remainder:
            await client.write_event(
                AudioChunk(rate=16000, width=2, channels=1, audio=remainder).event()
            )
            if keep_audio_path is not None:
                captured_audio.extend(remainder)

        await client.write_event(AudioStop().event())
        final_text = await events_task

    if keep_audio_path is not None:
        _write_wav(keep_audio_path, bytes(captured_audio))

    return final_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream mic audio to a running Wyoming ASR server"
    )
    parser.add_argument(
        "--uri",
        default="tcp://127.0.0.1:10301",
        help="Wyoming server URI (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=12.0,
        help="Streaming duration per iteration in seconds (default: 12)",
    )
    parser.add_argument(
        "--input-device",
        default=":1",
        help="ffmpeg avfoundation audio input device (default: :1)",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=120,
        help="Audio chunk size sent to Wyoming in milliseconds (default: 120)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for each server event (default: 20)",
    )
    parser.add_argument(
        "--keep-audio",
        type=Path,
        help="Optional path prefix to keep recorded WAV files",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List ffmpeg avfoundation devices and exit",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep streaming/transcribing until interrupted (Ctrl+C)",
    )
    parser.add_argument(
        "--loop-sleep",
        type=float,
        default=0.0,
        help="Seconds to wait between loop iterations (default: 0)",
    )
    return parser.parse_args()


def run_once(args: argparse.Namespace, iteration: int) -> int:
    output_path: Path | None = None
    if args.keep_audio:
        stem = args.keep_audio.stem
        suffix = args.keep_audio.suffix or ".wav"
        output_path = args.keep_audio.with_name(f"{stem}-{iteration:04d}{suffix}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nIteration {iteration}")
    print(
        f"Streaming mic audio for {args.duration:.1f}s from {args.input_device} to {args.uri}"
    )

    final_text = asyncio.run(
        transcribe_realtime_via_server(
            uri=args.uri,
            duration=args.duration,
            input_device=args.input_device,
            chunk_ms=args.chunk_ms,
            timeout=args.timeout,
            keep_audio_path=output_path,
        )
    )

    print("\nTranscript")
    print("----------")
    print(final_text or "<no speech detected>")

    if output_path is not None:
        print(f"\nSaved recording to: {output_path}")

    return 0


def main() -> int:
    args = parse_args()

    if args.list_devices:
        return list_audio_devices()

    if args.duration <= 0:
        print("--duration must be > 0", file=sys.stderr)
        return 2
    if args.chunk_ms <= 0:
        print("--chunk-ms must be > 0", file=sys.stderr)
        return 2
    if args.loop_sleep < 0:
        print("--loop-sleep must be >= 0", file=sys.stderr)
        return 2

    try:
        if not args.loop:
            return run_once(args, iteration=1)

        print("Loop mode enabled. Press Ctrl+C to stop.")
        iteration = 1
        while True:
            run_once(args, iteration=iteration)
            iteration += 1
            if args.loop_sleep > 0:
                time.sleep(args.loop_sleep)
    except TimeoutError:
        print(
            "Timed out waiting for server response. Verify the server is running and URI is correct.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
