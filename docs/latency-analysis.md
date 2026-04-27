# Latency Fix Implementation Plan

**Date:** March 21, 2026
**Goal:** Fix all 6 critical issues causing voice command lag

---

## ISSUE 1: 5-Second Blocking Health Check

### Problem
```cpp
// Current code (main.cpp:302-317)
if (millis() - lastHealthCheck >= HEALTH_CHECK_INTERVAL_MS) {
    HTTPClient http;
    http.begin(RELAY_URL_HEALTH);
    http.setTimeout(5000);  // BLOCKS FOR 5 SECONDS IF FAILS
    int httpCode = http.GET();
    // ...
}
```

### Solution
Make health check completely non-blocking using a state machine approach:
1. Reduce timeout to 500ms (still reasonable for local network)
2. Only attempt health check if WiFi is connected
3. Don't block on failure - just log and continue

### Implementation
```cpp
// Periodic health check - NON-BLOCKING
if (millis() - lastHealthCheck >= HEALTH_CHECK_INTERVAL_MS) {
    lastHealthCheck = millis();

    // Only check if WiFi is actually connected
    if (WiFi.status() == WL_CONNECTED) {
        HTTPClient http;
        http.begin(RELAY_URL_HEALTH);
        http.setTimeout(500);  // 500ms max - fast fail

        int httpCode = http.GET();
        if (httpCode == 200) {
            Serial.println("Health: OK");
        } else {
            Serial.printf("Health: %d (non-blocking)\n", httpCode);
        }
        http.end();
    }
}
```

---

## ISSUE 2: 2-Second Cooldown Blocks Everything

### Problem
```cpp
#define KEYWORD_COOLDOWN_MS 2000  // Too long!

// Applied to ALL detection
if (millis() - lastKeywordTime < KEYWORD_COOLDOWN_MS) {
    // Skip detection entirely for 2 seconds
}
```

### Solution
1. Reduce IDLE cooldown to 500ms (prevents double-triggers but allows quick response)
2. Separate cooldown for IDLE vs RECORDING states
3. During recording, NO cooldown for "OFF" detection - we want it immediately

### Implementation
```cpp
// In config.h
#define KEYWORD_COOLDOWN_IDLE_MS    500   // Cooldown in idle state (prevents double-trigger)
#define KEYWORD_COOLDOWN_RECORD_MS  0     // No cooldown during recording

// In main.cpp - idle detection
if (millis() - lastKeywordTime < KEYWORD_COOLDOWN_IDLE_MS) {
    // Flush buffer but skip detection
}

// In main.cpp - recording detection
// Remove cooldown check entirely - always check for "OFF"
int detected = checkKeywordInference();  // No cooldown needed
```

---

## ISSUE 3: 250ms Minimum Detection Latency

### Problem
Edge Impulse model requires 4000 samples (250ms) per inference slice. This is fundamental to the model architecture.

### Solution
We cannot reduce this without retraining the model, BUT we can:
1. Ensure we're reading audio as fast as possible
2. Use overlapping buffers efficiently (already using double-buffer)
3. Lower threshold slightly (0.75 instead of 0.8) to catch keywords earlier

### Implementation
```cpp
// In config.h - slightly more sensitive detection
#define WAKE_WORD_THRESHOLD     0.75f  // Was 0.8f - more responsive
```

---

## ISSUE 4: 3-Second Countdown Blocks Audio

### Problem
```cpp
void countdownLED(int seconds) {
    for (int i = seconds; i > 0; i--) {
        setLED(LED_COLOR_ERROR);
        delay(200);   // BLOCKING
        setLED(0);
        delay(800);   // BLOCKING - total 1 second per iteration
    }
}
```

The I2S DMA buffer is 8 * 1024 = 8192 samples = 512ms of audio.
During 3-second countdown, buffer overflows multiple times.

### Solution
Flush I2S buffer during countdown to prevent stale audio from affecting next detection:

```cpp
void countdownLED(int seconds) {
    Serial.printf("Countdown: %d seconds...\n", seconds);

    // Temp buffer for flushing audio during countdown
    int16_t flushBuffer[1024];

    for (int i = seconds; i > 0; i--) {
        Serial.printf("  %d...\n", i);

        // Flash red for 200ms (flush audio every 50ms)
        setLED(LED_COLOR_ERROR);
        for (int j = 0; j < 4; j++) {
            size_t bytesRead;
            i2s_read(I2S_PORT, flushBuffer, sizeof(flushBuffer), &bytesRead, 10);
            delay(50);
        }

        // LED off for 800ms (flush audio every 50ms)
        setLED(0);
        for (int j = 0; j < 16; j++) {
            size_t bytesRead;
            i2s_read(I2S_PORT, flushBuffer, sizeof(flushBuffer), &bytesRead, 10);
            delay(50);
        }
    }

    // Final flush and reset inference state before photo
    i2s_zero_dma_buffer(I2S_PORT);
    setLED(LED_COLOR_CAPTURING);
}
```

---

## ISSUE 5: 10ms Loop Delay

### Problem
```cpp
void loop() {
    // ... all processing ...
    delay(10);  // Unnecessary 10ms delay every loop
}
```

At 16kHz, 10ms = 160 samples potentially delayed.

### Solution
Remove the delay entirely. The I2S read operations provide natural pacing.
Use `yield()` if needed to let FreeRTOS handle background tasks.

```cpp
void loop() {
    // ... all processing ...
    yield();  // Allow FreeRTOS to run other tasks, but don't block
}
```

---

## ISSUE 6: Blocking I2S Read During Recording

### Problem
```cpp
// During recording (main.cpp:194-195)
i2s_read(I2S_PORT, audioBuffer + audioSamplesRecorded,
         samplesToRead * sizeof(int16_t), &bytesRead, portMAX_DELAY);
```

`portMAX_DELAY` = 0xFFFFFFFF ticks = potentially infinite wait.

### Solution
Use a reasonable timeout (20ms = ~320 samples at 16kHz):

```cpp
i2s_read(I2S_PORT, audioBuffer + audioSamplesRecorded,
         samplesToRead * sizeof(int16_t), &bytesRead, pdMS_TO_TICKS(20));
```

---

## IMPLEMENTATION ORDER

1. **Fix 5** (10ms delay) - Simplest, immediate improvement
2. **Fix 1** (health check) - Critical blocker
3. **Fix 6** (I2S timeout) - Prevents hangs
4. **Fix 2** (cooldown) - Major responsiveness improvement
5. **Fix 4** (countdown) - Prevents audio overflow
6. **Fix 3** (threshold) - Fine-tuning

---

## EXPECTED RESULTS

| Metric | Before | After |
|--------|--------|-------|
| Worst-case latency | 5+ seconds | ~500ms |
| "OFF" response after "ON" | 2+ seconds | ~300ms |
| Loop iteration time | 10ms+ | <1ms |
| Health check blocking | 5 seconds | 500ms max |
| Audio loss during countdown | Yes (3 sec) | No |

---

## TESTING CHECKLIST

- [ ] Say "ON" - should respond within 500ms
- [ ] Say "OFF" immediately after "ON" - should work within 300ms
- [ ] Say "OFF" for camera - countdown should work, photo should capture
- [ ] Disconnect server, verify no 5-second freezes
- [ ] Record audio for 10+ seconds, verify continuous recording
- [ ] Button short press - should still work
- [ ] Button long press - should still record audio
