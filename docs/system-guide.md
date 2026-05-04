# OpenCharm System Guide
**Date:** March 16, 2026
**Status:** Working MVP - Image & Audio capture with AI analysis

---

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Quickstart](#quickstart)
4. [Hardware Setup](#hardware-setup)
5. [Software Components](#software-components)
6. [Usage](#usage)
7. [Configuration](#configuration)
8. [Troubleshooting](#troubleshooting)
9. [Future Improvements](#future-improvements)
10. [Continuation Prompt](#continuation-prompt)

---

## Overview

OpenCharm is a wearable AI assistant built on the XIAO ESP32S3 Sense. It captures images and audio from your physical workspace and sends them to an AI for analysis, with responses delivered via Telegram.

**Key Features:**
- **Image capture**: Quick button press takes a photo, AI analyzes it
- **Voice commands**: Hold button to record, release to transcribe and get AI response
- **Free AI backend**: Uses school Ollama server (LLaVA for vision, Llama 3.2 for text)
- **Local transcription**: Faster-whisper runs on your laptop (no API costs)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           OPENCHARM                                  │
│                     (XIAO ESP32S3 Sense)                            │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │  Camera  │  │   Mic    │  │  Button  │  │   LED    │            │
│  │ OV2640   │  │   PDM    │  │  GPIO1   │  │ NeoPixel │            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘            │
│       │             │             │             │                    │
│       └─────────────┴─────────────┴─────────────┘                    │
│                           │                                          │
│                    WiFi HTTP POST                                    │
└───────────────────────────┼──────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      RELAY SERVER                                    │
│                  (FastAPI on Laptop)                                │
│                   localhost:8080                                    │
│                                                                      │
│  POST /capture/image ──────────────────────────────────────────┐    │
│       │                                                         │    │
│       ├─► Send image to Telegram                               │    │
│       ├─► Send to Ollama LLaVA (vision analysis)               │    │
│       └─► Send AI response to Telegram                         │    │
│                                                                      │
│  POST /capture/audio ──────────────────────────────────────────┐    │
│       │                                                         │    │
│       ├─► Transcribe with Whisper (local)                      │    │
│       ├─► Convert WAV → OGG (ffmpeg)                           │    │
│       ├─► Send voice message to Telegram                       │    │
│       ├─► Send "You said: [transcription]" to Telegram         │    │
│       ├─► Send to Ollama Llama 3.2 (text understanding)        │    │
│       └─► Send AI response to Telegram                         │    │
└─────────────────────────────────────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
┌─────────────────────┐      ┌─────────────────────────────┐
│      TELEGRAM       │      │     SCHOOL OLLAMA           │
│   @spatial_bot      │      │  itp-ml.itp.tsoa.nyu.edu    │
│                     │      │                             │
│  - Receives images  │      │  Models:                    │
│  - Receives voice   │      │  - llava (vision)           │
│  - Shows AI replies │      │  - llama3.2 (text)          │
└─────────────────────┘      └─────────────────────────────┘
```

---

## Quickstart

### Prerequisites
- XIAO ESP32S3 Sense with camera module
- macOS/Linux laptop on same WiFi network
- Telegram account
- Python 3.10+
- PlatformIO (`brew install platformio`)
- ffmpeg (`brew install ffmpeg`)

### 1. Start the Relay Server

```bash
cd ~/Projects/OpenCharm/relay

# First time setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure (edit .env with your Telegram credentials)
cp .env.example .env
nano .env

# Run
python relay_server.py
```

### 2. Flash the Firmware

```bash
cd ~/Projects/OpenCharm/firmware

# Update config.h with your WiFi credentials and relay IP
nano include/config.h

# Flash
pio run -t upload

# Monitor serial output
pio device monitor
```

### 3. Use the Bracelet

| Action | Result |
|--------|--------|
| **Quick tap** (<1 sec) | Captures photo → AI analyzes → Telegram |
| **Hold** (>1 sec) | Records audio (red LED breathes) |
| **Release** (after hold) | Stops recording → Whisper transcribes → AI responds → Telegram |

---

## Hardware Setup

### Components
- **XIAO ESP32S3 Sense** - Main MCU with camera connector
- **OV2640 Camera Module** - Included with Sense expansion board
- **PDM Microphone** - Built into Sense expansion board
- **Button** - Connected to GPIO1 (active LOW with internal pullup)
- **NeoPixel LED** - Connected to GPIO44

### Pin Configuration (defined in `config.h`)

| Function | GPIO |
|----------|------|
| Button | 1 |
| LED (NeoPixel) | 44 |
| PDM Clock | 42 |
| PDM Data | 41 |
| Camera XCLK | 10 |
| Camera SIOD | 40 |
| Camera SIOC | 39 |

---

## Software Components

### 1. Firmware (`firmware/`)
- `platformio.ini` - Build configuration
- `include/config.h` - WiFi, relay, pin definitions
- `src/main.cpp` - Main application logic

### 2. Relay Server (`relay/`)
- `relay_server.py` - FastAPI server
- `requirements.txt` - Python dependencies
- `.env` - Telegram credentials (not in git)

### 3. Configuration Files
- `~/.hermes/SOUL.md` - AI persona (used by Hermes, optional)

---

## Usage

### LED Feedback

| Color | Meaning |
|-------|---------|
| Dim Blue | Idle, ready |
| Purple (pulsing) | Connecting to WiFi |
| White flash | Capturing image |
| Red (breathing) | Recording audio |
| Blue | Sending data |
| Green (2 flashes) | Success |
| Red (3 flashes) | Error |

### Serial Monitor Commands

Watch the serial output for debugging:
```bash
pio device monitor
```

Example output:
```
=================================
OpenCharm - WiFi Firmware
=================================
Scanning for networks...
Found 5 networks:
  1: sandbox370 (-45 dBm) <-- TARGET
  2: neighbors_wifi (-72 dBm)
Connecting to sandbox370...
WiFi connected!
IP address: 10.20.28.100
Ready!
  - Short press: capture image
  - Long press (hold): record audio

Button pressed
Button released after 350 ms
Short press -> capturing image
Captured image: 640x480, 45230 bytes
Sending to relay server...
HTTP response code: 200
```

---

## Configuration

### Firmware Config (`include/config.h`)

```cpp
// WiFi
#define WIFI_SSID       "your_ssid"
#define WIFI_PASSWORD   "your_password"

// Relay Server
#define RELAY_IP        "10.20.28.166"  // Your laptop's IP
#define RELAY_PORT      8080

// Timing
#define LONG_PRESS_MS     1000   // 1 second to trigger recording
#define MAX_RECORDING_SECONDS 30 // Max audio length
```

### Relay Server Config (`.env`)

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Optional: Override defaults
OLLAMA_BASE_URL=http://itp-ml.itp.tsoa.nyu.edu:11434
OLLAMA_MODEL=llava
WHISPER_MODEL_SIZE=base  # tiny, base, small, medium, large-v3
```

### Finding Your Laptop IP

```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I | awk '{print $1}'
```

---

## Troubleshooting

### WiFi Connection Issues

**Symptom:** "WiFi connection timeout!"

**Check:**
1. Is the network 2.4GHz? (ESP32 doesn't support 5GHz)
2. Is the password correct? (Check serial output for status codes)
3. Is the network in range? (Serial shows scan results)

**Status codes:**
- `1` = Network not found
- `4` = Connection failed
- `6` = Wrong password

### Relay Server Issues

**Symptom:** "HTTP POST failed"

**Check:**
1. Is relay server running? (`curl http://localhost:8080/health`)
2. Is firewall blocking port 8080?
3. Is the IP in config.h correct?

### Audio Not Transcribing

**Symptom:** "(no speech detected)"

**Check:**
1. Is the microphone working? (Check for audio artifacts)
2. Is Whisper model downloaded? (First run downloads ~150MB)
3. Is recording too short? (Needs at least 0.5 sec of speech)

### Ollama Errors

**Symptom:** "Ollama error: 404" or timeout

**Check:**
1. Is school VPN connected?
2. Is Ollama server reachable? (`curl http://itp-ml.itp.tsoa.nyu.edu:11434/api/tags`)
3. Are the models available? (llava, llama3.2)

---

## Future Improvements

### Short-term
- [ ] **Hermes Agent integration** - Add conversational memory and context
- [ ] **TTS response** - Speak AI responses back through phone/earbuds
- [ ] **Battery optimization** - Deep sleep between captures
- [ ] **OTA updates** - Update firmware over WiFi

### Medium-term
- [ ] **Multi-modal queries** - Send image + voice together ("what's wrong with this circuit?")
- [ ] **Project context** - Track ongoing projects, reference previous captures
- [ ] **Offline mode** - Queue captures when WiFi unavailable
- [ ] **Gesture recognition** - IMU-based gestures for additional commands

### Long-term
- [ ] **Custom wake word** - "Hey Bracelet" activation
- [ ] **On-device inference** - TinyML for simple classifications
- [ ] **Mesh networking** - Multiple bracelets sharing context
- [ ] **AR glasses integration** - Display AI responses in view

### Known Issues
- Audio quality depends on ambient noise (consider adding noise reduction)
- Long recordings (>15s) may timeout on slow connections
- School Ollama may be unavailable outside campus (add fallback to OpenRouter)

---

## Continuation Prompt

Use this prompt to continue development in a future session:

---

**CONTINUATION PROMPT:**

```
I'm continuing work on the OpenCharm project. Here's the current state:

## Working System
- XIAO ESP32S3 Sense with camera + PDM microphone
- WiFi HTTP POST to relay server (FastAPI)
- Image capture → LLaVA analysis → Telegram
- Audio capture → Whisper transcription → Llama 3.2 response → Telegram
- Button: tap for photo, hold for audio

## File Structure
- firmware/ - PlatformIO project (config.h, main.cpp)
- relay/ - FastAPI server (relay_server.py, .env)
- docs/ - Documentation
- ml/ - ML training and sample recording scripts

## AI Backend
- School Ollama: http://itp-ml.itp.tsoa.nyu.edu:11434
- Models: llava (vision), llama3.2 (text)
- Local Whisper: faster-whisper base model

## Pending Tasks
1. Integrate Hermes Agent for conversational memory
2. Add TTS for spoken responses
3. Battery optimization with deep sleep
4. Multi-modal queries (image + voice together)

## Key Files to Review
- firmware/include/config.h - Hardware and network config
- firmware/src/main.cpp - Firmware logic
- relay/relay_server.py - Server and AI integration
- docs/SYSTEM_GUIDE_2026-03-16.md - This documentation

What would you like to work on?
```

---

## Appendix: API Reference

### Relay Server Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Status and last capture info |
| `/health` | GET | Health check (returns `{"status": "ok"}`) |
| `/capture/image` | POST | Upload JPEG, analyze, send to Telegram |
| `/capture/audio` | POST | Upload WAV, transcribe, analyze, send to Telegram |

### POST /capture/image

**Request:**
```
Content-Type: multipart/form-data
Field: image (JPEG file)
Optional: prompt (string) - Custom analysis prompt
```

**Response:**
```json
{
  "status": "ok",
  "message_id": 123,
  "analysis": "I see a circuit diagram with..."
}
```

### POST /capture/audio

**Request:**
```
Content-Type: multipart/form-data
Field: audio (WAV file, 16kHz 16-bit mono)
```

**Response:**
```json
{
  "status": "ok",
  "message_id": 124,
  "transcription": "What does this circuit do?",
  "response": "This appears to be a voltage divider..."
}
```

---

*Last updated: March 16, 2026*
