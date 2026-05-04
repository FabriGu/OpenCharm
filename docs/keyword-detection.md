# OpenCharm - Keyword Detection Lag Analysis

**Date:** March 21, 2026
**Issue:** Voice commands have extreme lag and/or don't respond at all

---

## SYSTEM OVERVIEW

### Hardware
- **MCU:** XIAO ESP32S3 Sense (240MHz dual-core, 320KB RAM, 8MB Flash, PSRAM)
- **Microphone:** PDM microphone on GPIO 42 (CLK) and 41 (DATA)
- **Camera:** OV2640/OV5640 via PSRAM
- **LED:** NeoPixel on GPIO 44

### Software Stack
- **Framework:** Arduino on ESP-IDF via PlatformIO
- **ML Model:** Edge Impulse pre-trained "reference-kws-off-on-50"
  - 4 classes: `noise`, `off`, `on`, `unknown`
  - Input: 16kHz audio, 1 second window (16000 samples)
  - Inference: TFLite micro with 4 slices per window
- **Audio:** I2S PDM at 16kHz, 16-bit mono
- **Network:** WiFi HTTP POST to relay server

---

## EDGE IMPULSE MODEL PARAMETERS

From `model_metadata.h`:

```cpp
#define EI_CLASSIFIER_RAW_SAMPLE_COUNT           16000   // 1 second of audio
#define EI_CLASSIFIER_FREQUENCY                  16000   // 16kHz sample rate
#define EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW    4       // 4 inference windows per second
#define EI_CLASSIFIER_SLICE_SIZE                 (16000 / 4)  // = 4000 samples per slice
#define EI_CLASSIFIER_LABEL_COUNT                4       // noise, off, on, unknown
#define EI_CLASSIFIER_INTERVAL_MS                0.0625  // 62.5 microseconds per sample
```

**Key Insight:** The model uses a **sliding window** approach:
- Each inference needs 16000 samples (1 second)
- The window advances by `EI_CLASSIFIER_SLICE_SIZE` = 4000 samples (0.25 seconds)
- This means **4 inferences per second** maximum
- **Minimum latency:** 250ms to fill one slice, but often needs multiple slices

---

## THE PROBLEM: WHERE LAG COMES FROM

### 1. AUDIO BUFFER FILLING DELAY

**Code Location:** `main.cpp:928-953` (`checkKeywordInIdle()`)

```cpp
// Read audio samples for keyword detection
size_t bytesRead = 0;
i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 10);
// sizeof(wakeWordSampleBuffer) = 2048 * 2 = 4096 bytes = 2048 samples

// Fill inference buffer
for (size_t i = 0; i < samplesRead && inference.buf_count < inference.n_samples; i++) {
    inference.buffers[inference.buf_select][inference.buf_count++] = wakeWordSampleBuffer[i];
}

// Check if buffer is full
if (inference.buf_count >= inference.n_samples) {  // n_samples = 4000 (SLICE_SIZE)
    inference.buf_select ^= 1;
    inference.buf_count = 0;
    inference.buf_ready = 1;
}
```

**Analysis:**
- Each I2S read gets ~2048 samples (at 10ms timeout)
- Need 4000 samples to fill one slice buffer
- **Requires 2+ loop iterations just to fill one slice** (4000/2048 ≈ 2)
- At 16kHz, 4000 samples = 250ms
- **Minimum time to get first inference: ~250ms**

### 2. INFERENCE ONLY RUNS WHEN BUFFER IS READY

The inference only runs when `inference.buf_ready == 1`, which only happens after filling 4000 samples. If the user says "ON" but it doesn't align with the buffer boundary, the keyword gets split across two slices and detection fails or is delayed.

### 3. LOOP TIMING AND BLOCKING DELAYS

**Code Location:** `main.cpp:119-320` (`loop()`)

```cpp
void loop() {
    // Check WiFi... (potentially blocking)
    if (WiFi.status() != WL_CONNECTED) { ... }

    // Poll button (fast)
    updateButton();

    // Wake word detection (THE BOTTLENECK)
    if (!isRecording && !buttonDown && wakeWordEnabled && wakeWordInitialized) {
        // Cooldown check
        if (millis() - lastKeywordTime < KEYWORD_COOLDOWN_MS) {  // 2000ms cooldown!
            // Just flush buffer, no detection
            i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 10);
        } else {
            int detected = checkKeywordInIdle();  // <-- INFERENCE HAPPENS HERE
            // ...
        }
    }

    // DURING RECORDING (voice triggered)
    if (isRecording && voiceTriggered) {
        i2s_read(I2S_PORT, audioBuffer + ..., ..., &bytesRead, portMAX_DELAY);  // BLOCKING!
        // ...
        feedToInference(scaledChunk, newSamples);
        int detected = checkKeywordInference();  // Only runs when buf_ready
    }

    // Health check every 30 seconds (blocks for 5 seconds if server down)
    if (millis() - lastHealthCheck >= 30000) {
        HTTPClient http;
        http.begin(RELAY_URL_HEALTH);
        http.setTimeout(5000);  // <-- BLOCKS FOR 5 SECONDS IF FAILS
        int httpCode = http.GET();
    }

    delay(10);  // 10ms delay every loop
}
```

**Problems Identified:**

1. **2-second cooldown after detection** (`KEYWORD_COOLDOWN_MS = 2000`)
   - After saying "ON", no detection happens for 2 seconds
   - This means you can't say "OFF" until 2 seconds after "ON" is detected

2. **`portMAX_DELAY` on I2S read during recording** (line 195)
   - This blocks indefinitely until audio is available
   - Should use a timeout instead

3. **Health check blocks for 5 seconds** on failure
   - If server is unreachable, loop freezes for 5 seconds every 30 seconds

4. **10ms delay every loop**
   - Adds minimum 10ms latency to every iteration
   - At 16kHz, this misses ~160 samples per loop

### 4. THE INFERENCE TIMING PROBLEM

From `checkKeywordInIdle()`:

```cpp
// Only run inference if buffer is ready
if (inference.buf_ready) {
    inference.buf_ready = 0;

    signal_t signal;
    signal.total_length = EI_CLASSIFIER_SLICE_SIZE;  // 4000 samples
    signal.get_data = &microphone_audio_signal_get_data;

    ei_impulse_result_t result = { 0 };
    EI_IMPULSE_ERROR err = run_classifier_continuous(&impulse_229721_1, &signal, &result, false);
    // ... check results
}

return -1;  // Return -1 if buffer not ready (most of the time!)
```

**Issue:** Most calls to `checkKeywordInIdle()` return -1 immediately because the buffer isn't ready yet. The inference only happens every ~250ms when a slice is complete.

### 5. DOUBLE-BUFFER CONFUSION

The microphone data callback reads from `inference.buffers[inference.buf_select ^ 1]`:

```cpp
static int microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr) {
    numpy::int16_to_float(&inference.buffers[inference.buf_select ^ 1][offset], out_ptr, length);
    return 0;
}
```

**Problem:** This reads from the "other" buffer (XOR with 1), which should be the completed one. But if the buffer state isn't properly managed, it may read stale or partial data.

---

## IDENTIFIED BUGS

### Bug 1: Cooldown Too Long for "OFF" Detection During Recording

When "ON" is detected:
1. `lastKeywordTime = millis()` is set
2. During recording, the same 2-second cooldown applies
3. But `checkKeywordInference()` doesn't have a cooldown check

**However**, after `resetInferenceBuffers()` is called, the buffer needs to refill before detection can happen again. At 4000 samples per slice, this takes ~250ms minimum.

### Bug 2: Blocking I2S Read During Recording

```cpp
i2s_read(I2S_PORT, audioBuffer + audioSamplesRecorded,
         samplesToRead * sizeof(int16_t), &bytesRead, portMAX_DELAY);
```

Using `portMAX_DELAY` (0xFFFFFFFF ticks) blocks until data is available. If the I2S buffer is empty, this hangs. Should use a reasonable timeout.

### Bug 3: 10ms Loop Delay Causes Audio Underrun

At 16kHz, the I2S DMA buffer fills at 16000 samples/second = 16 samples/ms.

The I2S config is:
```cpp
.dma_buf_count = 8,
.dma_buf_len = 1024,
```

Total DMA buffer: 8 * 1024 = 8192 samples = 512ms of audio

The 10ms delay should be fine, but combined with the blocking health check (5 seconds!) and other delays, the DMA buffer can overflow, causing lost audio.

### Bug 4: Inference Result Only Checked Once Per Slice

The model outputs confidence scores, but they're only checked when a full slice (4000 samples) is ready. If the keyword spans the boundary between two slices, the confidence may be split and neither slice passes the 0.8 threshold.

### Bug 5: No Audio During Countdown

When "OFF" is detected for camera:
```cpp
countdownLED(3);  // Blocks for 3 seconds!
```

During this 3-second countdown, no audio is being read. The I2S DMA buffer overflows and audio is lost.

---

## TIMING ANALYSIS

### Best Case (Everything Works)

1. User says "ON" (0ms)
2. Audio travels through PDM → I2S → DMA buffer (negligible)
3. Loop reads ~2048 samples (10ms timeout, may need 2 reads = 20ms)
4. Buffer fills to 4000 samples (~250ms total from start of speech)
5. Inference runs (~20-50ms on ESP32)
6. Detection threshold check passes
7. `resetInferenceBuffers()` called (~1ms, flushes DMA)
8. State changes to recording (~300ms total from "ON" spoken)

**Best case latency: ~300ms** (noticeable but acceptable)

### Worst Case (Where Problems Occur)

1. User says "ON" (0ms)
2. Health check starts (5000ms blocking!)
3. Loop resumes, DMA buffer has overflowed, audio lost
4. "ON" not detected because critical audio is missing
5. User says "ON" again (5000ms later)
6. Now in cooldown from partial detection? Or stale buffer data?
7. Eventually detected at ~5500ms

**Worst case latency: 5+ seconds** (unacceptable)

### Recording Flow Issues

1. "ON" detected, recording starts (0ms)
2. Recording for ~2 seconds, user says "OFF" (2000ms)
3. Cooldown from "ON" still active for 0ms more (cooldown = 2000ms)
4. But during recording, cooldown doesn't apply to `checkKeywordInference()`
5. However, `feedToInference()` is only called with scaled audio
6. Buffer fills slowly because we're reading 1024 samples at a time
7. 4000 samples needed = 4 reads minimum
8. At 64ms per read (1024 samples / 16kHz), that's 256ms per inference
9. "OFF" detected after ~256-512ms from when spoken

**Recording "OFF" detection latency: 250-500ms** (noticeable)

---

## ROOT CAUSES SUMMARY

| Issue | Impact | Severity |
|-------|--------|----------|
| 5-second blocking health check | Completely freezes detection | **CRITICAL** |
| 2-second cooldown after detection | Can't detect commands quickly | HIGH |
| 10ms loop delay | Adds latency, risks buffer underrun | MEDIUM |
| `portMAX_DELAY` on I2S read | Potential infinite block | MEDIUM |
| 3-second countdown blocks loop | Audio lost during countdown | MEDIUM |
| 4000 sample slice requirement | Minimum 250ms detection latency | LOW (by design) |

---

## POTENTIAL FIXES

### Fix 1: Make Health Check Non-Blocking

```cpp
// Don't block the main loop
// Option A: Use async/task
// Option B: Much shorter timeout
http.setTimeout(500);  // 500ms instead of 5000ms
```

### Fix 2: Reduce or Remove Cooldown During Recording

The cooldown prevents double-triggers in IDLE state, but during recording we need to detect "OFF" quickly. The cooldown should only apply to idle detection.

### Fix 3: Remove 10ms Loop Delay

```cpp
// delay(10);  // Remove this
// Instead, use yield() to let other tasks run
yield();
```

### Fix 4: Use Timeout for I2S Read

```cpp
// Instead of portMAX_DELAY
i2s_read(I2S_PORT, audioBuffer + ..., ..., &bytesRead, pdMS_TO_TICKS(20));
```

### Fix 5: Continue Audio Capture During Countdown

```cpp
void countdownLED(int seconds) {
    for (int i = seconds; i > 0; i--) {
        setLED(LED_COLOR_ERROR);
        // Keep reading audio to prevent DMA overflow
        size_t bytesRead;
        i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 10);
        delay(200);
        setLED(0);
        i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 10);
        delay(800);
    }
    setLED(LED_COLOR_CAPTURING);
}
```

### Fix 6: Consider Using Interrupt-Based Audio

Instead of polling I2S in the main loop, use an I2S event callback or a separate FreeRTOS task:

```cpp
// Create dedicated audio task
xTaskCreatePinnedToCore(audioTask, "audio", 4096, NULL, 1, NULL, 0);

void audioTask(void *param) {
    while (true) {
        size_t bytesRead;
        i2s_read(I2S_PORT, buffer, sizeof(buffer), &bytesRead, portMAX_DELAY);
        // Push to queue for main task
        xQueueSend(audioQueue, buffer, 0);
    }
}
```

---

## FILES TO MODIFY

1. **`firmware/src/main.cpp`** - Main firmware with all the issues
2. **`firmware/include/config.h`** - Configuration constants

---

## COMPLETE SOURCE CODE FOLLOWS

### firmware/include/config.h

```cpp
#ifndef CONFIG_H
#define CONFIG_H

// =============================================================================
// WiFi Configuration
// =============================================================================
#define WIFI_SSID "Fios-K2sFJ"
#define WIFI_PASSWORD "mop43coat89corn"

// =============================================================================
// Relay Server Configuration
// =============================================================================
#define RELAY_IP        "192.168.1.181"
#define RELAY_PORT      8080
#define RELAY_URL_IMAGE  "http://" RELAY_IP ":8080/capture/image"
#define RELAY_URL_AUDIO  "http://" RELAY_IP ":8080/capture/audio"
#define RELAY_URL_HEALTH "http://" RELAY_IP ":8080/health"

// =============================================================================
// Camera Configuration (XIAO ESP32S3 Sense OV2640)
// =============================================================================
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     10
#define SIOD_GPIO_NUM     40
#define SIOC_GPIO_NUM     39
#define Y9_GPIO_NUM       48
#define Y8_GPIO_NUM       11
#define Y7_GPIO_NUM       12
#define Y6_GPIO_NUM       14
#define Y5_GPIO_NUM       16
#define Y4_GPIO_NUM       18
#define Y3_GPIO_NUM       17
#define Y2_GPIO_NUM       15
#define VSYNC_GPIO_NUM    38
#define HREF_GPIO_NUM     47
#define PCLK_GPIO_NUM     13

#define CAMERA_FRAME_SIZE   FRAMESIZE_VGA    // 640x480
#define CAMERA_JPEG_QUALITY 12               // 0-63, lower = better

// =============================================================================
// Audio Configuration (PDM Microphone)
// =============================================================================
#define PDM_CLK_PIN       42
#define PDM_DATA_PIN      41
#define AUDIO_SAMPLE_RATE     16000   // 16kHz for Whisper
#define AUDIO_BITS_PER_SAMPLE 16
#define AUDIO_CHANNELS        1       // Mono
#define MAX_RECORDING_SECONDS 30

// =============================================================================
// Button Configuration
// =============================================================================
#define BUTTON_PIN        1           // GPIO1, active LOW
#define DEBOUNCE_MS       50
#define LONG_PRESS_MS     1000

// =============================================================================
// LED Configuration (NeoPixel)
// =============================================================================
#define LED_PIN           44
#define NUM_LEDS          1
#define LED_COLOR_IDLE      0x000011  // Dim blue
#define LED_COLOR_CONNECTING 0x110011 // Purple
#define LED_COLOR_CAPTURING  0xFFFFFF // White
#define LED_COLOR_SENDING    0x0000FF // Blue
#define LED_COLOR_SUCCESS    0x00FF00 // Green
#define LED_COLOR_ERROR      0xFF0000 // Red
#define LED_COLOR_RECORDING  0xFF0000 // Red breathing
#define LED_COLOR_WAKE_DETECTED 0x8800FF  // Purple
#define LED_COLOR_LISTENING     0xFF4400  // Orange

// =============================================================================
// Timeouts
// =============================================================================
#define WIFI_CONNECT_TIMEOUT_MS  15000
#define HTTP_TIMEOUT_MS          30000
#define HEALTH_CHECK_INTERVAL_MS 30000

// =============================================================================
// Wake Word Configuration
// =============================================================================
#define WAKE_WORD_THRESHOLD     0.8f    // 0.0-1.0 confidence
#define WAKE_INFERENCE_INTERVAL 100     // ms between inference

// =============================================================================
// VAD (unused but kept for reference)
// =============================================================================
#define VAD_SILENCE_THRESHOLD   500
#define VAD_SILENCE_MS          1500
#define VAD_MIN_RECORDING_MS    1000

#endif // CONFIG_H
```

### firmware/platformio.ini

```ini
[env:seeed_xiao_esp32s3]
platform = espressif32
board = seeed_xiao_esp32s3
framework = arduino

build_flags =
    -DBOARD_HAS_PSRAM
    -DARDUINO_USB_CDC_ON_BOOT=1
    -DARDUINO_USB_MODE=1
    -DEI_CLASSIFIER_ALLOCATION_STATIC
    -DEIDSP_USE_CMSIS_DSP=0
    -DEIDSP_LOAD_CMSIS_DSP_SOURCES=0
    -DEI_CLASSIFIER_TFLITE_ENABLE_ESP_NN=0

lib_deps =
    adafruit/Adafruit NeoPixel@^1.12.0

monitor_speed = 115200
monitor_filters = esp32_exception_decoder
upload_speed = 921600
board_build.partitions = huge_app.csv
```

### Edge Impulse Model Info

- **Project:** reference-kws-off-on-50 (ID: 229721)
- **Classes:** noise, off, on, unknown
- **Input:** 16000 samples (1 second @ 16kHz)
- **Slices per window:** 4 (inference every 4000 samples = 250ms)
- **Inference engine:** TFLite Micro (quantized INT8)

---

## QUESTIONS TO INVESTIGATE

1. What is the actual inference time on the ESP32S3?
2. Is the PDM microphone gain (`* 8` scaling) appropriate?
3. Should we use a lower confidence threshold (0.7 instead of 0.8)?
4. Would reducing `EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW` help?
5. Is the Edge Impulse model trained on similar microphone characteristics?

---

## RECOMMENDED NEXT STEPS

1. **Add timing logs** to measure actual detection latency
2. **Remove/reduce health check blocking** - most impactful fix
3. **Remove 10ms loop delay** - quick win
4. **Consider FreeRTOS audio task** - better architecture but more work
5. **Test with lower threshold (0.7)** - may improve responsiveness
