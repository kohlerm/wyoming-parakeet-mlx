#!/usr/bin/env python3
"""Wyoming server for Parakeet MLX speech-to-text on Apple Silicon."""
import argparse
import asyncio
import logging
from functools import partial

from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer

from . import __version__
from .const import DEFAULT_MODEL, DEFAULT_STREAM_CONTEXT_SIZE, DEFAULT_STREAM_DEPTH, PARAKEET_LANGUAGES
from .handler import ParakeetEventHandler

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Wyoming server for Parakeet MLX speech-to-text"
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Parakeet MLX model to use (HuggingFace repo ID)",
    )
    parser.add_argument(
        "--uri",
        required=True,
        help="Server URI (e.g., tcp://0.0.0.0:10300)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Default language code (default: en)",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=0,
        help="Beam size for decoding (0 = greedy, >0 = beam search)",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Enable streaming transcript events (TranscriptStart/Chunk/Stop)",
    )
    parser.add_argument(
        "--stream-chunk-seconds",
        type=float,
        default=0.5,
        help="How often to emit streaming chunks in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--stream-context-size",
        type=int,
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=list(DEFAULT_STREAM_CONTEXT_SIZE),
        help="StreamingParakeet context size (left, right) in encoder frames (default: 128 64)",
    )
    parser.add_argument(
        "--stream-depth",
        type=int,
        default=DEFAULT_STREAM_DEPTH,
        help="StreamingParakeet depth for draft decoding (default: 1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log DEBUG messages",
    )
    parser.add_argument(
        "--trace-events",
        action="store_true",
        help="Log incoming/outgoing Wyoming event types per client",
    )
    parser.add_argument(
        "--log-format",
        default=logging.BASIC_FORMAT,
        help="Format for log messages",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="Print version and exit",
    )
    args = parser.parse_args()
    # Normalize stream_context_size list → tuple
    args.stream_context_size = tuple(args.stream_context_size)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s:%(name)s:%(message)s",
        datefmt="%H:%M:%S",
    )
    _LOGGER.debug("Arguments: %s", args)

    # Load the Parakeet MLX model at startup
    _LOGGER.info("Loading Parakeet MLX model: %s", args.model)
    from parakeet_mlx import from_pretrained

    model = from_pretrained(args.model)
    _LOGGER.info("Model loaded successfully")

    wyoming_info = Info(
        asr=[
            AsrProgram(
                name="parakeet-mlx",
                description="Nvidia Parakeet speech-to-text via MLX on Apple Silicon",
                attribution=Attribution(
                    name="senstella",
                    url="https://github.com/senstella/parakeet-mlx",
                ),
                installed=True,
                version=__version__,
                models=[
                    AsrModel(
                        name=args.model,
                        description=f"Parakeet MLX ({args.model})",
                        attribution=Attribution(
                            name="NVIDIA NeMo / MLX Community",
                            url="https://huggingface.co/collections/mlx-community/parakeet",
                        ),
                        installed=True,
                        languages=PARAKEET_LANGUAGES,
                        version=__version__,
                    )
                ],
            )
        ],
    )

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Ready — listening on %s", args.uri)

    streaming_lock = asyncio.Lock()

    await server.run(
        partial(ParakeetEventHandler, wyoming_info, args, model, streaming_lock)
    )


def run() -> None:
    """Run the server."""
    asyncio.run(main())


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
