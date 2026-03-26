"""Event handler for clients of the Wyoming Parakeet MLX server."""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import logging
import tempfile
import wave

import mlx.core as mx
import numpy as np
from parakeet_mlx.parakeet import StreamingParakeet

from wyoming.asr import (
    Transcribe,
    Transcript,
    TranscriptChunk,
    TranscriptStart,
    TranscriptStop,
)
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

_LOGGER = logging.getLogger(__name__)


class _ClientDisconnected(Exception):
    """Raised when peer disconnects during read/write."""


class ParakeetEventHandler(AsyncEventHandler):
    """Event handler for Wyoming protocol clients."""

    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        model,
        streaming_lock: asyncio.Lock,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self._model = model
        self._streaming_lock = streaming_lock
        self.trace_events = bool(getattr(cli_args, "trace_events", False))

        # Use bytearray for O(1) amortized appends (bytes are immutable)
        self.audio = bytearray()
        self.audio_converter = AudioChunkConverter(
            rate=16000,
            width=2,
            channels=1,
        )

        self.streaming_enabled = bool(getattr(cli_args, "streaming", False))
        self.stream_chunk_seconds = max(
            0.05, float(getattr(cli_args, "stream_chunk_seconds", 0.5))
        )
        self._stream_chunk_bytes = int(16000 * 2 * self.stream_chunk_seconds)
        self._stream_min_audio_bytes = int(16000 * 2 * 0.8)

        # StreamingParakeet configuration
        self._stream_context_size = tuple(
            getattr(cli_args, "stream_context_size", (128, 64))
        )
        self._stream_depth = int(getattr(cli_args, "stream_depth", 1))

        # Streaming state
        self._stream_started = False
        self._stream_last_sent_text = ""
        self._last_finalized_count: int = 0
        self._stream_pending_audio = bytearray()  # PCM16 bytes not yet fed to streamer
        self._streamer: StreamingParakeet | None = None
        self._trace_audio_chunk_count = 0

        peer = self.writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            self._peer_name = f"{peer[0]}:{peer[1]}"
        elif peer is not None:
            self._peer_name = str(peer)
        else:
            self._peer_name = "unknown"

    # ------------------------------------------------------------------
    # Streaming state management
    # ------------------------------------------------------------------

    def _reset_streaming_state(self) -> None:
        """Reset per-session streaming state (does NOT clean up streamer)."""
        self._stream_started = False
        self._stream_last_sent_text = ""
        self._last_finalized_count = 0
        self._stream_pending_audio.clear()
        self._trace_audio_chunk_count = 0

    async def _start_streamer(self) -> None:
        """Create and enter a StreamingParakeet context (acquires model lock)."""
        await self._streaming_lock.acquire()
        try:
            self._streamer = self._model.transcribe_stream(
                context_size=self._stream_context_size,
                depth=self._stream_depth,
            )
            # __enter__ switches encoder attention — must run in thread to avoid
            # blocking the asyncio event loop
            await asyncio.to_thread(self._streamer.__enter__)
        except BaseException:
            self._streaming_lock.release()
            self._streamer = None
            raise

    async def _stop_streamer(self) -> None:
        """Exit StreamingParakeet context and release the model lock."""
        if self._streamer is None:
            return
        try:
            await asyncio.to_thread(self._streamer.__exit__, None, None, None)
        except Exception:
            _LOGGER.exception("Error stopping streamer")
        finally:
            self._streamer = None
            self._streaming_lock.release()

    # ------------------------------------------------------------------
    # Audio conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _pcm16_to_mlx(pcm_bytes: bytes) -> mx.array:
        """Convert PCM16 16kHz mono bytes to normalized MLX float32 array."""
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return mx.array(samples)

    # ------------------------------------------------------------------
    # Disconnect detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_disconnect_error(err: BaseException) -> bool:
        if isinstance(err, _ClientDisconnected):
            return True

        if isinstance(err, (ConnectionResetError, BrokenPipeError)):
            return True

        if isinstance(err, OSError):
            return err.errno in {
                errno.ENOTCONN,
                errno.ECONNRESET,
                errno.EPIPE,
                errno.EBADF,
            }

        return False

    # ------------------------------------------------------------------
    # Event I/O
    # ------------------------------------------------------------------

    async def _write_event_or_disconnect(self, event: Event) -> None:
        if self.trace_events and not AudioChunk.is_type(event.type):
            _LOGGER.info("TX[%s] %s", self._peer_name, event.type)

        try:
            await self.write_event(event)
        except Exception as err:
            if self._is_disconnect_error(err):
                raise _ClientDisconnected from err
            raise

    def _trace_incoming_event(self, event: Event) -> None:
        if not self.trace_events:
            return

        if AudioChunk.is_type(event.type):
            self._trace_audio_chunk_count += 1
            return

        data_keys = sorted((event.data or {}).keys())
        _LOGGER.info(
            "RX[%s] %s %s",
            self._peer_name,
            event.type,
            data_keys,
        )

    async def _read_event_with_trace(self) -> Event | None:
        try:
            json_line = await self.reader.readline()
        except Exception as err:
            if self._is_disconnect_error(err):
                raise _ClientDisconnected from err
            raise

        if not json_line:
            if self.trace_events:
                _LOGGER.info("RX[%s] eof", self._peer_name)
            return None

        try:
            event_dict = json.loads(json_line)
        except Exception:
            if self.trace_events:
                preview = json_line[:120]
                preview_text = preview.decode("utf-8", errors="replace").strip()
                preview_hex = preview[:32].hex()
                _LOGGER.warning(
                    "RX[%s] invalid_json_line bytes=%d text=%r hex=%s",
                    self._peer_name,
                    len(json_line),
                    preview_text,
                    preview_hex,
                )
            return None

        data = event_dict.get("data", {})
        if not isinstance(data, dict):
            data = {}

        data_length = event_dict.get("data_length")
        if isinstance(data_length, int) and data_length > 0:
            try:
                data_bytes = await self.reader.readexactly(data_length)
            except Exception as err:
                if self._is_disconnect_error(err):
                    raise _ClientDisconnected from err
                raise

            try:
                extra_data = json.loads(data_bytes)
                if isinstance(extra_data, dict):
                    data.update(extra_data)
            except Exception:
                if self.trace_events:
                    _LOGGER.warning(
                        "RX[%s] invalid_json_data bytes=%d",
                        self._peer_name,
                        data_length,
                    )
                return None

        payload: bytes | None = None
        payload_length = event_dict.get("payload_length")
        if isinstance(payload_length, int) and payload_length > 0:
            try:
                payload = await self.reader.readexactly(payload_length)
            except Exception as err:
                if self._is_disconnect_error(err):
                    raise _ClientDisconnected from err
                raise

        event_type = event_dict.get("type")
        if not isinstance(event_type, str) or not event_type:
            if self.trace_events:
                _LOGGER.warning(
                    "RX[%s] invalid_event_type value=%r",
                    self._peer_name,
                    event_type,
                )
            return None

        return Event(type=event_type, data=data, payload=payload)

    # ------------------------------------------------------------------
    # Transcription (non-streaming path)
    # ------------------------------------------------------------------

    def _transcribe_audio_bytes(self, audio_bytes: bytes | bytearray) -> str:
        """Transcribe raw PCM16 bytes via a temp WAV file (non-streaming path)."""
        if not audio_bytes:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmpfile:
            with wave.open(tmpfile, "wb") as wavfile:
                wavfile.setnchannels(1)
                wavfile.setsampwidth(2)
                wavfile.setframerate(16000)
                wavfile.writeframes(audio_bytes)

            tmpfile.flush()

            result = self._model.transcribe(tmpfile.name)
            return result.text.strip()

    async def _transcribe_current_audio(self) -> str:
        """Transcribe the full audio buffer, serialized via the streaming lock."""
        audio_copy = bytes(self.audio)  # snapshot for thread safety
        async with self._streaming_lock:
            return await asyncio.to_thread(self._transcribe_audio_bytes, audio_copy)

    # ------------------------------------------------------------------
    # Streaming path
    # ------------------------------------------------------------------

    async def _maybe_emit_stream_chunk(self, *, force: bool) -> None:
        """Incrementally feed new audio to the streamer, emit finalized token deltas."""
        if not self.streaming_enabled:
            return
        if not self._stream_pending_audio:
            return

        pending_len = len(self._stream_pending_audio)
        total_audio_len = len(self.audio)

        if not force:
            # Minimum audio before first transcription (~0.8s)
            if total_audio_len < self._stream_min_audio_bytes:
                return
            # Throttle: wait for stream_chunk_bytes of new audio
            if pending_len < self._stream_chunk_bytes:
                return

        # Lazily create the streamer on first qualifying chunk (acquires lock)
        if self._streamer is None:
            await self._start_streamer()

        # Consume pending audio
        pcm_bytes = bytes(self._stream_pending_audio)
        self._stream_pending_audio.clear()

        audio_array = self._pcm16_to_mlx(pcm_bytes)

        # add_audio calls mx.eval() synchronously — run in thread
        streamer = self._streamer
        await asyncio.to_thread(streamer.add_audio, audio_array)

        # Check for new finalized tokens
        current_finalized_count = len(streamer.finalized_tokens)
        if current_finalized_count <= self._last_finalized_count:
            return  # No new finalized tokens yet

        # Build text from all finalized tokens
        finalized_text = "".join(t.text for t in streamer.finalized_tokens).strip()
        if not finalized_text:
            return

        # Emit TranscriptStart if not yet started
        if not self._stream_started:
            await self._write_event_or_disconnect(TranscriptStart().event())
            self._stream_started = True

        # Compute delta from last sent text
        if finalized_text.startswith(self._stream_last_sent_text):
            delta = finalized_text[len(self._stream_last_sent_text):]
        else:
            # Finalized text diverged (shouldn't happen; finalized_tokens are monotonic)
            delta = ""

        self._last_finalized_count = current_finalized_count

        if delta:
            await self._write_event_or_disconnect(TranscriptChunk(text=delta).event())
            self._stream_last_sent_text = finalized_text

    async def _finish_streaming(self) -> str:
        """Flush remaining audio, get final result from streamer, emit events."""
        if not self.streaming_enabled:
            return ""

        # If the streamer was never started (e.g. audio was too short to cross
        # the _stream_min_audio_bytes threshold), start it now to process all
        # pending audio so we can still emit streaming events.
        if self._streamer is None and (self._stream_pending_audio or self.audio):
            # Use all audio as pending if we haven't fed any yet
            if not self._stream_pending_audio and self.audio:
                self._stream_pending_audio.extend(self.audio)
            if self._stream_pending_audio:
                await self._start_streamer()
                pcm_bytes = bytes(self._stream_pending_audio)
                self._stream_pending_audio.clear()
                audio_array = self._pcm16_to_mlx(pcm_bytes)
                await asyncio.to_thread(self._streamer.add_audio, audio_array)

        # Feed any remaining buffered audio to an already-running streamer
        elif self._streamer is not None and self._stream_pending_audio:
            pcm_bytes = bytes(self._stream_pending_audio)
            self._stream_pending_audio.clear()
            audio_array = self._pcm16_to_mlx(pcm_bytes)
            await asyncio.to_thread(self._streamer.add_audio, audio_array)

        # Get final text from streamer (finalized + draft tokens)
        final_text = ""
        if self._streamer is not None:
            result = self._streamer.result
            final_text = result.text.strip()

        if final_text:
            if not self._stream_started:
                await self._write_event_or_disconnect(TranscriptStart().event())
                self._stream_started = True

            if final_text.startswith(self._stream_last_sent_text):
                tail = final_text[len(self._stream_last_sent_text):]
            else:
                tail = ""

            if tail:
                await self._write_event_or_disconnect(TranscriptChunk(text=tail).event())
            self._stream_last_sent_text = final_text

        if self._stream_started:
            await self._write_event_or_disconnect(TranscriptStop().event())

        # Clean up streamer and release model lock
        await self._stop_streamer()

        return final_text

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def handle_event(self, event: Event) -> bool:
        """Handle incoming Wyoming protocol events."""

        if AudioChunk.is_type(event.type):
            if not self.audio:
                _LOGGER.debug("Receiving audio")

            chunk = AudioChunk.from_event(event)
            chunk = self.audio_converter.convert(chunk)
            self.audio.extend(chunk.audio)  # bytearray.extend is O(1) amortized

            if self.streaming_enabled:
                self._stream_pending_audio.extend(chunk.audio)
                try:
                    await self._maybe_emit_stream_chunk(force=False)
                except _ClientDisconnected:
                    return False
                except Exception:
                    _LOGGER.exception(
                        "Streaming update failed; falling back to final-only mode"
                    )
                    self.streaming_enabled = False
                    # Clean up streamer if it was started
                    if self._streamer is not None:
                        try:
                            await self._stop_streamer()
                        except Exception:
                            pass
                    self._reset_streaming_state()

            return True

        if AudioStop.is_type(event.type):
            _LOGGER.info(
                "Audio: %d chunks, %d bytes, %.1fs",
                self._trace_audio_chunk_count,
                len(self.audio),
                len(self.audio) / (16000 * 2),
            )

            text = ""
            try:
                if self.streaming_enabled:
                    # Emit streaming events (Start/Chunk/Stop) using StreamingParakeet.
                    # This may have lower accuracy than the full model transcription.
                    await self._finish_streaming()

                # Always use the full model for the final Transcript event for
                # best accuracy — this is the result Home Assistant uses.
                text = await self._transcribe_current_audio()
            except _ClientDisconnected:
                return False
            except Exception:
                _LOGGER.exception("Error during transcription")
            finally:
                # Ensure streamer is cleaned up even if _finish_streaming raised
                if self._streamer is not None:
                    try:
                        await self._stop_streamer()
                    except Exception:
                        pass
                self.audio = bytearray()
                self._reset_streaming_state()

            _LOGGER.info("Transcription: %s", text)
            await self._write_event_or_disconnect(Transcript(text=text).event())
            _LOGGER.debug("Completed request")
            return False

        if Transcribe.is_type(event.type):
            _LOGGER.debug("Transcribe event received")
            return True

        if Describe.is_type(event.type):
            await self._write_event_or_disconnect(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        return True

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run event loop and treat abrupt client disconnects as normal."""
        self._is_running = True
        if self.trace_events:
            _LOGGER.info("Client connected: %s", self._peer_name)

        try:
            # Send info immediately on connect for Home Assistant compatibility.
            try:
                await self._write_event_or_disconnect(self.wyoming_info_event)
                if self.trace_events:
                    _LOGGER.info("TX[%s] info (on connect)", self._peer_name)
            except _ClientDisconnected:
                return

            while self._is_running:
                try:
                    event = await self._read_event_with_trace()
                except _ClientDisconnected:
                    break
                except Exception as err:
                    # Home Assistant and network clients may close sockets abruptly.
                    # Treat common disconnect errors as a normal end of session.
                    if self._is_disconnect_error(err):
                        break
                    raise

                if event is None:
                    break

                self._trace_incoming_event(event)

                try:
                    if not (await self.handle_event(event)):
                        break
                except _ClientDisconnected:
                    break
        finally:
            if self.trace_events:
                _LOGGER.info("Client disconnected: %s", self._peer_name)
            # Ensure streamer is cleaned up (releases lock) on abrupt disconnect
            if self._streamer is not None:
                try:
                    await self._stop_streamer()
                except Exception:
                    _LOGGER.debug(
                        "Error cleaning up streamer on disconnect", exc_info=True
                    )
            await self.disconnect()
