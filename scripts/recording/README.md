# ESP32 Keyword Sample Recorder

Record training samples directly through your ESP32's microphone for better keyword detection accuracy.

## Quick Start

### 1. Find your computer's IP address

```bash
# On Mac:
ipconfig getifaddr en0
```

### 2. Update the firmware with your IP

Edit `recorder_firmware/src/main.cpp`:
```cpp
#define SERVER_IP       "YOUR_IP_HERE"  // e.g., "192.168.1.181"
```

Also verify WiFi credentials match your network.

### 3. Flash the recorder firmware

```bash
cd recorder_firmware
pio run -t upload
```

### 4. Start the recording server

```bash
# In another terminal:
python sample_server.py
```

### 5. Record samples

1. Press the **button** on ESP32
2. LED turns **RED** - say the keyword clearly
3. **GREEN flash** = sample saved
4. Repeat until all samples collected

## How it works

- ESP32 records 2.5 seconds of audio when you press the button
- Audio is sent to your computer over WiFi
- The Python server automatically:
  - Detects where your voice is in the recording (VAD)
  - Centers the word in a perfect 1-second window
  - Saves it with the correct filename
- No timing pressure on you!

## After recording

Samples are saved to:
```
scripts/training/samples/
  record/record_real_001.wav, record_real_002.wav, ...
  stop/stop_real_001.wav, ...
  capture/capture_real_001.wav, ...
  post/post_real_001.wav, ...
```

Then:
1. Go to Edge Impulse Studio
2. Delete old samples (or keep some TTS for variety)
3. Upload the new real samples
4. Retrain the model
5. Download and flash the new Arduino library
