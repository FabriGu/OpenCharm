# Firmware Changes for RECORD/STOP/SNAP/SEND Keywords

This document describes the changes needed in `firmware/src/main.cpp` after training the new Edge Impulse model.

## Overview

**Current keywords:** ON, OFF
**New keywords:** RECORD, STOP, SNAP, SEND

## Step 1: Update config.h URLs

Add the new session endpoints:

```cpp
// In firmware/include/config.h

// Existing endpoints
#define RELAY_URL_IMAGE  "http://" RELAY_IP ":8080/capture/image"
#define RELAY_URL_AUDIO  "http://" RELAY_IP ":8080/capture/audio"
#define RELAY_URL_HEALTH "http://" RELAY_IP ":8080/health"

// NEW: Session endpoints (for RECORD/STOP/SNAP/SEND workflow)
#define RELAY_URL_SESSION_AUDIO   "http://" RELAY_IP ":8080/session/audio"
#define RELAY_URL_SESSION_IMAGE   "http://" RELAY_IP ":8080/session/image"
#define RELAY_URL_SESSION_PROCESS "http://" RELAY_IP ":8080/session/process"
```

## Step 2: Update Keyword Indices

After training, check your model's output labels. Update `main.cpp`:

```cpp
// Near the top of main.cpp, after includes
// UPDATE THESE BASED ON YOUR MODEL'S LABEL ORDER!
// Check in Edge Impulse Studio -> Impulse Design -> Classification -> Labels

#define KW_NOISE    0   // Background noise
#define KW_RECORD   1   // "record" - start recording
#define KW_SEND     2   // "send" - process session
#define KW_SNAP     3   // "snap" - take photo
#define KW_STOP     4   // "stop" - stop recording
#define KW_UNKNOWN  5   // Unknown words
```

## Step 3: Update Keyword Detection Logic (main.cpp loop)

Replace the ON/OFF detection with RECORD/STOP/SNAP/SEND:

```cpp
// In loop(), replace the wake word detection section:

// OLD CODE:
// if (detected == 2) {  // "on" detected
//     ... start voice recording ...
// }
// else if (detected == 1) {  // "off" detected
//     ... take photo ...
// }

// NEW CODE:
if (detected == KW_RECORD) {
    Serial.println(">>> 'RECORD' DETECTED! Starting audio recording...");
    lastKeywordTime = millis();
    resetInferenceBuffers();

    voiceTriggered = true;
    isRecording = true;
    audioSamplesRecorded = 0;
    voiceRecordingStart = millis();
    lastSpeechTime = millis();
    setLED(LED_COLOR_WAKE_DETECTED);
    delay(200);
    setLED(LED_COLOR_LISTENING);
}
else if (detected == KW_SNAP) {
    Serial.println(">>> 'SNAP' DETECTED! Starting photo countdown...");
    lastKeywordTime = millis();
    resetInferenceBuffers();

    // Use session endpoint instead of capture endpoint
    // Start countdown state machine
    photoState = PHOTO_STATE_COUNTDOWN;
    photoCountdownValue = 3;
    photoCountdownLastTick = millis();
    photoPending = true;
    // NOTE: Will need to modify captureAndSendImage() to use session endpoint
}
else if (detected == KW_SEND) {
    Serial.println(">>> 'SEND' DETECTED! Processing session...");
    lastKeywordTime = millis();
    resetInferenceBuffers();

    // Call the process endpoint
    setLED(LED_COLOR_SENDING);
    if (sendProcessRequest()) {
        flashLED(LED_COLOR_SUCCESS, 2, 150);
    } else {
        flashLED(LED_COLOR_ERROR, 3, 200);
    }
    setLED(LED_COLOR_IDLE);
}
```

## Step 4: Update Recording Stop Logic

Replace OFF detection during recording with STOP:

```cpp
// In the voice-triggered recording section:

// OLD CODE:
// if (detected == 1) {  // 1 = "off"
//     ... stop and send ...

// NEW CODE:
if (detected == KW_STOP) {
    Serial.println(">>> 'STOP' keyword detected! Stopping recording...");
    isRecording = false;
    voiceTriggered = false;
    resetInferenceBuffers();

    Serial.printf("Voice recording complete: %d samples (%.1f sec)\n",
                 audioSamplesRecorded,
                 (float)audioSamplesRecorded / AUDIO_SAMPLE_RATE);

    // Send to SESSION endpoint (not capture endpoint)
    setLED(LED_COLOR_SENDING);
    if (sendAudioToSession()) {  // NEW FUNCTION
        flashLED(LED_COLOR_SUCCESS, 2, 150);
    } else {
        flashLED(LED_COLOR_ERROR, 3, 200);
    }
    setLED(LED_COLOR_IDLE);
    lastKeywordTime = millis();
}
```

## Step 5: Add New HTTP Functions

Add these new functions for session endpoints:

```cpp
// Send audio to session buffer (STOP keyword)
bool sendAudioToSession() {
    // Similar to recordAndSendAudio() but uses RELAY_URL_SESSION_AUDIO
    // Does NOT trigger AI processing

    HTTPClient http;
    http.begin(RELAY_URL_SESSION_AUDIO);
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.addHeader("Content-Type", "audio/wav");

    // Create WAV header + audio data (same as existing)
    // ... existing WAV creation code ...

    int httpCode = http.POST(wavBuffer, wavSize);
    http.end();

    return (httpCode == 200);
}

// Send image to session buffer (SNAP keyword)
bool sendImageToSession() {
    // Similar to captureAndSendImage() but uses RELAY_URL_SESSION_IMAGE
    // Does NOT trigger AI processing

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) return false;

    HTTPClient http;
    http.begin(RELAY_URL_SESSION_IMAGE);
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.addHeader("Content-Type", "image/jpeg");

    int httpCode = http.POST(fb->buf, fb->len);
    esp_camera_fb_return(fb);
    http.end();

    return (httpCode == 200);
}

// Trigger session processing (SEND keyword)
bool sendProcessRequest() {
    HTTPClient http;
    http.begin(RELAY_URL_SESSION_PROCESS);
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.addHeader("Content-Type", "application/json");

    int httpCode = http.POST("{}");
    http.end();

    return (httpCode == 200);
}
```

## Step 6: Update Photo State Machine

Modify `handlePhotoStateMachine()` to use session endpoint:

```cpp
// In handlePhotoStateMachine(), PHOTO_STATE_CAPTURING case:

// OLD:
// if (captureAndSendImage()) {

// NEW:
if (sendImageToSession()) {  // Use session endpoint
    Serial.println("Photo sent to session buffer");
    flashLEDOnce(LED_COLOR_SUCCESS, 200);
} else {
    Serial.println("Photo failed to send");
    flashLEDOnce(LED_COLOR_ERROR, 500);
}
```

## Step 7: Update Edge Impulse Include

Update the include statement if the library name changed:

```cpp
// OLD:
#include <reference-kws-off-on-50_inferencing.h>

// NEW (update based on your exported library name):
#include <smartbracelet-keywords-v2_inferencing.h>
```

## Summary of Changes

| Component | Change |
|-----------|--------|
| `config.h` | Add 3 new session endpoint URLs |
| `main.cpp` (includes) | Update Edge Impulse library include |
| `main.cpp` (defines) | Add keyword index defines (KW_RECORD, etc.) |
| `main.cpp` (loop) | Replace ON/OFF logic with RECORD/STOP/SNAP/SEND |
| `main.cpp` (functions) | Add `sendAudioToSession()`, `sendImageToSession()`, `sendProcessRequest()` |
| `main.cpp` (photo state) | Update to use session endpoint |

## Testing Order

1. Flash firmware with new model
2. Test RECORD → STOP (audio should be buffered, not processed)
3. Test SNAP (photo should be buffered, not processed)
4. Test SEND (all buffered content should be processed)
5. Test combination: RECORD → STOP → SNAP → SEND

## LED Feedback Guide

| Action | LED Color |
|--------|-----------|
| RECORD detected | Purple flash, then Orange breathing |
| STOP detected | Blue (sending), Green flash (success) |
| SNAP countdown | Flashing white (3, 2, 1) |
| SNAP captured | Blue (sending), Green flash (success) |
| SEND processing | Blue, then Green flash (success) |
| Error | Red flashes |
