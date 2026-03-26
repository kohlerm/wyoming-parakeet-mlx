#!/usr/bin/env python3
"""Validate Wyoming streaming server with audio/text test pairs."""

from __future__ import annotations

import argparse
import asyncio
import string
import subprocess
import sys
import time
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

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac")


def normalize_text(text: str) -> list[str]:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return text.split()


def compute_wer(reference: str, hypothesis: str) -> float:
    ref_words = normalize_text(reference)
    hyp_words = normalize_text(hypothesis)

    if not ref_words:
        return 1.0 if hyp_words else 0.0

    n = len(ref_words)
    m = len(hyp_words)
    dp = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref_words[i - 1] == hyp_words[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )

    return dp[n][m] / max(1, n)


def discover_cases(test_dir: Path) -> list[tuple[str, Path, Path]]:
    cases = []
    for txt_path in sorted(test_dir.glob("*.txt")):
        if txt_path.name.lower() == "readme.txt":
            continue

        audio_path = None
        for ext in AUDIO_EXTENSIONS:
            candidate = txt_path.with_suffix(ext)
            if candidate.exists():
                audio_path = candidate
                break

        if audio_path is None:
            continue

        cases.append((txt_path.stem, audio_path, txt_path))

    return cases


def audio_to_pcm16(audio_path: Path, sample_rate: int = 16000) -> bytes:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())

    return result.stdout


async def wait_for_server(uri: str, timeout_seconds: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            async with AsyncClient.from_uri(uri):
                return True
        except Exception:
            await asyncio.sleep(0.5)
    return False


async def run_one_case(
    *,
    uri: str,
    audio_bytes: bytes,
    chunk_ms: int,
    timeout: float,
    language: str,
    realtime: bool,
) -> tuple[str, int, int, int]:
    chunk_size = int(16000 * 2 * (chunk_ms / 1000.0))
    chunk_size = max(2, chunk_size - (chunk_size % 2))

    starts = 0
    chunks = 0
    stops = 0
    final_text = ""

    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Transcribe(language=language).event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())

        async def read_events() -> str:
            nonlocal starts, chunks, stops
            while True:
                event = await asyncio.wait_for(client.read_event(), timeout=timeout)
                if event is None:
                    return ""

                if TranscriptStart.is_type(event.type):
                    starts += 1
                    continue
                if TranscriptChunk.is_type(event.type):
                    chunks += 1
                    continue
                if TranscriptStop.is_type(event.type):
                    stops += 1
                    continue
                if Transcript.is_type(event.type):
                    return Transcript.from_event(event).text.strip()

        reader_task = asyncio.create_task(read_events())

        for offset in range(0, len(audio_bytes), chunk_size):
            payload = audio_bytes[offset : offset + chunk_size]
            await client.write_event(
                AudioChunk(rate=16000, width=2, channels=1, audio=payload).event()
            )
            if realtime:
                await asyncio.sleep(len(payload) / (16000 * 2))

        await client.write_event(AudioStop().event())
        final_text = await reader_task

    return final_text, starts, chunks, stops


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test a running Wyoming streaming server with expected transcripts"
    )
    parser.add_argument(
        "--uri",
        default="tcp://127.0.0.1:10301",
        help="Wyoming server URI (default: %(default)s)",
    )
    parser.add_argument(
        "--test-dir",
        type=Path,
        default=Path("~/parakeet-mlx/kyutai-mlx/python/test_data").expanduser(),
        help="Directory with <name>.txt and <name>.(wav|mp3|m4a|flac)",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=120,
        help="Audio chunk size sent to server in ms (default: 120)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="Maximum WER to pass a case (default: 0.3)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for server events (default: 30)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language sent in Transcribe event (default: en)",
    )
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        help="Send file as fast as possible instead of realtime pacing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.chunk_ms <= 0:
        print("--chunk-ms must be > 0", file=sys.stderr)
        return 2
    if args.threshold < 0:
        print("--threshold must be >= 0", file=sys.stderr)
        return 2
    if not args.test_dir.exists():
        print(f"Test directory not found: {args.test_dir}", file=sys.stderr)
        return 2

    cases = discover_cases(args.test_dir)
    if not cases:
        print(f"No test cases found in {args.test_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(cases)} case(s) in {args.test_dir}")
    print(f"Target server: {args.uri}")

    if not asyncio.run(wait_for_server(args.uri)):
        print("Server did not become reachable in time", file=sys.stderr)
        return 1

    failed = 0
    for name, audio_path, txt_path in cases:
        expected = txt_path.read_text(encoding="utf-8").strip()
        try:
            audio_bytes = audio_to_pcm16(audio_path)
        except Exception as err:
            failed += 1
            print(f"\n{name}: FAIL")
            print(f"  audio conversion error: {err}")
            continue

        try:
            final_text, starts, chunks, stops = asyncio.run(
                run_one_case(
                    uri=args.uri,
                    audio_bytes=audio_bytes,
                    chunk_ms=args.chunk_ms,
                    timeout=args.timeout,
                    language=args.language,
                    realtime=not args.no_realtime,
                )
            )
        except Exception as err:
            failed += 1
            print(f"\n{name}: FAIL")
            print(f"  server/streaming error: {err}")
            continue

        case_wer = compute_wer(expected, final_text)
        streaming_ok = starts >= 1 and chunks >= 1 and stops >= 1
        pass_wer = case_wer <= args.threshold
        passed = streaming_ok and pass_wer
        status = "PASS" if passed else "FAIL"

        print(f"\n{name}: {status}")
        print(f"  events: start={starts} chunk={chunks} stop={stops}")
        print(f"  WER: {case_wer:.1%} (threshold {args.threshold:.1%})")
        print(f"  expected: {expected}")
        print(f"  final:    {final_text}")

        if not passed:
            failed += 1

    print("\nDone")
    print(f"Failures: {failed}/{len(cases)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
