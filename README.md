# Wyoming Parakeet MLX

A Wyoming protocol server for the [Parakeet MLX](https://github.com/senstella/parakeet-mlx) speech-to-text system. This allows you to use Nvidia's high-performance Parakeet ASR models, running natively on Apple Silicon via MLX, as a speech-to-text provider for [Home Assistant](https://www.home-assistant.io/).

This project provides a simple, efficient bridge between Home Assistant's voice pipeline and the `parakeet-mlx` library, offering a significant speed and performance advantage over CPU-based or non-native solutions on Mac hardware.

It is designed as a drop-in replacement for `wyoming-mlx-whisper`, but uses the Parakeet model for faster, streaming-capable transcription (English-focused).

## Features

*   **High Performance:** Leverages Apple Silicon's GPU for fast inference via the MLX framework.
*   **Streaming Ready:** Uses Parakeet's native `StreamingParakeet` API for incremental transcription — partial results appear progressively as you speak, with minimal latency overhead.
*   **Easy Installation:** Simple setup with a single script to run as a background service on macOS.
*   **Home Assistant Integration:** Implements the Wyoming protocol for seamless integration with Home Assistant's voice pipelines.
*   **Lightweight:** Parakeet models are significantly smaller than Whisper, requiring less memory.

## Requirements

*   macOS with Apple Silicon (M1, M2, M3, M4, etc.)
*   Python 3.10+
*   [uv](https://docs.astral.sh/uv/) (for Python version management and package installation)
*   [ffmpeg](https://ffmpeg.org/) (for audio processing)

## Installation

1.  **Install Dependencies:**

    Open a terminal on your Mac and install `ffmpeg` and `uv` using Homebrew.

    ```bash
    brew install ffmpeg uv
    ```

2.  **Clone the Repository:**

    ```bash
    git clone https://github.com/Wysie/wyoming-parakeet-mlx.git
    cd wyoming-parakeet-mlx
    ```

3.  **Set Up the Environment:**

    Run the setup script to create a virtual environment with Python 3.12 and install all dependencies.

    ```bash
    ./script/setup
    ```

4.  **Install as a Service (optional):**

    To run the server in the background and automatically on login:

    ```bash
    ./install_service.sh
    ```

    The script will prompt you for a port number (default: `10301`):

    ```
    Enter port number to listen on [10301]:
    ```

    Press **Enter** to accept the default, or type a custom port number. The server will then start and listen on `tcp://0.0.0.0:<port>`.

## Home Assistant Configuration

1.  Go to **Settings > Devices & Services > Add Integration**.
2.  Search for **Wyoming Protocol** and select it.
3.  Enter the IP address of your Mac and the port you chose during installation (default: `10301`).
4.  Click **Submit**. The Parakeet STT service should now be available to use in your voice pipelines.

## Usage

The server is controlled via `launchctl`.

*   **To Stop the Service:**
    ```bash
    launchctl unload ~/Library/LaunchAgents/com.wyoming_parakeet_mlx.plist
    ```
*   **To Start the Service Manually:**
    ```bash
    launchctl load ~/Library/LaunchAgents/com.wyoming_parakeet_mlx.plist
    ```
*   **To View Logs:**
    Logs are stored in the `log` directory within the repository folder.
    ```bash
    tail -f log/wyoming-parakeet-mlx.log
    ```

### Changing the Port

To change the port after the service has already been installed, uninstall and reinstall the service:

```bash
./uninstall_service.sh
./install_service.sh
```

You will be prompted for a new port number during reinstallation.

### Running Manually

To run the server directly in your terminal for debugging:

```bash
./wyoming-parakeet-mlx.sh --debug
```

To enable streaming transcript events (partial results while speaking):

```bash
./wyoming-parakeet-mlx.sh --debug --streaming
```

Streaming uses Parakeet's native `StreamingParakeet` API, which processes audio incrementally using an encoder KV-cache — only new audio is processed on each update, rather than re-transcribing the entire buffer from scratch. This results in O(n) total compute instead of O(n²).

You can tune how often partial chunks are emitted:

```bash
./wyoming-parakeet-mlx.sh --debug --streaming --stream-chunk-seconds 0.25
```

For lower-latency partial updates, reduce streaming chunk size:

```bash
./wyoming-parakeet-mlx.sh --debug --streaming --stream-chunk-seconds 0.12
```

#### Advanced Streaming Options

The `StreamingParakeet` encoder uses a sliding context window. You can tune the window size and depth:

```bash
./wyoming-parakeet-mlx.sh --debug --streaming --stream-context-size 256 128 --stream-depth 2
```

| Flag | Default | Description |
|------|---------|-------------|
| `--stream-chunk-seconds` | `0.5` | How often (in seconds) to emit streaming chunks |
| `--stream-context-size LEFT RIGHT` | `128 64` | Encoder context window in frames (left=keep, right=draft). Larger values improve quality but increase latency |
| `--stream-depth` | `1` | Number of encoder layers using cached KV. Higher values improve quality at the cost of more compute |

**Note:** The final `Transcript` event always uses a full model transcription for best accuracy. Streaming chunks are incremental previews that may differ slightly from the final result.

Or with a custom port by setting the `PARAKEET_PORT` environment variable:

```bash
PARAKEET_PORT=10302 ./wyoming-parakeet-mlx.sh --debug
```

Or by passing the URI directly:

```bash
.venv/bin/python -m wyoming_parakeet_mlx --uri tcp://0.0.0.0:10302 --debug
```

### Quick Microphone Transcription Test

You can run a local mic test to verify transcription end-to-end (record from your microphone, then transcribe with Parakeet MLX):

```bash
./.venv/bin/python script/test_mic_transcription.py
```

Options:

* `--duration 8` - record for 8 seconds
* `--model mlx-community/parakeet-tdt-0.6b-v3` - use a specific model
* `--list-devices` - list available microphone devices for `--input-device`
* `--keep-audio /tmp/mic-test.wav` - keep the recorded WAV file

If you get no transcript, try listing devices and choosing a specific one:

```bash
./.venv/bin/python script/test_mic_transcription.py --list-devices
./.venv/bin/python script/test_mic_transcription.py --input-device :1 --duration 8 --keep-audio /tmp/mic-test.wav
```

If recording fails on first run, allow microphone access for your terminal app in macOS **System Settings > Privacy & Security > Microphone**.

### Test a Running Wyoming Server

If you want to verify the actual Wyoming server path (including streaming events), run the server and then use the client test script.

Start server:

```bash
./wyoming-parakeet-mlx.sh --debug --streaming
```

In another terminal, run test client:

```bash
./.venv/bin/python script/test_wyoming_server.py --uri tcp://127.0.0.1:10301 --input-device :1
```

The script prints incoming server events including streaming chunks (`[stream] ...`) and the final transcript (`[final] ...`).

This client now streams microphone PCM directly to the server while you speak, so partial words can appear progressively during capture.

If partial text still arrives late, reduce client chunk size:

```bash
./.venv/bin/python script/test_wyoming_server.py --uri tcp://127.0.0.1:10301 --input-device :1 --duration 20 --chunk-ms 60
```

For continuous testing, run in loop mode (press `Ctrl+C` to stop):

```bash
./.venv/bin/python script/test_wyoming_server.py --uri tcp://127.0.0.1:10301 --input-device :1 --duration 12 --loop
```

To validate streaming behavior and accuracy with test audio files + expected text:

```bash
./.venv/bin/python script/test_wyoming_streaming_files.py --uri tcp://127.0.0.1:10301 --test-dir ~/parakeet-mlx/kyutai-mlx/python/test_data
```

This checks that each case receives streaming events (`start/chunk/stop`) and that final WER is under threshold.

## Uninstallation

Run the `uninstall_service.sh` script to stop and remove the `launchd` service.

```bash
./uninstall_service.sh
```

You can then safely delete the repository folder.

## License and Acknowledgements

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

This project is a derivative work and would not be possible without the excellent open-source projects it builds upon. We gratefully acknowledge their contributions.

*   **Core ASR Engine:** [parakeet-mlx](https://github.com/senstella/parakeet-mlx) by Senstella (Apache 2.0 License)
*   **Server Architecture:** The server structure and macOS service scripts are heavily based on [wyoming-mlx-whisper](https://github.com/vincent861223/wyoming-mlx-whisper) by Dr. Serge Victor (MIT License).
*   **Communication Protocol:** [wyoming](https://github.com/rhasspy/wyoming) by Michael Hansen (MIT License).
*   **ASR Model:** The Parakeet models are developed by [NVIDIA NeMo](https://github.com/NVIDIA/NeMo) (Apache 2.0 License).
*   **ML Framework:** The underlying MLX framework is developed by [Apple](https://github.com/ml-explore/mlx) (MIT License).

For full license details of all upstream dependencies, please see the [NOTICE](NOTICE) file.
