# Spatial Bracelet Relay Server

Receives images and audio from the XIAO ESP32S3 bracelet via HTTP POST, converts audio to OGG/Opus, and forwards to Telegram where Hermes Agent processes them.

## Setup

### 1. Install Dependencies

```bash
cd relay
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Install FFmpeg (for audio conversion)

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

### 3. Create Telegram Bot

1. Open Telegram and message `@BotFather`
2. Send `/newbot`
3. Choose a name (e.g., "Spatial Bracelet")
4. Choose a username (e.g., `spatial_bracelet_bot`)
5. Copy the HTTP API token

### 4. Get Your Chat ID

1. Send any message to your new bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789}` in the JSON response
4. That number is your chat ID

### 5. Configure Environment

```bash
cp .env.example .env
# Edit .env with your token and chat ID
```

### 6. Run the Server

```bash
source .venv/bin/activate
python relay_server.py
```

Or with uvicorn directly:
```bash
uvicorn relay_server:app --host 0.0.0.0 --port 8080
```

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Status and last capture info |
| `/health` | GET | Health check for bracelet |
| `/capture/image` | POST | Receive JPEG, send to Telegram |
| `/capture/audio` | POST | Receive WAV, convert to OGG, send as voice |

## Testing with curl

### Test image upload:
```bash
curl -X POST http://localhost:8080/capture/image \
  -F "image=@test.jpg"
```

### Test audio upload:
```bash
curl -X POST http://localhost:8080/capture/audio \
  -F "audio=@test.wav"
```

## Bracelet Configuration

Set these in the bracelet firmware `config.h`:

```cpp
#define RELAY_IP    "192.168.x.x"  // Your laptop's IP
#define RELAY_PORT  8080
```

Find your IP with:
```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I
```
