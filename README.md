# OpenCharm

A wearable that lets you talk to AI without being at your computer.

## The Idea

You shouldn't have to be sitting at a desk to use AI tools. OpenCharm is a tiny wrist-worn device that listens for voice commands, records what you say, and sends it to an AI agent that actually does stuff for you — runs commands, writes files, whatever you need. Results come back to your phone via Telegram.

Walk around. Be outside. Be cooking. Be anywhere. Touch grass. Just talk to your wrist and things happen on your machine.

## How It Works

```
┌──────────────────────────────────────────────┐
│      XIAO ESP32S3 Sense (on your wrist)      │
│  keyword spotting · mic · camera · LED       │
└──────────────────┬───────────────────────────┘
                   │ WiFi HTTP POST
                   ▼
┌──────────────────────────────────────────────┐
│       Relay Server (on your laptop)          │
│  Whisper transcription · session mgmt        │
│  Claude Code executor · Telegram bot         │
└──────────┬───────────────────┬───────────────┘
           │                   │
           ▼                   ▼
    Claude Code CLI      Telegram Bot
    (executes tasks)     (sends results)
```

1. **Say a keyword** — the bracelet runs an Edge Impulse ML model on-device to detect `RECORD`, `STOP`, `CAPTURE`, or `POST`.
2. **Record audio or snap a photo** — audio is captured at 16 kHz 16-bit mono; photos at 640x480 JPEG.
3. **Upload over WiFi** — the bracelet HTTP-POSTs the file to a FastAPI relay server on your local network.
4. **Transcribe and execute** — the relay runs Whisper (local, no API cost) to transcribe speech, then feeds the request to Claude Code.
5. **Get notified** — results are pushed to your phone via Telegram.

### Interaction Modes

| Input | Trigger | What happens |
|-------|---------|-------------|
| Voice command | Say **"Record"** → speak → say **"Stop"** (or 30 s timeout) | Audio recorded, uploaded, transcribed, executed |
| Photo | Say **"Capture"** | 3-second LED countdown, then JPEG captured and uploaded |
| Session | Buffer multiple audio/image captures, then say **"Post"** | Accumulated content sent as one batch to the AI |

## Why

AI tools are powerful but they chain you to a keyboard and screen. This project breaks that chain. It's about **freedom** — accessing the same AI capabilities from anywhere in your space, hands-free, eyes-free.

## Tech Stack

| Layer | Technology | Details |
|-------|-----------|---------|
| **MCU** | XIAO ESP32S3 Sense | 240 MHz dual-core, 8 MB flash, PSRAM, built-in mic + camera |
| **ML** | Edge Impulse / TFLite Micro | On-device keyword spotting (4 keywords + noise/unknown) |
| **Speech-to-Text** | faster-whisper | Runs locally on your laptop — no cloud API costs |
| **AI Execution** | Claude Code CLI | Subprocess with JSON stdin/stdout |
| **Relay Server** | Python / FastAPI | Async HTTP, session management, Telegram integration |
| **Notifications** | Telegram Bot API | Push results to your phone |
| **Firmware** | Arduino / PlatformIO | C++ with FreeRTOS on Core 1 for inference |

## Project Structure

```
OpenCharm/
├── firmware/                   ESP32S3 firmware (Arduino/PlatformIO)
│   ├── platformio.ini
│   ├── include/
│   │   └── config.h            WiFi, pin, ML threshold configuration
│   ├── lib/
│   │   └── ei-keyword-spotting/ Edge Impulse TFLite model library
│   └── src/
│       └── main.cpp            Main firmware: keywords, recording, camera, WiFi
│
├── relay/                      Python relay server
│   ├── relay_server.py         FastAPI server (audio upload, transcription, execution)
│   ├── claude_executor.py      Claude Code subprocess executor
│   ├── intent_parser.py        Voice command intent parsing
│   ├── requirements.txt
│   └── README.md
│
├── scripts/
│   ├── recording/              Serial audio recording utilities
│   │   ├── record_serial.py
│   │   ├── sample_recorder_local.py
│   │   └── recorder_firmware/
│   └── training/               ML training automation
│       ├── train_model.py      Automated Edge Impulse pipeline
│       ├── generate_tts_samples.py  Synthetic voice data via Google TTS
│       ├── clean_upload.py
│       └── requirements.txt
│
├── cad/                        3D-printable enclosure
│   └── V1/                     STL + STEP files (left/right halves)
│
├── docs/                       Documentation
│   ├── quick-start.md
│   ├── architecture.md
│   ├── network-architecture.md
│   ├── keyword-detection.md
│   ├── latency-analysis.md
│   ├── bom.md
│   └── ...
│
├── assets/                     Photos and media
│   └── V0.1_Prototype/
│
├── hermes-config/              Alternative AI integration notes
│
└── LICENSE                     MIT
```

## Quick Start

### Hardware

- 1x [Seeed XIAO ESP32S3 Sense](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)
- USB-C cable
- (Optional) 3D-printed enclosure from `cad/V1/`

See `docs/bom.md` for the full bill of materials.

### Firmware

```bash
# Install PlatformIO CLI (or use VS Code extension)
pip install platformio

# Configure WiFi and relay IP
# Edit firmware/include/config.h:
#   WIFI_SSID, WIFI_PASSWORD, RELAY_IP, RELAY_PORT

cd firmware
pio run --target upload
pio device monitor        # watch serial output
```

### Relay Server

```bash
cd relay
pip install -r requirements.txt

# Create .env with your Telegram bot token (optional)
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...

python relay_server.py
# Server starts on http://0.0.0.0:8080
```

### ML Model Training (Optional)

Only needed if you want to retrain the keyword detection model.

```bash
cd scripts/training
pip install -r requirements.txt

# Generate synthetic training samples
python generate_tts_samples.py

# Train on Edge Impulse and download the Arduino library
python train_model.py
```

See `docs/keyword-detection.md` for model details and threshold tuning.

## API Endpoints

The relay server exposes these endpoints for the bracelet:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/capture/audio` | POST | Upload and transcribe a WAV file |
| `/capture/image` | POST | Upload and analyze a JPEG |
| `/session/audio` | POST | Buffer audio in current session |
| `/session/image` | POST | Buffer image in current session |
| `/session/process` | POST | Process all buffered session content |
| `/session/execute` | POST | Send accumulated context to Claude Code |

## Status

Working prototype. Voice commands, photo capture, audio recording, AI execution, and Telegram feedback all functional. Actively iterating on ML model accuracy and form factor.

## License

[MIT](LICENSE)
