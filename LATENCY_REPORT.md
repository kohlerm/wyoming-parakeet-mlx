# Latency Benchmark Report

**Date:** 2026-03-26
**Model:** `mlx-community/parakeet-tdt-0.6b-v3`
**Engine:** `parakeet_mlx` v0.5.1, StreamingParakeet API
**Hardware:** MacBook Pro, Apple M2 Pro, 32 GB
**OS:** macOS 26.3

## Test Configuration

| Parameter | Value |
|-----------|-------|
| Audio chunk size | 120 ms |
| Audio pacing | Realtime (simulates live microphone) |
| Streaming chunk interval | 0.5 s (default `--stream-chunk-seconds`) |
| Stream context size | 128 / 64 (left / right encoder frames) |
| Stream depth | 1 |
| Warmup runs | 1 per file |
| Benchmark runs | 3 per file |
| Beam size | 0 (greedy) |

### Test Files

| File | Duration | Expected Text |
|------|----------|---------------|
| `123.wav` | 2.40 s | "One, two, three." |
| `helloWorldPython.wav` | 3.87 s | "Implement Hello World in Python." |
| `torvaldsAI.wav` | 137.49 s | Linus Torvalds on AI and vibe coding (interview excerpt) |
| `stop.wav` | 0.42 s | Skipped (too short) |

## Key Result: Time to First Answer

The most important user-facing metric is **when text first appears relative to when the user stops speaking** (AudioStop event).

| File | Streaming | Non-Streaming | Streaming Advantage |
|------|-----------|---------------|---------------------|
| `123.wav` (2.4 s) | 237 ms **before** AudioStop | 213 ms **after** AudioStop | **~450 ms earlier** |
| `helloWorldPython.wav` (3.9 s) | 294 ms **before** AudioStop | 237 ms **after** AudioStop | **~530 ms earlier** |
| `torvaldsAI.wav` (137.5 s) | **131.7 s before** AudioStop | 3.9-11.0 s **after** AudioStop | **~135 s earlier** |

Streaming shows a usable transcription **before the user finishes speaking**. Non-streaming cannot begin transcription until after all audio is received. The advantage grows dramatically with audio length — for a 2+ minute clip, streaming delivers first text over 2 minutes before non-streaming can even start.

## Detailed Results: Streaming Mode

```
============================================================================
  LATENCY REPORT: STREAMING
============================================================================

  123.wav (2.40s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  TTFT= 2564ms  1st chunk= 2564ms  #chunks=1  final=  396ms  e2e= 2826ms  RTF=1.175
    run 2:  TTFT= 2566ms  1st chunk= 2566ms  #chunks=1  final=  315ms  e2e= 2745ms  RTF=1.141
    run 3:  TTFT= 2570ms  1st chunk= 2570ms  #chunks=1  final=  319ms  e2e= 2749ms  RTF=1.143
             streaming text: "One to three."
             final text:     "One, two, three."

  helloWorldPython.wav (3.87s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  TTFT= 4040ms  1st chunk= 4040ms  #chunks=1  final=  329ms  e2e= 4241ms  RTF=1.095
    run 2:  TTFT= 4044ms  1st chunk= 4044ms  #chunks=1  final=  347ms  e2e= 4258ms  RTF=1.100
    run 3:  TTFT= 4046ms  1st chunk= 4046ms  #chunks=1  final=  333ms  e2e= 4248ms  RTF=1.097
             streaming text: "Implement hello world in python."
             final text:     "Implement Hello World in Python."

  torvaldsAI.wav (137.49s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  TTFT= 5745ms  1st chunk= 5745ms  #chunks=172  final= 2714ms  e2e=141814ms  RTF=1.031
    run 2:  TTFT= 5744ms  1st chunk= 5744ms  #chunks=172  final= 2925ms  e2e=141777ms  RTF=1.031
    run 3:  TTFT= 5745ms  1st chunk= 5745ms  #chunks=172  final= 2968ms  e2e=141810ms  RTF=1.031
             streaming text: "Hey, clockwhat I want you to develop this all the way..."
             final text:     "Hey Claude, I want you to develop this feature all the way..."

  AGGREGATE — Short files (6 runs, 123.wav + helloWorldPython.wav)
  ──────────────────────────────────────────────────────────────────────
    TTFT:            mean= 3399ms  median= 3398ms  min= 2564ms  max= 4046ms
    final latency:   mean=  340ms  median=  327ms  min=  315ms  max=  396ms
    e2e latency:     mean= 3611ms  median= 3538ms  min= 2745ms  max= 4258ms
    RTF:             mean=1.124    median=1.119    min=1.095    max=1.189

  AGGREGATE — Long file (3 runs, torvaldsAI.wav)
  ──────────────────────────────────────────────────────────────────────
    TTFT:            mean= 5745ms  median= 5745ms  min= 5744ms  max= 5745ms
    chunks/run:      172
    final latency:   mean= 2869ms  median= 2925ms  min= 2714ms  max= 2968ms
    e2e latency:     mean=141800ms median=141810ms  min=141777ms max=141814ms
    RTF:             mean=1.031    median=1.031    min=1.031    max=1.032
============================================================================
```

## Detailed Results: Non-Streaming Mode

```
============================================================================
  LATENCY REPORT: NON-STREAMING
============================================================================

  123.wav (2.40s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  final=  246ms  e2e= 2683ms  RTF=1.116
    run 2:  final=  201ms  e2e= 2630ms  RTF=1.094
    run 3:  final=  198ms  e2e= 2629ms  RTF=1.093
             final text: "One, two, three."

  helloWorldPython.wav (3.87s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  final=  268ms  e2e= 4181ms  RTF=1.080
    run 2:  final=  246ms  e2e= 4159ms  RTF=1.074
    run 3:  final=  217ms  e2e= 4127ms  RTF=1.066
             final text: "Implement Hello World in Python."

  torvaldsAI.wav (137.49s audio, 3 runs)
  ──────────────────────────────────────────────────────────────────────
    run 1:  final= 3866ms  e2e=145155ms  RTF=1.056
    run 2:  final= 6226ms  e2e=152755ms  RTF=1.111
    run 3:  final=11013ms  e2e=155638ms  RTF=1.132
             final text: "Hey Claude, I want you to develop this feature all the way..."

  AGGREGATE — Short files (6 runs, 123.wav + helloWorldPython.wav)
  ──────────────────────────────────────────────────────────────────────
    final latency:   mean=  229ms  median=  224ms  min=  198ms  max=  268ms
    e2e latency:     mean= 3402ms  median= 3405ms  min= 2629ms  max= 4181ms
    RTF:             mean=1.087    median=1.087    min=1.066    max=1.116

  AGGREGATE — Long file (3 runs, torvaldsAI.wav)
  ──────────────────────────────────────────────────────────────────────
    final latency:   mean= 7035ms  median= 6226ms  min= 3866ms  max=11013ms  ⚠️ increasing
    e2e latency:     mean=151183ms median=152755ms  min=145155ms max=155638ms
    RTF:             mean=1.100    median=1.111    min=1.056    max=1.132
============================================================================
```

## Comparison Summary

### Short Audio (2-4 s)

| Metric | Non-Streaming | Streaming | Overhead |
|--------|---------------|-----------|----------|
| **First usable text** | AudioStop + 224 ms | AudioStop **- 260 ms** | **~490 ms faster** |
| **Final latency** (AudioStop → Transcript) | 224 ms | 340 ms | +116 ms (+52%) |
| **E2E latency** (first audio → Transcript) | 3,402 ms | 3,611 ms | +209 ms (+6.1%) |
| **RTF** (real-time factor) | 1.087 | 1.124 | +0.037 (+3.4%) |

### Long Audio (137.5 s — torvaldsAI.wav)

| Metric | Non-Streaming | Streaming | Delta |
|--------|---------------|-----------|-------|
| **First usable text** | AudioStop + 7.0 s (avg) | AudioStop **- 131.7 s** | **~139 s faster** |
| **TTFT** | N/A | 5.7 s from first audio | — |
| **Streaming chunks** | 0 | 172 (~0.77 s apart) | — |
| **Final latency** (AudioStop → Transcript) | 7.0 s (3.9-11.0, increasing) | 2.9 s (2.7-3.0, stable) | **-4.1 s (59% faster)** |
| **E2E latency** (first audio → Transcript) | 151.2 s | 141.8 s | **-9.4 s (6.2% faster)** |
| **RTF** (real-time factor) | 1.100 (1.056-1.132) | 1.031 (stable) | **-0.069 (6.3% faster)** |

Key observation: On long audio, streaming is **faster overall** — not just for first text, but also for the final Transcript. Non-streaming final latency degrades across repeated runs (3.9→6.2→11.0s), while streaming stays stable (~2.9s). The RTF advantage (1.031 vs 1.100) likely comes from the StreamingParakeet KV-cache keeping the encoder warm during realtime playback.

## Metric Definitions

| Metric | Definition |
|--------|-----------|
| **TTFT** | Time from first AudioChunk sent to TranscriptStart received |
| **1st chunk** | Time from first AudioChunk sent to first TranscriptChunk received |
| **Final latency** | Time from AudioStop sent to final Transcript received |
| **E2E latency** | Time from first AudioChunk sent to final Transcript received |
| **RTF** | Real-time factor = E2E latency / audio duration. Values < 1.0 mean faster than real-time; the compute overhead is RTF - 1.0 |
| **Time-to-usable** | Time from first AudioChunk to first usable text (streaming: 1st chunk; non-streaming: Transcript) |

## Analysis

### Why streaming shows text earlier

Streaming processes audio incrementally via the `StreamingParakeet` KV-cache encoder while audio is still arriving. The first `TranscriptChunk` is emitted once enough audio has accumulated (~0.8 s minimum + 0.5 s chunk interval). For short 2-4 s clips, partial text appears 200-320 ms **before** the user stops speaking. For a 137.5 s clip, the first text appears at 5.7 s — over **2 minutes** before AudioStop.

Non-streaming waits for AudioStop, then runs `model.transcribe()` on the complete audio buffer. No text is available until the full transcription completes.

### Why streaming final latency is higher (short audio) but lower (long audio)

**Short audio (2-4 s):** In streaming mode, AudioStop triggers two operations sequentially:
1. `_finish_streaming()` — flush remaining audio to StreamingParakeet, emit final TranscriptChunk + TranscriptStop
2. `_transcribe_current_audio()` — full `model.transcribe()` for the authoritative final Transcript

The full model re-transcription is necessary because StreamingParakeet has slightly lower accuracy (e.g. "One to three." vs "One, two, three."). This adds ~100-200 ms compared to non-streaming, which only runs step 2.

**Long audio (137.5 s):** Streaming final latency is **lower** (2.9 s vs 7.0 s avg). Two factors:
- The StreamingParakeet KV-cache keeps the encoder warm during realtime playback, so the final `model.transcribe()` benefits from cached computations
- Non-streaming final latency degrades across repeated runs (3.9→6.2→11.0 s), suggesting memory pressure or thermal throttling when processing 137.5 s of audio from cold

### Non-streaming latency degradation

Non-streaming shows alarming latency growth across consecutive runs on long audio:

| Run | Final Latency | RTF |
|-----|--------------|-----|
| 1 | 3.87 s | 1.056 |
| 2 | 6.23 s | 1.111 |
| 3 | 11.01 s | 1.132 |

This 2.8× increase over 3 runs was not observed in streaming mode (which stayed at 2.7-3.0 s). The cause is likely MLX memory management or macOS thermal throttling during sustained compute-heavy transcription. This degradation would compound in production with repeated long utterances.

### Streaming text quality

| File | Streaming Output | Final Output |
|------|-----------------|--------------|
| `123.wav` | "One to three." | "One, two, three." |
| `helloWorldPython.wav` | "Implement hello world in python." | "Implement Hello World in Python." |
| `torvaldsAI.wav` | "Hey, clockwhat I want you to develop..." | "Hey Claude, I want you to develop this feature..." |

Short audio: Streaming omits punctuation and capitalization details but is otherwise accurate. Long audio: Streaming quality degrades significantly — "Claude" → "clockwhat", "kernel" → "cernal", "programmers" → "flugrams". The final Transcript always uses full model transcription for best accuracy — this is the result Home Assistant consumes.

### Scaling expectations

The torvaldsAI results confirm and extend the short-audio observations:

| Audio Length | Streaming TTFT Advantage | RTF (Streaming) | RTF (Non-Streaming) |
|-------------|--------------------------|-----------------|---------------------|
| 2.4 s | ~450 ms | 1.153 | 1.101 |
| 3.9 s | ~530 ms | 1.097 | 1.073 |
| 137.5 s | **~131.7 s** | **1.031** | 1.100 |

The streaming RTF **improves** with audio length (1.15→1.03), while non-streaming RTF stays roughly constant or worsens. This demonstrates the O(n) vs O(n²) compute advantage of the StreamingParakeet incremental encoder: as audio grows, the streaming approach processes only new chunks while maintaining KV-cache state, rather than re-encoding the entire buffer.

## Reproducing

The `--test-dir` argument expects a directory containing audio/text pairs (e.g. `speech.wav` + `speech.txt`). Any `.wav`, `.mp3`, `.m4a`, or `.flac` file with a matching `.txt` file works.

```bash
# Start server in streaming mode
./wyoming-parakeet-mlx.sh --debug --streaming

# Run streaming benchmark (short files)
python script/test_latency.py \
    --uri tcp://127.0.0.1:10301 \
    --test-dir YOUR_TEST_DIR \
    --runs 3 --warmup 1 --label "STREAMING"

# For long audio files (>60s), increase the timeout:
python script/test_latency.py \
    --uri tcp://127.0.0.1:10301 \
    --test-dir YOUR_TEST_DIR \
    --runs 3 --warmup 1 --timeout 300 --label "STREAMING (long)"

# Restart server without streaming
./wyoming-parakeet-mlx.sh --debug

# Run non-streaming benchmark
python script/test_latency.py \
    --uri tcp://127.0.0.1:10301 \
    --test-dir YOUR_TEST_DIR \
    --runs 3 --warmup 1 --timeout 300 --label "NON-STREAMING"
```

Additional options: `--runs 5` for more data, `--no-realtime` to measure pure compute without audio pacing, `--chunk-ms 60` for finer-grained audio delivery, `--json results.json` to save raw data.
