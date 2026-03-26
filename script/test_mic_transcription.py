#!/usr/bin/env python3
"""Record from the default microphone and run a local transcription test."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from parakeet_mlx import from_pretrained

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from wyoming_parakeet_mlx.const import DEFAULT_MODEL


def record_wav(output_path: Path, duration: float, input_device: str) -> None:
    """Record a mono 16 kHz WAV file using ffmpeg on macOS."""
    cmd = [
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
        "-c:a",
        "pcm_s16le",
        str(output_path),
        "-y",
    ]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as err:
        raise RuntimeError(
            "ffmpeg was not found. Install it with: brew install ffmpeg"
        ) from err
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            "Recording failed. If this is your first run, allow microphone access "
            "for your terminal app in System Settings > Privacy & Security > "
            "Microphone."
        ) from err


def list_audio_devices() -> int:
    """List avfoundation devices and exit."""
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


def inspect_audio(path: Path) -> tuple[float, float, float]:
    """Return duration (s), RMS level, and peak level for recorded WAV."""
    with wave.open(str(path), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        raw = wav_file.readframes(n_frames)

    if sample_width != 2:
        raise RuntimeError(f"Unexpected sample width: {sample_width * 8} bits")

    audio = np.frombuffer(raw, dtype=np.int16)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

    if audio.size == 0:
        return 0.0, 0.0, 0.0

    normalized = audio.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(normalized))))
    peak = float(np.max(np.abs(normalized)))
    duration = float(n_frames / sample_rate) if sample_rate else 0.0
    return duration, rms, peak


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record from your mic and transcribe with parakeet-mlx"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Recording duration in seconds (default: 5)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Parakeet model repo ID (default: %(default)s)",
    )
    parser.add_argument(
        "--input-device",
        default=":0",
        help=(
            "ffmpeg avfoundation device string (default: :0 for the default "
            "microphone)"
        ),
    )
    parser.add_argument(
        "--keep-audio",
        type=Path,
        help="Optional path to save the recorded WAV file",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List ffmpeg avfoundation devices and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_devices:
        return list_audio_devices()

    if args.duration <= 0:
        print("--duration must be greater than 0", file=sys.stderr)
        return 2

    output_path: Path
    temp_file = None

    if args.keep_audio:
        output_path = args.keep_audio
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_file.close()
        output_path = Path(temp_file.name)

    try:
        print(f"Loading model: {args.model}")
        model = from_pretrained(args.model)

        print(f"Recording {args.duration:.1f}s from microphone ({args.input_device})...")
        record_wav(output_path=output_path, duration=args.duration, input_device=args.input_device)

        duration, rms, peak = inspect_audio(output_path)
        print(f"Captured {duration:.2f}s audio (rms={rms:.4f}, peak={peak:.4f})")
        if rms < 0.002 and peak < 0.02:
            print(
                "Warning: input looks almost silent. Try a different --input-device "
                "or check macOS microphone permissions.",
                file=sys.stderr,
            )

        print("Transcribing...")
        result = model.transcribe(str(output_path))
        text = result.text.strip()

        print("\nTranscript")
        print("----------")
        print(text or "<no speech detected>")

        if args.keep_audio:
            print(f"\nSaved recording to: {output_path}")

        return 0
    finally:
        if temp_file is not None and output_path.exists():
            output_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
