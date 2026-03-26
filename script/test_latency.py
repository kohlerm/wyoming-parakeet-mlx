#!/usr/bin/env python3
"""Latency benchmark for Wyoming Parakeet MLX server.

Measures timing of streaming and non-streaming transcription paths:
  - Time-to-first-token (TTFT): first AudioChunk → TranscriptStart
  - First chunk latency: first AudioChunk → first TranscriptChunk
  - Chunk-to-chunk latency: intervals between TranscriptChunk events
  - Final latency: AudioStop → Transcript
  - End-to-end latency: first AudioChunk → Transcript
  - Audio duration vs wall-clock ratios (RTF)

Usage:
    # Run against a streaming server
    python script/test_latency.py --uri tcp://127.0.0.1:10301 --test-dir ~/parakeet-mlx/kyutai-mlx/python/test_data --runs 3

    # Compare: restart server without --streaming, run again
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from wyoming.asr import Transcribe, Transcript, TranscriptChunk, TranscriptStart, TranscriptStop
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RunTimings:
    """Timestamps collected during a single transcription run."""
    audio_file: str = ""
    audio_duration_s: float = 0.0

    # Client-side timestamps (monotonic)
    t_first_audio_sent: float = 0.0
    t_audio_stop_sent: float = 0.0

    # Server-side event receipt timestamps
    t_transcript_start: float = 0.0
    t_transcript_chunks: list[float] = field(default_factory=list)
    t_transcript_stop: float = 0.0
    t_transcript_final: float = 0.0

    # Content
    chunk_texts: list[str] = field(default_factory=list)
    final_text: str = ""

    @property
    def is_streaming(self) -> bool:
        return self.t_transcript_start > 0

    @property
    def ttft(self) -> float | None:
        """Time-to-first-token: first audio sent → TranscriptStart."""
        if not self.t_transcript_start:
            return None
        return self.t_transcript_start - self.t_first_audio_sent

    @property
    def first_chunk_latency(self) -> float | None:
        """First audio sent → first TranscriptChunk."""
        if not self.t_transcript_chunks:
            return None
        return self.t_transcript_chunks[0] - self.t_first_audio_sent

    @property
    def chunk_intervals(self) -> list[float]:
        """Inter-chunk latencies."""
        if len(self.t_transcript_chunks) < 2:
            return []
        return [
            self.t_transcript_chunks[i] - self.t_transcript_chunks[i - 1]
            for i in range(1, len(self.t_transcript_chunks))
        ]

    @property
    def final_latency(self) -> float:
        """AudioStop → Transcript."""
        return self.t_transcript_final - self.t_audio_stop_sent

    @property
    def e2e_latency(self) -> float:
        """First AudioChunk → Transcript."""
        return self.t_transcript_final - self.t_first_audio_sent

    @property
    def rtf(self) -> float:
        """Real-time factor: e2e_latency / audio_duration. <1 = faster than real-time."""
        if self.audio_duration_s <= 0:
            return 0.0
        return self.e2e_latency / self.audio_duration_s

    @property
    def time_to_usable_text(self) -> float | None:
        """Time from first audio to first usable partial text (streaming) or final text (non-streaming)."""
        if self.t_transcript_chunks:
            return self.t_transcript_chunks[0] - self.t_first_audio_sent
        return self.e2e_latency


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def audio_to_pcm16(audio_path: str | Path, sample_rate: int = 16000) -> bytes:
    """Convert audio file to PCM16 16kHz mono via ffmpeg."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path),
        "-ar", str(sample_rate), "-ac", "1",
        "-f", "s16le", "-acodec", "pcm_s16le",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")
    return result.stdout


def get_audio_duration(audio_path: str | Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def run_one(
    *,
    uri: str,
    audio_path: Path,
    audio_bytes: bytes,
    audio_duration: float,
    chunk_ms: int,
    timeout: float,
    realtime: bool,
    language: str,
) -> RunTimings:
    """Run a single transcription and collect timing data."""

    timings = RunTimings(
        audio_file=audio_path.name,
        audio_duration_s=audio_duration,
    )

    chunk_size = max(1, int(16000 * 2 * (chunk_ms / 1000.0)))

    async with AsyncClient.from_uri(uri) as client:
        # Send Transcribe + AudioStart
        await client.write_event(Transcribe(language=language).event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())

        # Reader task — collects all server events with timestamps
        async def read_events() -> None:
            while True:
                event = await asyncio.wait_for(client.read_event(), timeout=timeout)
                now = time.monotonic()

                if TranscriptStart.is_type(event.type):
                    timings.t_transcript_start = now
                elif TranscriptChunk.is_type(event.type):
                    timings.t_transcript_chunks.append(now)
                    timings.chunk_texts.append(TranscriptChunk.from_event(event).text)
                elif TranscriptStop.is_type(event.type):
                    timings.t_transcript_stop = now
                elif Transcript.is_type(event.type):
                    timings.t_transcript_final = now
                    timings.final_text = Transcript.from_event(event).text.strip()
                    return

        reader = asyncio.create_task(read_events())

        # Send audio chunks
        first_sent = False
        for offset in range(0, len(audio_bytes), chunk_size):
            payload = audio_bytes[offset : offset + chunk_size]
            await client.write_event(
                AudioChunk(rate=16000, width=2, channels=1, audio=payload).event()
            )
            if not first_sent:
                timings.t_first_audio_sent = time.monotonic()
                first_sent = True

            if realtime:
                await asyncio.sleep(len(payload) / (16000 * 2))

        # Send AudioStop
        await client.write_event(AudioStop().event())
        timings.t_audio_stop_sent = time.monotonic()

        # Wait for final transcript
        await reader

    return timings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt_ms(val: float | None) -> str:
    if val is None:
        return "  n/a"
    return f"{val * 1000:7.1f}ms"


def _stats(values: list[float]) -> dict:
    """Compute stats for a list of values."""
    if not values:
        return {}
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def print_report(all_timings: list[RunTimings], label: str) -> dict:
    """Print a formatted latency report and return summary dict."""

    print()
    print("=" * 76)
    print(f"  LATENCY REPORT: {label}")
    print("=" * 76)

    is_streaming = any(t.is_streaming for t in all_timings)

    # Per-file detail
    by_file: dict[str, list[RunTimings]] = {}
    for t in all_timings:
        by_file.setdefault(t.audio_file, []).append(t)

    for filename, runs in by_file.items():
        dur = runs[0].audio_duration_s
        print(f"\n  {filename} ({dur:.2f}s audio, {len(runs)} run(s))")
        print(f"  {'─' * 70}")

        for i, r in enumerate(runs):
            run_label = f"    run {i+1}: "
            parts = []
            if r.is_streaming:
                parts.append(f"TTFT={_fmt_ms(r.ttft)}")
                parts.append(f"1st chunk={_fmt_ms(r.first_chunk_latency)}")
                parts.append(f"#chunks={len(r.t_transcript_chunks)}")
            parts.append(f"final={_fmt_ms(r.final_latency)}")
            parts.append(f"e2e={_fmt_ms(r.e2e_latency)}")
            parts.append(f"RTF={r.rtf:.3f}")
            print(run_label + "  ".join(parts))

            if r.chunk_intervals:
                intervals_str = ", ".join(f"{v*1000:.0f}ms" for v in r.chunk_intervals)
                print(f"             chunk intervals: [{intervals_str}]")

            if r.chunk_texts:
                streaming_text = "".join(r.chunk_texts)
                print(f"             streaming text: \"{streaming_text}\"")
            print(f"             final text:     \"{r.final_text}\"")

    # Aggregate stats
    print(f"\n  {'─' * 70}")
    print(f"  AGGREGATE ({len(all_timings)} runs across {len(by_file)} file(s))")
    print(f"  {'─' * 70}")

    summary = {}

    if is_streaming:
        ttfts = [t.ttft for t in all_timings if t.ttft is not None]
        first_chunks = [t.first_chunk_latency for t in all_timings if t.first_chunk_latency is not None]
        all_intervals = []
        for t in all_timings:
            all_intervals.extend(t.chunk_intervals)

        if ttfts:
            s = _stats(ttfts)
            print(f"    TTFT:            mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
            summary["ttft"] = s

        if first_chunks:
            s = _stats(first_chunks)
            print(f"    1st chunk:       mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
            summary["first_chunk"] = s

        if all_intervals:
            s = _stats(all_intervals)
            print(f"    chunk interval:  mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
            summary["chunk_interval"] = s

        usable = [t.time_to_usable_text for t in all_timings if t.time_to_usable_text is not None]
        if usable:
            s = _stats(usable)
            print(f"    time-to-usable:  mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
            summary["time_to_usable"] = s

    finals = [t.final_latency for t in all_timings]
    e2es = [t.e2e_latency for t in all_timings]
    rtfs = [t.rtf for t in all_timings]

    s = _stats(finals)
    print(f"    final latency:   mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
    summary["final_latency"] = s

    s = _stats(e2es)
    print(f"    e2e latency:     mean={_fmt_ms(s['mean'])}  median={_fmt_ms(s['median'])}  min={_fmt_ms(s['min'])}  max={_fmt_ms(s['max'])}")
    summary["e2e_latency"] = s

    s_rtf = _stats(rtfs)
    print(f"    RTF:             mean={s_rtf['mean']:.3f}    median={s_rtf['median']:.3f}    min={s_rtf['min']:.3f}    max={s_rtf['max']:.3f}")
    summary["rtf"] = s_rtf

    print("=" * 76)
    print()

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_test_pairs(test_dir: Path) -> list[tuple[Path, Path]]:
    """Find audio/text pairs in test directory."""
    pairs = []
    for txt_file in sorted(test_dir.glob("*.txt")):
        stem = txt_file.stem
        for ext in (".wav", ".mp3", ".m4a", ".flac"):
            audio_file = txt_file.with_suffix(ext)
            if audio_file.exists():
                pairs.append((audio_file, txt_file))
                break
    return pairs


async def main() -> None:
    parser = argparse.ArgumentParser(description="Latency benchmark for Wyoming Parakeet MLX")
    parser.add_argument("--uri", default="tcp://127.0.0.1:10301", help="Server URI")
    parser.add_argument("--test-dir", type=Path, required=True, help="Directory with audio/text test pairs")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per file (default: 3)")
    parser.add_argument("--chunk-ms", type=int, default=120, help="Audio chunk size in ms (default: 120)")
    parser.add_argument("--realtime", action="store_true", default=True, help="Pace audio at real-time speed (default)")
    parser.add_argument("--no-realtime", dest="realtime", action="store_false", help="Send audio as fast as possible")
    parser.add_argument("--timeout", type=float, default=30.0, help="Event timeout in seconds")
    parser.add_argument("--language", default="en")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs per file (excluded from stats, default: 1)")
    parser.add_argument("--json", type=Path, help="Write JSON results to file")
    parser.add_argument("--label", default=None, help="Label for report header")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    pairs = find_test_pairs(args.test_dir)
    if not pairs:
        print(f"No audio/text pairs found in {args.test_dir}", file=sys.stderr)
        sys.exit(1)

    # Filter out very short audio (<0.5s) as it doesn't produce meaningful latency data
    valid_pairs = []
    for audio_path, txt_path in pairs:
        dur = get_audio_duration(audio_path)
        if dur < 0.5:
            _LOGGER.info("Skipping %s (%.2fs — too short for latency test)", audio_path.name, dur)
        else:
            valid_pairs.append((audio_path, txt_path, dur))

    if not valid_pairs:
        print("No audio files >= 0.5s found", file=sys.stderr)
        sys.exit(1)

    # Pre-convert all audio
    audio_data = {}
    for audio_path, _, dur in valid_pairs:
        _LOGGER.info("Converting %s (%.2fs)", audio_path.name, dur)
        audio_data[audio_path] = audio_to_pcm16(audio_path)

    # Detect streaming by doing a probe run
    _LOGGER.info("Probing server at %s...", args.uri)
    probe = await run_one(
        uri=args.uri,
        audio_path=valid_pairs[0][0],
        audio_bytes=audio_data[valid_pairs[0][0]],
        audio_duration=valid_pairs[0][2],
        chunk_ms=args.chunk_ms,
        timeout=args.timeout,
        realtime=args.realtime,
        language=args.language,
    )
    mode = "streaming" if probe.is_streaming else "non-streaming"
    label = args.label or f"{mode} (chunk_ms={args.chunk_ms})"
    _LOGGER.info("Server mode: %s — probe final text: \"%s\"", mode, probe.final_text)

    # Warmup
    if args.warmup > 0:
        _LOGGER.info("Warmup: %d run(s) per file...", args.warmup)
        for audio_path, _, dur in valid_pairs:
            for w in range(args.warmup):
                _LOGGER.info("  warmup %d/%d: %s", w + 1, args.warmup, audio_path.name)
                await run_one(
                    uri=args.uri,
                    audio_path=audio_path,
                    audio_bytes=audio_data[audio_path],
                    audio_duration=dur,
                    chunk_ms=args.chunk_ms,
                    timeout=args.timeout,
                    realtime=args.realtime,
                    language=args.language,
                )

    # Benchmark runs
    all_timings: list[RunTimings] = []
    _LOGGER.info("Benchmark: %d run(s) per file × %d file(s)...", args.runs, len(valid_pairs))

    for audio_path, txt_path, dur in valid_pairs:
        for run_idx in range(args.runs):
            _LOGGER.info("  run %d/%d: %s", run_idx + 1, args.runs, audio_path.name)
            t = await run_one(
                uri=args.uri,
                audio_path=audio_path,
                audio_bytes=audio_data[audio_path],
                audio_duration=dur,
                chunk_ms=args.chunk_ms,
                timeout=args.timeout,
                realtime=args.realtime,
                language=args.language,
            )
            all_timings.append(t)

    # Report
    summary = print_report(all_timings, label)

    # Optional JSON output
    if args.json:
        json_data = {
            "label": label,
            "mode": mode,
            "settings": {
                "chunk_ms": args.chunk_ms,
                "realtime": args.realtime,
                "runs": args.runs,
                "warmup": args.warmup,
            },
            "files": [
                {
                    "file": t.audio_file,
                    "audio_duration_s": t.audio_duration_s,
                    "ttft_s": t.ttft,
                    "first_chunk_latency_s": t.first_chunk_latency,
                    "chunk_count": len(t.t_transcript_chunks),
                    "chunk_intervals_s": t.chunk_intervals,
                    "final_latency_s": t.final_latency,
                    "e2e_latency_s": t.e2e_latency,
                    "rtf": t.rtf,
                    "streaming_text": "".join(t.chunk_texts),
                    "final_text": t.final_text,
                }
                for t in all_timings
            ],
            "summary": summary,
        }
        args.json.write_text(json.dumps(json_data, indent=2))
        _LOGGER.info("JSON results written to %s", args.json)


if __name__ == "__main__":
    asyncio.run(main())
