# Voice Activation Implementation
**Date:** March 19, 2026
**Status:** IMPLEMENTED

---

## What Was Built

Voice-activated audio recording using Edge Impulse pre-trained ON/OFF keyword detection.

**Flow:**
```
[Always Listening] → "ON" detected
                   → LED turns purple (wake feedback)
                   → LED turns orange (recording)
                   → User speaks message
                   → "OFF" detected
                   → Send to Relay Server
                   → Return to listening

[Button Fallback] → Still works (short=photo, long=audio)
```

---

## What Worked

| Component | Notes |
|-----------|-------|
| Edge Impulse pre-trained model | Used `reference-kws-off-on-50` from GitHub - no training needed |
| Wake word "ON" detection | Reliable, ~80% threshold works well |
| "OFF" keyword to stop | More reliable than VAD silence detection |
| Audio recording | 16kHz 16-bit mono, shares audio with inference |
| WiFi connection | Works WITH 2.4GHz antenna attached |
| HTTP POST to relay | Existing code reused, no changes needed |

## What Didn't Work

| Component | Issue | Solution |
|-----------|-------|----------|
| Edge Impulse project cloning | Cloned project had no trained model | Used pre-trained library from GitHub instead |
| ESP-NN TFLite acceleration | Linker errors with ESP-NN symbols | Disabled with `-DEI_CLASSIFIER_TFLITE_ENABLE_ESP_NN=0` |
| VAD silence detection | Unreliable - either too sensitive or not enough | Replaced with "OFF" keyword detection |
| WiFi without antenna | Status 6 (WRONG_PASSWORD) even with correct password | **Must use 2.4GHz antenna** - weak signal causes auth failure |

---

## Implementation Details

### Files Modified

**`firmware/include/config.h`** - Added wake word config:
```cpp
#define WAKE_WORD_THRESHOLD     0.8f
#define WAKE_INFERENCE_INTERVAL 100
#define LED_COLOR_WAKE_DETECTED 0x8800FF  // Purple
#define LED_COLOR_LISTENING     0xFF4400  // Orange
```

**`firmware/platformio.ini`** - Added Edge Impulse flags:
```ini
build_flags =
    -DEI_CLASSIFIER_ALLOCATION_STATIC
    -DEIDSP_USE_CMSIS_DSP=0
    -DEIDSP_LOAD_CMSIS_DSP_SOURCES=0
    -DEI_CLASSIFIER_TFLITE_ENABLE_ESP_NN=0

board_build.partitions = huge_app.csv
```

**`firmware/src/main.cpp`** - Added:
- Edge Impulse include and inference buffers
- `initWakeWord()` - initialize model
- `checkWakeWord()` - detect "ON" when idle
- `feedToInference()` - share recorded audio with inference
- `checkKeywordInference()` - detect "OFF" during recording
- Modified loop() with voice-triggered recording flow

**`firmware/lib/ei-keyword-spotting/`** - Pre-trained library:
- Model: `impulse_229721_1` (reference-kws-off-on-50)
- Keywords: "noise", "off", "on", "unknown"
- Input: 16kHz audio, ~1 second window

### Memory Usage
- RAM: 20.6% (67KB / 328KB)
- Flash: 32.3% (1015KB / 3146KB)

---

## Testing

**Start relay server:**
```bash
cd ~/Projects/OpenCharm/relay
source .venv/bin/activate
python relay_server.py
```

**Monitor bracelet:**
```bash
cd ~/Projects/OpenCharm/firmware
pio device monitor
```

**Test sequence:**
1. Wait for "Ready!" message
2. Say **"ON"** clearly → LED turns orange
3. Speak your message
4. Say **"OFF"** clearly → audio sends
5. Check Telegram for transcription + AI response

---

## Future Improvements

### Voice-Activated Camera (Options)

**Option A: Custom Edge Impulse Model**
1. Create Edge Impulse account
2. Record samples: "camera", "picture", "snap", "photo"
3. Train model (~15-30 min)
4. Export Arduino library
5. Add as second model, check for camera keywords in idle

**Option B: Intent Parsing on Relay**
1. After transcription, check if message contains camera intent
2. Relay sends response with `action: "camera"` flag
3. Bracelet polls or uses WebSocket for commands
4. More complex but more flexible

**Option C: Keyword Sequences**
- "ON CAMERA" triggers photo
- "ON" alone triggers audio recording
- Requires two-stage detection

### Other Ideas
- [ ] Custom wake word ("Hey Bracelet")
- [ ] Speaker verification (only respond to owner)
- [ ] Multiple language support
- [ ] Streaming to relay (real-time transcription)

---

## Troubleshooting

### WiFi Won't Connect (Status 6)
- **Cause:** Weak signal strength (< -75 dBm)
- **Fix:** Attach 2.4GHz antenna, move closer to router

### "ON" Not Detected
- Speak clearly and slightly louder
- Try adjusting `WAKE_WORD_THRESHOLD` (lower = more sensitive, more false positives)
- Check serial monitor for confidence values

### "OFF" Not Stopping Recording
- Wait for brief pause before saying "OFF"
- Say "OFF" distinctly (not "of" or "ah")
- Check serial for "Keyword detected: off" messages

### Build Errors with Edge Impulse
- Ensure `-DEI_CLASSIFIER_TFLITE_ENABLE_ESP_NN=0` in build_flags
- Use `huge_app.csv` partition scheme
- Clean build: `pio run -t clean && pio run`

---

## Lessons Learned

1. **Pre-trained models save time** - Edge Impulse public projects or GitHub libraries are faster than training from scratch

2. **VAD is tricky** - Silence detection with fixed thresholds doesn't work well in variable environments; keyword-based stop is more reliable

3. **WiFi antenna matters** - ESP32 internal antenna is weak; external 2.4GHz antenna dramatically improves reliability

4. **ESP-NN causes issues** - Disable TFLite ESP-NN acceleration to avoid linker errors on ESP32-S3

5. **Reuse existing code** - Kept button fallback, HTTP POST, relay server unchanged - only added ~100 lines for voice activation

---

*Implementation completed: March 19, 2026*
