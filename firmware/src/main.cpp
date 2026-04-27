/**
 * Spatial Bracelet - WiFi HTTP POST Firmware
 *
 * XIAO ESP32S3 Sense with OV2640 camera + PDM microphone
 *
 * Voice commands (session-based workflow):
 * - RECORD: Start audio recording
 * - STOP: Stop recording, buffer in session (not processed yet)
 * - CAPTURE: Take photo with 3-second countdown, buffer in session
 * - POST: Process ALL accumulated session content with AI
 *
 * Button controls (direct send - legacy):
 * - Short press: capture image and POST directly
 * - Long press (hold): record audio while held, send on release
 *
 * LED feedback for status
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_camera.h>
#include <driver/i2s.h>
#include <esp_wifi.h>  // For WiFi power save control
#include <Adafruit_NeoPixel.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <math.h>  // For sqrt() in silence detection
#include "config.h"

// Edge Impulse for keyword detection
#define EIDSP_QUANTIZE_FILTERBANK 0
#include <spatialBraceletActionTraining_inferencing.h>

// =============================================================================
// KEYWORD INDICES (from model_variables.h)
// =============================================================================
// Labels: { "capture", "noise", "post", "record", "stop", "unknown" }
#define KW_CAPTURE  0   // Take photo (was "snap")
#define KW_NOISE    1   // Background noise (ignore)
#define KW_POST     2   // Process & send session (was "send")
#define KW_RECORD   3   // Start recording
#define KW_STOP     4   // Stop recording
#define KW_UNKNOWN  5   // Unknown word (ignore)

// NeoPixel LED
Adafruit_NeoPixel led(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);

// Button state - polling based for reliable timing
bool buttonDown = false;
bool lastButtonState = HIGH;  // Not pressed (active LOW with pullup)
unsigned long buttonPressTime = 0;
unsigned long lastDebounceTime = 0;

// Recording state
bool isRecording = false;
int16_t *audioBuffer = nullptr;
size_t audioBufferSize = 0;
size_t audioSamplesRecorded = 0;

// Voice activation state
bool voiceTriggered = false;           // True if recording started by wake word
unsigned long voiceRecordingStart = 0; // When voice recording started
unsigned long lastSpeechTime = 0;      // Last time speech was detected (for VAD)
bool wakeWordEnabled = false;          // Disabled - using button-only mode
unsigned long lastKeywordTime = 0;     // Cooldown timer to prevent double-triggers
// Cooldown values now defined in config.h: KEYWORD_COOLDOWN_IDLE_MS, KEYWORD_COOLDOWN_RECORD_MS

// Inference warmup counter - skip first N inferences after state change
// The continuous classifier needs EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW slices
// of fresh audio before results are reliable (prevents "record" word from
// being stuck in the temporal averaging buffer)
// CRITICAL: Initialize to NEGATIVE to skip startup garbage!
static int inferenceWarmupCounter = -(EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW + 4);

// Wake word inference buffers
typedef struct {
    signed short *buffers[2];
    unsigned char buf_select;
    unsigned char buf_ready;
    unsigned int buf_count;
    unsigned int n_samples;
} inference_t;
static inference_t inference;
static signed short wakeWordSampleBuffer[2048];  // Audio read buffer (128ms of audio)
static bool wakeWordInitialized = false;
static bool i2sInitialized = false;  // Track I2S driver status for safety

// =============================================================================
// PHOTO CAPTURE STATE MACHINE (non-blocking countdown)
// =============================================================================
typedef enum {
    PHOTO_STATE_IDLE,
    PHOTO_STATE_COUNTDOWN,
    PHOTO_STATE_CAPTURING
} PhotoState_t;

static PhotoState_t photoState = PHOTO_STATE_IDLE;
static int photoCountdownValue = 0;
static unsigned long photoCountdownLastTick = 0;
static bool photoPending = false;  // Flag to trigger photo after countdown

// =============================================================================
// NON-BLOCKING LED STATE MACHINE
// Allows LED feedback without blocking keyword detection
// =============================================================================
struct LEDState {
    uint32_t color;           // Current flash color
    uint32_t returnColor;     // Color to return to after flash sequence
    int flashCount;           // Remaining flash cycles (on+off pairs)
    int flashDelay;           // Delay between on/off states
    unsigned long lastTime;   // Last state change time
    bool isOn;                // Current LED state in flash sequence
    bool active;              // Whether flash sequence is running
} ledState = {0, 0, 0, 0, 0, false, false};

// Delayed LED state change (for wake word feedback)
static uint32_t pendingLEDColor = 0;
static unsigned long pendingLEDTime = 0;
static bool hasPendingLED = false;

// =============================================================================
// FREERTOS INFERENCE TASK
// Dedicated task for keyword detection - SOLE I2S READER to avoid race conditions
// =============================================================================
TaskHandle_t inferenceTaskHandle = NULL;
volatile int detectedKeyword = -1;  // Thread-safe detected keyword (-1 = none)
volatile bool inferenceEnabled = true;  // Enable/disable inference from main loop
SemaphoreHandle_t keywordMutex = NULL;  // Protect keyword access

// SHARED RECORDING STATE: Inference task writes audio here when recording is active
// This avoids I2S race conditions - only inference task reads from I2S
volatile bool recordingActive = false;           // Flag: inference task should capture audio
volatile size_t recordingSamplesWritten = 0;     // Samples written by inference task
volatile bool stopKeywordDetected = false;       // STOP detected during recording
SemaphoreHandle_t recordingMutex = NULL;         // Protect recordingSamplesWritten access

// Inference timing diagnostics
static unsigned long lastInferenceTime = 0;
static unsigned long avgInferenceInterval = 0;
static unsigned long maxInferenceGap = 0;

// Debug settings are now in config.h (DEBUG_INFERENCE)

// Connection state
bool wifiConnected = false;
unsigned long lastHealthCheck = 0;

// I2S configuration
#define I2S_PORT I2S_NUM_0

// Forward declarations
void initCamera();
void initAudio();
void initButton();
void initWakeWord();
void updateButton();
void connectWiFi();
bool captureAndSendImage();
bool recordAndSendAudio();
bool sendAudioToSession();
bool sendImageToSession();
bool sendSessionProcess();
void setLED(uint32_t color);
void flashLED(uint32_t color, int times, int delayMs);
void flashLEDNonBlocking(uint32_t color, int times, int delayMs, uint32_t returnColor = 0);
void updateLED();  // Call in main loop to process non-blocking LED animations
void setLEDDelayed(uint32_t color, unsigned long delayMs);  // Set LED after delay
void breatheLED(uint32_t color, int duration);
bool checkWakeWord();
int checkKeywordInIdle();     // For idle state: reads from I2S, returns keyword
int checkKeywordInference();  // For recording: uses pre-fed buffer, returns keyword
void feedToInference(int16_t *samples, size_t count);
void resetInferenceBuffers();  // Properly reset all inference state
void countdownLED(int seconds); // DEPRECATED: Use non-blocking photo state machine instead
void handlePhotoStateMachine();  // Non-blocking photo countdown handler
void inferenceTaskFunc(void* parameter);  // FreeRTOS inference task
int consumeDetectedKeyword();  // Get and clear detected keyword from task
void flashLEDOnce(uint32_t color, int durationMs);  // Single non-blocking flash
static int microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr);

// =============================================================================
// FREERTOS INFERENCE TASK IMPLEMENTATION
// SOLE I2S READER - Runs on Core 1 to avoid WiFi contention on Core 0
// This task is the ONLY code that reads from I2S to prevent race conditions
// =============================================================================
void inferenceTaskFunc(void* parameter) {
    const TickType_t xDelay = pdMS_TO_TICKS(20);  // 20ms polling for responsive recording

    Serial.println("[InferenceTask] Started on Core " + String(xPortGetCoreID()));

    while (true) {
        // ALWAYS read from I2S if initialized - we are the sole reader
        if (!i2sInitialized || !wakeWordInitialized) {
            vTaskDelay(xDelay);
            continue;
        }

        // Read audio from I2S - this is the ONLY place I2S is read
        size_t bytesRead = 0;
        i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 50);

        if (bytesRead == 0) {
            vTaskDelay(pdMS_TO_TICKS(5));  // Short delay if no data
            continue;
        }

        size_t samplesRead = bytesRead / sizeof(int16_t);
        size_t maxSamples = (AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS);

        // =================================================================
        // MODE A: RECORDING ACTIVE - Capture audio AND detect STOP keyword
        // =================================================================
        if (recordingActive) {
            // Scale samples by 8x - PDM microphone output is very quiet
            // This scaling is REQUIRED for both recording and inference
            static int16_t scaledChunk[2048];
            for (size_t i = 0; i < samplesRead && i < 2048; i++) {
                // Scale by 8x and clamp to int16 range to prevent overflow
                int32_t scaled = (int32_t)wakeWordSampleBuffer[i] * 8;
                if (scaled > 32767) scaled = 32767;
                if (scaled < -32768) scaled = -32768;
                scaledChunk[i] = (int16_t)scaled;
            }

            // Copy SCALED samples to audio buffer for recording
            size_t currentPos = recordingSamplesWritten;
            size_t space = maxSamples - currentPos;
            size_t toCopy = (samplesRead < space) ? samplesRead : space;

            if (toCopy > 0 && audioBuffer != nullptr) {
                memcpy(audioBuffer + currentPos, scaledChunk, toCopy * sizeof(int16_t));
                // Use mutex to safely update sample count (prevents race condition with main loop)
                if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
                    recordingSamplesWritten = currentPos + toCopy;
                    xSemaphoreGive(recordingMutex);
                } else {
                    recordingSamplesWritten = currentPos + toCopy;  // Fallback if mutex unavailable
                }
            }

            // Feed scaled samples to inference for STOP detection
            feedToInference(scaledChunk, samplesRead);
            int detected = checkKeywordInference();

            if (detected == KW_STOP) {
                stopKeywordDetected = true;
                Serial.println("[InferenceTask] STOP keyword detected!");
            }
        }
        // =================================================================
        // MODE B: IDLE - Run inference for keyword detection
        // =================================================================
        else if (inferenceEnabled && wakeWordEnabled) {
            unsigned long now = millis();

            // Track timing diagnostics
            if (lastInferenceTime > 0) {
                unsigned long interval = now - lastInferenceTime;
                avgInferenceInterval = (avgInferenceInterval * 9 + interval) / 10;
                if (interval > maxInferenceGap) {
                    maxInferenceGap = interval;
                }
            }
            lastInferenceTime = now;

            // Scale audio for inference
            for (size_t i = 0; i < samplesRead; i++) {
                wakeWordSampleBuffer[i] = (int16_t)(wakeWordSampleBuffer[i] * 8);
            }

            // Feed to inference buffer
            for (size_t i = 0; i < samplesRead && inference.buf_count < inference.n_samples; i++) {
                inference.buffers[inference.buf_select][inference.buf_count++] = wakeWordSampleBuffer[i];
            }

            // Check if buffer is full - swap and run inference
            if (inference.buf_count >= inference.n_samples) {
                inference.buf_select ^= 1;
                inference.buf_count = 0;
                inference.buf_ready = 1;
            }

            // Run inference if buffer ready
            if (inference.buf_ready) {
                inference.buf_ready = 0;

                // Warmup period check
                if (inferenceWarmupCounter < 0) {
                    inferenceWarmupCounter++;
                    #if DEBUG_INFERENCE
                    static unsigned long lastWarmupPrint = 0;
                    if (millis() - lastWarmupPrint >= 500) {
                        lastWarmupPrint = millis();
                        Serial.printf("[INF] Warmup: %d slices remaining\n", -inferenceWarmupCounter);
                    }
                    #endif
                } else {
                    // Calculate RMS energy of audio buffer to detect silence
                    // Use the buffer we're about to classify (buf_select ^ 1)
                    int bufIdx = inference.buf_select ^ 1;
                    int64_t sumSquares = 0;
                    for (size_t i = 0; i < EI_CLASSIFIER_SLICE_SIZE; i++) {
                        int32_t sample = inference.buffers[bufIdx][i];
                        sumSquares += (int64_t)sample * sample;
                    }
                    uint32_t rms = (uint32_t)sqrt((double)sumSquares / EI_CLASSIFIER_SLICE_SIZE);

                    // Skip inference if audio is too quiet (silence)
                    if (rms < INFERENCE_SILENCE_RMS_THRESHOLD) {
                        #if DEBUG_INFERENCE
                        static unsigned long lastSilencePrint = 0;
                        if (millis() - lastSilencePrint >= 2000) {
                            lastSilencePrint = millis();
                            Serial.printf("[INF] Silence detected (RMS=%u < %d), skipping inference\n",
                                rms, INFERENCE_SILENCE_RMS_THRESHOLD);
                        }
                        #endif
                        continue;  // Skip to next iteration
                    }

                    #if DEBUG_INFERENCE
                    static uint32_t lastRMS = 0;
                    lastRMS = rms;  // Track for debug output
                    #endif

                    signal_t signal;
                    signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
                    signal.get_data = &microphone_audio_signal_get_data;

                    ei_impulse_result_t result = { 0 };
                    EI_IMPULSE_ERROR err = run_classifier_continuous(&impulse_handle_936625_1, &signal, &result, false);

                    if (err == EI_IMPULSE_OK) {
                        // Extract all class confidences
                        float captureConf = 0.0f, noiseConf = 0.0f, postConf = 0.0f;
                        float recordConf = 0.0f, stopConf = 0.0f, unknownConf = 0.0f;

                        for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
                            const char* label = result.classification[ix].label;
                            float value = result.classification[ix].value;
                            // Order: noise first for early rejection, then actionable keywords
                            if (strcmp(label, "noise") == 0) noiseConf = value;
                            else if (strcmp(label, "unknown") == 0) unknownConf = value;
                            else if (strcmp(label, "capture") == 0) captureConf = value;
                            else if (strcmp(label, "stop") == 0) stopConf = value;
                            else if (strcmp(label, "post") == 0) postConf = value;
                            else if (strcmp(label, "record") == 0) recordConf = value;
                        }

                        #if DEBUG_INFERENCE
                        // Print all confidences for debugging
                        static unsigned long lastDebugPrint = 0;
                        unsigned long now = millis();
                        // Print every 500ms to avoid flooding serial
                        if (now - lastDebugPrint >= 500) {
                            lastDebugPrint = now;
                            Serial.printf("[INF] cap:%.2f noi:%.2f pos:%.2f rec:%.2f sto:%.2f unk:%.2f",
                                captureConf, noiseConf, postConf, recordConf, stopConf, unknownConf);

                            // Find the highest confidence class for quick visual
                            const char* topClass = "?";
                            float topVal = 0.0f;
                            if (captureConf > topVal) { topVal = captureConf; topClass = "CAPTURE"; }
                            if (noiseConf > topVal) { topVal = noiseConf; topClass = "noise"; }
                            if (postConf > topVal) { topVal = postConf; topClass = "POST"; }
                            if (recordConf > topVal) { topVal = recordConf; topClass = "RECORD"; }
                            if (stopConf > topVal) { topVal = stopConf; topClass = "STOP"; }
                            if (unknownConf > topVal) { topVal = unknownConf; topClass = "unknown"; }

                            Serial.printf(" | TOP: %s (%.2f)\n", topClass, topVal);
                        }
                        #endif

                        // =============================================================
                        // PER-KEYWORD THRESHOLD DETECTION
                        // Each keyword has its own confidence threshold based on:
                        // - Phonetic distinctiveness
                        // - False positive tendency
                        // - Detection difficulty
                        // =============================================================
                        int bestIdx = -1;
                        float bestValue = 0.0f;
                        float usedThreshold = 0.0f;

                        // Check each keyword against its specific threshold
                        // Priority: check all, pick the one that exceeds its threshold by most
                        struct {
                            int idx;
                            float conf;
                            float threshold;
                            float noiseReject;
                        } candidates[] = {
                            // Order: capture, stop, post, record (record last - most false positive prone)
                            { KW_CAPTURE, captureConf, THRESHOLD_CAPTURE, NOISE_REJECT_CAPTURE },
                            { KW_STOP,    stopConf,    THRESHOLD_STOP,    NOISE_REJECT_STOP },
                            { KW_POST,    postConf,    THRESHOLD_POST,    NOISE_REJECT_POST },
                            { KW_RECORD,  recordConf,  THRESHOLD_RECORD,  NOISE_REJECT_RECORD },
                        };

                        for (int i = 0; i < 4; i++) {
                            // Check if this keyword exceeds its threshold
                            if (candidates[i].conf >= candidates[i].threshold) {
                                // Calculate how much it exceeds threshold (margin)
                                float margin = candidates[i].conf - candidates[i].threshold;
                                // Pick the one with highest margin (most confident detection)
                                if (margin > (bestValue - usedThreshold)) {
                                    bestIdx = candidates[i].idx;
                                    bestValue = candidates[i].conf;
                                    usedThreshold = candidates[i].threshold;
                                }
                            }
                        }

                        // Apply per-keyword noise rejection
                        if (bestIdx >= 0) {
                            float noiseRejectThreshold = NOISE_REJECTION_THRESHOLD;
                            // Get per-keyword noise rejection threshold
                            for (int i = 0; i < 4; i++) {
                                if (candidates[i].idx == bestIdx) {
                                    noiseRejectThreshold = candidates[i].noiseReject;
                                    break;
                                }
                            }

                            // Reject if noise or unknown is too high
                            if (noiseConf > noiseRejectThreshold ||
                                unknownConf > noiseRejectThreshold ||
                                (noiseConf + unknownConf) > 0.60f) {
                                #if DEBUG_INFERENCE
                                const char* kwNames[] = {"CAPTURE", "noise", "POST", "RECORD", "STOP", "unknown"};
                                Serial.printf("[INF] REJECTED %s: noise=%.2f unk=%.2f (reject_thr=%.2f)\n",
                                    kwNames[bestIdx], noiseConf, unknownConf, noiseRejectThreshold);
                                #endif
                                bestIdx = -1;
                            }
                        }

                        // Store detected keyword thread-safely
                        if (bestIdx >= 0) {
                            #if DEBUG_INFERENCE
                            const char* kwNames[] = {"CAPTURE", "noise", "POST", "RECORD", "STOP", "unknown"};
                            Serial.printf("[INF] >>> DETECTED: %s (%.2f >= %.2f) <<<\n",
                                kwNames[bestIdx], bestValue, usedThreshold);
                            #endif
                            if (xSemaphoreTake(keywordMutex, portMAX_DELAY) == pdTRUE) {
                                detectedKeyword = bestIdx;
                                xSemaphoreGive(keywordMutex);
                            }
                        }
                    }
                }
            }
        }
        // =================================================================
        // MODE C: DISABLED - Just keep I2S flowing (prevent DMA overflow)
        // =================================================================
        // Audio is read at the top of the loop, so nothing more needed here

        vTaskDelay(xDelay);
    }
}

// Get and clear the detected keyword (thread-safe)
int consumeDetectedKeyword() {
    int keyword = -1;
    if (xSemaphoreTake(keywordMutex, portMAX_DELAY) == pdTRUE) {
        keyword = detectedKeyword;
        detectedKeyword = -1;  // Clear after consuming
        xSemaphoreGive(keywordMutex);
    }
    return keyword;
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println("\n=================================");
    Serial.println("Spatial Bracelet - WiFi Firmware");
    Serial.println("=================================");

    // Initialize LED
    led.begin();
    led.setBrightness(50);
    setLED(LED_COLOR_CONNECTING);

    // Initialize button
    initButton();

    // Connect to WiFi
    connectWiFi();

    // Initialize camera
    initCamera();

    // Initialize audio (I2S PDM microphone)
    initAudio();

    // Initialize wake word detection
    initWakeWord();

    // Create mutex for thread-safe keyword communication
    keywordMutex = xSemaphoreCreateMutex();
    if (keywordMutex == NULL) {
        Serial.println("ERROR: Failed to create keyword mutex!");
    }

    // Create mutex for recording state synchronization (prevents race condition)
    recordingMutex = xSemaphoreCreateMutex();
    if (recordingMutex == NULL) {
        Serial.println("ERROR: Failed to create recording mutex!");
    }

    // Create dedicated inference task on Core 1 (WiFi runs on Core 0)
    BaseType_t taskResult = xTaskCreatePinnedToCore(
        inferenceTaskFunc,      // Task function
        "Inference",            // Task name
        8192,                   // Stack size (bytes)
        NULL,                   // Parameters
        configMAX_PRIORITIES - 2,  // High priority (2nd highest)
        &inferenceTaskHandle,   // Task handle
        1                       // Core 1 (avoid WiFi on Core 0)
    );

    if (taskResult != pdPASS) {
        Serial.println("ERROR: Failed to create inference task!");
    } else {
        Serial.println("Inference task created on Core 1");
    }

    // Ready state
    setLED(LED_COLOR_IDLE);
    Serial.println("\nReady!");
    Serial.println("Voice commands:");
    Serial.println("  - RECORD: Start audio recording");
    Serial.println("  - STOP: Stop recording (buffered in session)");
    Serial.println("  - CAPTURE: Take photo with 3-second countdown");
    Serial.println("  - POST: Process all session content with AI");
    Serial.println("Button controls:");
    Serial.println("  - Short press: capture image (direct send)");
    Serial.println("  - Long press (hold): record audio (direct send)");
}

void loop() {
    // Check WiFi connection
    if (WiFi.status() != WL_CONNECTED) {
        if (wifiConnected) {
            Serial.println("WiFi disconnected! Reconnecting...");
            wifiConnected = false;
            setLED(LED_COLOR_ERROR);
            connectWiFi();
        }
    }

    // Poll button state (handles press/release detection)
    updateButton();

    // Process non-blocking LED animations
    updateLED();

    // =========================================================================
    // HANDLE PHOTO STATE MACHINE (non-blocking countdown)
    // =========================================================================
    handlePhotoStateMachine();

    // =========================================================================
    // WAKE WORD DETECTION (FreeRTOS task handles audio reading and inference)
    // Main loop just consumes detected keywords from the task
    // =========================================================================
    bool inPhotoState = (photoState != PHOTO_STATE_IDLE);

    // Control inference task: disable during recording, button press, or photo state
    bool shouldEnableInference = !isRecording && !buttonDown && wakeWordEnabled &&
                                  wakeWordInitialized && !inPhotoState;

    // Also disable during cooldown
    unsigned long timeSinceLastKeyword = millis() - lastKeywordTime;
    unsigned long requiredCooldown = KEYWORD_COOLDOWN_IDLE_MS;
    if (timeSinceLastKeyword < KEYWORD_COOLDOWN_PHOTO_MS) {
        requiredCooldown = KEYWORD_COOLDOWN_PHOTO_MS;
    }
    bool inCooldown = (timeSinceLastKeyword < requiredCooldown);

    if (inCooldown && shouldEnableInference) {
        shouldEnableInference = false;

        // Debug: Print cooldown status occasionally
        static unsigned long lastCooldownPrint = 0;
        if (millis() - lastCooldownPrint > 1000) {
            lastCooldownPrint = millis();
            Serial.printf("[Cooldown] %lums remaining\n", requiredCooldown - timeSinceLastKeyword);
        }
    }

    // Update inference task state
    inferenceEnabled = shouldEnableInference;

    // Check for keywords detected by the inference task
    if (shouldEnableInference) {
        int detected = consumeDetectedKeyword();

        if (detected == KW_RECORD) {  // "record" detected - start audio recording
            Serial.println(">>> 'RECORD' DETECTED! Starting audio recording...");
            lastKeywordTime = millis();  // Start cooldown
            inferenceEnabled = false;  // Disable idle inference (recording uses different mode)
            resetInferenceBuffers();  // Clear stale audio to prevent contamination

            voiceTriggered = true;
            isRecording = true;
            audioSamplesRecorded = 0;
            voiceRecordingStart = millis();
            lastSpeechTime = millis();

            // Initialize shared recording state for inference task
            recordingSamplesWritten = 0;
            stopKeywordDetected = false;
            recordingActive = true;  // Tell inference task to start capturing audio

            setLED(LED_COLOR_WAKE_DETECTED);
            setLEDDelayed(LED_COLOR_LISTENING, 200);  // Non-blocking: switch to listening after 200ms
        }
        else if (detected == KW_CAPTURE) {  // "capture" detected - take photo
            Serial.println(">>> 'CAPTURE' DETECTED! Starting 3-second countdown...");
            lastKeywordTime = millis();  // Start initial cooldown
            inferenceEnabled = false;  // Immediately disable inference
            resetInferenceBuffers();  // Clear stale audio to prevent contamination

            // Start non-blocking photo countdown state machine
            photoState = PHOTO_STATE_COUNTDOWN;
            photoCountdownValue = 3;
            photoCountdownLastTick = millis();
            photoPending = true;
            Serial.printf("  %d...\n", photoCountdownValue);
        }
        else if (detected == KW_POST) {  // "post" detected - process session
            Serial.println(">>> 'POST' DETECTED! Processing session...");
            lastKeywordTime = millis();
            inferenceEnabled = false;  // Immediately disable inference
            resetInferenceBuffers();

            setLED(LED_COLOR_SENDING);
            if (sendSessionProcess()) {
                flashLED(LED_COLOR_SUCCESS, 2, 150);
            } else {
                flashLED(LED_COLOR_ERROR, 3, 200);
            }
            setLED(LED_COLOR_IDLE);
        }
    }

    // =========================================================================
    // VOICE-TRIGGERED RECORDING (with STOP keyword to stop)
    // Audio is captured by inference task (sole I2S reader) - we just monitor state
    // =========================================================================
    if (isRecording && voiceTriggered) {
        size_t maxSamples = (AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS);

        // Update audioSamplesRecorded from inference task's counter (mutex protected)
        if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
            audioSamplesRecorded = recordingSamplesWritten;
            xSemaphoreGive(recordingMutex);
        } else {
            audioSamplesRecorded = recordingSamplesWritten;  // Fallback
        }

        // Check for STOP keyword detected by inference task
        if (stopKeywordDetected) {
            stopKeywordDetected = false;  // Clear flag
            recordingActive = false;      // Stop inference task from recording

            Serial.println(">>> 'STOP' keyword detected! Stopping recording...");
            isRecording = false;
            voiceTriggered = false;

            Serial.printf("Voice recording complete: %d samples (%.1f sec)\n",
                         audioSamplesRecorded,
                         (float)audioSamplesRecorded / AUDIO_SAMPLE_RATE);

            // Send to SESSION endpoint (buffered, not processed yet)
            setLED(LED_COLOR_SENDING);
            if (sendAudioToSession()) {
                Serial.println("Audio buffered in session - say POST to process");
                flashLED(LED_COLOR_SUCCESS, 2, 150);
            } else {
                flashLED(LED_COLOR_ERROR, 3, 200);
            }
            setLED(LED_COLOR_IDLE);

            // Set cooldown and reset inference
            lastKeywordTime = millis();
            resetInferenceBuffers();
        }
        // Check for max recording time
        else if (audioSamplesRecorded >= maxSamples) {
            recordingActive = false;  // Stop inference task from recording

            Serial.println("Max recording time reached!");
            isRecording = false;
            voiceTriggered = false;

            setLED(LED_COLOR_SENDING);
            if (sendAudioToSession()) {
                Serial.println("Audio buffered in session - say POST to process");
                flashLED(LED_COLOR_SUCCESS, 2, 150);
            } else {
                flashLED(LED_COLOR_ERROR, 3, 200);
            }
            setLED(LED_COLOR_IDLE);

            lastKeywordTime = millis();
            resetInferenceBuffers();
        }
        else {
            // Breathing LED effect while recording
            static unsigned long lastBreath = 0;
            if (millis() - lastBreath > 100) {
                lastBreath = millis();
                float brightness = (sin(millis() * 0.005) + 1) / 2;
                uint8_t r = 255 * brightness;
                uint8_t g = 68 * brightness;
                led.setPixelColor(0, led.Color(r, g, 0));  // Orange breathing
                led.show();
            }
        }
    }

    // =========================================================================
    // BUTTON-TRIGGERED RECORDING (existing behavior)
    // =========================================================================
    // Check for long press threshold while button is held
    if (buttonDown && !isRecording) {
        unsigned long heldDuration = millis() - buttonPressTime;

        if (heldDuration >= LONG_PRESS_MS) {
            // Start recording audio
            Serial.println("Long press -> starting audio recording...");
            Serial.println("Release button to stop and send.");
            isRecording = true;
            voiceTriggered = false;  // Not voice triggered
            audioSamplesRecorded = 0;

            // Initialize shared recording state for inference task
            recordingSamplesWritten = 0;
            stopKeywordDetected = false;
            recordingActive = true;  // Tell inference task to start capturing audio

            setLED(LED_COLOR_RECORDING);
        }
    }

    // Continue recording while button is held (button-triggered only)
    // Audio is captured by inference task (sole I2S reader) - we just monitor state
    if (isRecording && buttonDown && !voiceTriggered) {
        size_t maxSamples = (AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS);

        // Update audioSamplesRecorded from inference task's counter (mutex protected)
        if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
            audioSamplesRecorded = recordingSamplesWritten;
            xSemaphoreGive(recordingMutex);
        } else {
            audioSamplesRecorded = recordingSamplesWritten;  // Fallback
        }

        if (audioSamplesRecorded < maxSamples) {
            // Breathing LED effect while recording
            static unsigned long lastBreath = 0;
            if (millis() - lastBreath > 100) {
                lastBreath = millis();
                float brightness = (sin(millis() * 0.005) + 1) / 2;
                uint8_t r = 255 * brightness;
                led.setPixelColor(0, led.Color(r, 0, 0));
                led.show();
            }
        } else {
            // Max recording reached - auto-stop
            Serial.println("Max recording time reached!");
            recordingActive = false;  // Stop inference task from recording
        }
    }

    // Periodic health check - NON-BLOCKING (reduced timeout, fast fail)
    if (millis() - lastHealthCheck >= HEALTH_CHECK_INTERVAL_MS) {
        lastHealthCheck = millis();

        // Only attempt health check if WiFi is connected
        if (WiFi.status() == WL_CONNECTED) {
            HTTPClient http;
            http.begin(RELAY_URL_HEALTH);
            http.setTimeout(HEALTH_CHECK_TIMEOUT_MS);  // 500ms max, fast fail

            int httpCode = http.GET();
            if (httpCode == 200) {
                Serial.println("Health: OK");
            } else {
                Serial.printf("Health: %d\n", httpCode);
            }
            http.end();
        } else {
            Serial.println("Health: WiFi disconnected");
        }
    }

    // No delay - let I2S reads provide natural pacing
    yield();  // Allow FreeRTOS to run background tasks
}

void initCamera() {
    Serial.println("Initializing camera...");

    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = CAMERA_FRAME_SIZE;
    config.jpeg_quality = CAMERA_JPEG_QUALITY;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("Camera init failed with error 0x%x\n", err);
        flashLED(LED_COLOR_ERROR, 5, 100);
        while (1) delay(1000);
    }

    // Get camera sensor for adjustments
    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        s->set_brightness(s, 0);
        s->set_contrast(s, 0);
        s->set_saturation(s, 0);
        s->set_whitebal(s, 1);
        s->set_awb_gain(s, 1);
        s->set_wb_mode(s, 0);
        s->set_exposure_ctrl(s, 1);
        s->set_aec2(s, 0);
        s->set_gain_ctrl(s, 1);
        s->set_agc_gain(s, 0);
        s->set_gainceiling(s, (gainceiling_t)0);
        s->set_bpc(s, 0);
        s->set_wpc(s, 1);
        s->set_raw_gma(s, 1);
        s->set_lenc(s, 1);
        s->set_hmirror(s, 1);  // Mirror horizontally (selfie mode)
        s->set_vflip(s, 0);
    }

    Serial.println("Camera initialized successfully");
}

void initButton() {
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    lastButtonState = digitalRead(BUTTON_PIN);
    Serial.println("Button initialized on GPIO " + String(BUTTON_PIN));
}

// Polling-based button reading (call this in loop)
void updateButton() {
    bool currentState = digitalRead(BUTTON_PIN);

    // Debounce: only process if state stable for DEBOUNCE_MS
    if (currentState != lastButtonState) {
        lastDebounceTime = millis();
    }

    if ((millis() - lastDebounceTime) > DEBOUNCE_MS) {
        // State is stable, check for transitions
        if (currentState == LOW && !buttonDown) {
            // Button just pressed
            buttonDown = true;
            buttonPressTime = millis();
            Serial.println("Button pressed");
        } else if (currentState == HIGH && buttonDown) {
            // Button just released
            buttonDown = false;
            unsigned long pressDuration = millis() - buttonPressTime;
            Serial.printf("Button released after %lu ms\n", pressDuration);

            // Handle the release
            if (isRecording) {
                // Was recording - stop and send
                recordingActive = false;  // Stop inference task from recording
                isRecording = false;
                // Get final count from inference task (mutex protected)
                if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                    audioSamplesRecorded = recordingSamplesWritten;
                    xSemaphoreGive(recordingMutex);
                } else {
                    audioSamplesRecorded = recordingSamplesWritten;  // Fallback
                }
                Serial.printf("Recording stopped. %d samples (%.1f sec)\n",
                             audioSamplesRecorded,
                             (float)audioSamplesRecorded / AUDIO_SAMPLE_RATE);
                setLED(LED_COLOR_SENDING);
                if (recordAndSendAudio()) {
                    flashLED(LED_COLOR_SUCCESS, 2, 150);
                } else {
                    flashLED(LED_COLOR_ERROR, 3, 200);
                }
                setLED(LED_COLOR_IDLE);
            } else if (pressDuration < LONG_PRESS_MS) {
                // Short press - capture image
                Serial.println("Short press -> capturing image");
                setLED(LED_COLOR_CAPTURING);
                if (captureAndSendImage()) {
                    flashLED(LED_COLOR_SUCCESS, 2, 150);
                } else {
                    flashLED(LED_COLOR_ERROR, 3, 200);
                }
                setLED(LED_COLOR_IDLE);
            }
            // If pressDuration >= LONG_PRESS_MS but !isRecording,
            // recording already started and was handled above
        }
    }

    lastButtonState = currentState;
}

void initAudio() {
    Serial.println("Initializing I2S PDM microphone...");

    // Calculate buffer size for max recording duration
    audioBufferSize = AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS * sizeof(int16_t);

    // Allocate audio buffer in PSRAM
    audioBuffer = (int16_t *)ps_malloc(audioBufferSize);
    if (!audioBuffer) {
        Serial.println("Failed to allocate audio buffer in PSRAM!");
        flashLED(LED_COLOR_ERROR, 5, 100);
        return;
    }
    Serial.printf("Audio buffer allocated: %d bytes in PSRAM\n", audioBufferSize);

    // Configure I2S for PDM microphone
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = AUDIO_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL2,  // Higher interrupt priority for consistent timing
        .dma_buf_count = 16,   // Increased from 8 for better buffering
        .dma_buf_len = 1024,   // Max allowed by ESP-IDF I2S driver
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_PIN_NO_CHANGE,
        .ws_io_num = PDM_CLK_PIN,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = PDM_DATA_PIN
    };

    esp_err_t err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("I2S driver install failed: %d\n", err);
        return;
    }

    err = i2s_set_pin(I2S_PORT, &pin_config);
    if (err != ESP_OK) {
        Serial.printf("I2S set pin failed: %d\n", err);
        return;
    }

    i2sInitialized = true;
    Serial.println("I2S PDM microphone initialized successfully");
}

void connectWiFi() {
    Serial.printf("Target WiFi: %s\n", WIFI_SSID);
    Serial.printf("Password length: %d chars\n", strlen(WIFI_PASSWORD));
    setLED(LED_COLOR_CONNECTING);

    WiFi.mode(WIFI_STA);
    WiFi.setTxPower(WIFI_POWER_19_5dBm);  // Max TX power for weak signals

    // Scan for networks first to help debug
    Serial.println("Scanning for networks...");
    int numNetworks = WiFi.scanNetworks();
    Serial.printf("Found %d networks:\n", numNetworks);
    bool targetFound = false;
    int targetRSSI = -100;
    for (int i = 0; i < numNetworks && i < 10; i++) {
        String ssid = WiFi.SSID(i);
        int rssi = WiFi.RSSI(i);
        Serial.printf("  %d: %s (%d dBm)%s\n", i+1, ssid.c_str(), rssi,
                      ssid == WIFI_SSID ? " <-- TARGET" : "");
        if (ssid == WIFI_SSID) {
            targetFound = true;
            targetRSSI = rssi;
        }
    }

    if (!targetFound) {
        Serial.printf("\nWARNING: '%s' not found in scan!\n", WIFI_SSID);
        Serial.println("Check: Is network in range? Is it 2.4GHz? (ESP32 doesn't support 5GHz)");
    } else if (targetRSSI < -75) {
        Serial.printf("\nWARNING: Signal is WEAK (%d dBm). Move closer to the router/hotspot!\n", targetRSSI);
        Serial.println("  -30 to -50 dBm = Excellent");
        Serial.println("  -50 to -60 dBm = Good");
        Serial.println("  -60 to -70 dBm = Fair");
        Serial.println("  -70 to -80 dBm = Weak (connection issues likely)");
        Serial.println("  Below -80 dBm = Very weak (auth may fail)");
    }

    // Retry loop for weak signal connections
    int maxRetries = 3;
    for (int attempt = 1; attempt <= maxRetries; attempt++) {
        Serial.printf("\nConnection attempt %d/%d to %s...\n", attempt, maxRetries, WIFI_SSID);

        WiFi.disconnect(true);
        delay(100);
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

        unsigned long startTime = millis();
        int dots = 0;

        while (WiFi.status() != WL_CONNECTED) {
            if (millis() - startTime > WIFI_CONNECT_TIMEOUT_MS) {
                wl_status_t status = WiFi.status();
                Serial.printf("\nAttempt %d failed! Status: %d", attempt, status);
                if (status == 6) Serial.print(" (WRONG_PASSWORD - may be weak signal)");
                Serial.println();
                break;
            }

            delay(500);
            Serial.print(".");
            dots++;
            if (dots % 20 == 0) {
                Serial.printf(" (status=%d)\n", WiFi.status());
            }

            // Pulse LED while connecting
            led.setPixelColor(0, (dots % 2) ? LED_COLOR_CONNECTING : 0);
            led.show();
        }

        if (WiFi.status() == WL_CONNECTED) {
            break;  // Success!
        }

        if (attempt < maxRetries) {
            Serial.println("Retrying in 2 seconds...");
            flashLED(LED_COLOR_ERROR, 2, 200);
            delay(2000);
        }
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\nAll connection attempts failed!");
        Serial.println("Possible causes:");
        Serial.println("  1. Weak signal - move closer to router/hotspot");
        Serial.println("  2. Wrong password (check config.h)");
        Serial.println("  3. Network congestion - try again");
        flashLED(LED_COLOR_ERROR, 5, 200);
        ESP.restart();
    }

    wifiConnected = true;
    Serial.println();
    Serial.println("WiFi connected!");
    Serial.printf("IP address: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Signal strength (RSSI): %d dBm\n", WiFi.RSSI());

    // Disable WiFi power save for maximum throughput and minimum latency
    esp_wifi_set_ps(WIFI_PS_NONE);
    Serial.println("WiFi power save disabled for performance");

    flashLED(LED_COLOR_SUCCESS, 3, 100);
}

bool captureAndSendImage() {
    // Capture frame
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("Camera capture failed");
        return false;
    }

    Serial.printf("Captured image: %dx%d, %d bytes\n", fb->width, fb->height, fb->len);
    unsigned long startTime = millis();

    // Send via HTTP POST using streaming (no large memory allocation)
    setLED(LED_COLOR_SENDING);

    WiFiClient client;
    if (!client.connect(RELAY_IP, RELAY_PORT)) {
        Serial.println("Connection to relay server failed");
        esp_camera_fb_return(fb);
        return false;
    }

    // Build multipart boundary and headers
    String boundary = "----ESP32Boundary";
    String header = "--" + boundary + "\r\n";
    header += "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n";
    header += "Content-Type: image/jpeg\r\n\r\n";
    String footer = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = header.length() + fb->len + footer.length();

    // Send HTTP request headers
    client.println("POST /capture/image HTTP/1.1");
    client.printf("Host: %s:%d\r\n", RELAY_IP, RELAY_PORT);
    client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary.c_str());
    client.printf("Content-Length: %d\r\n", totalLen);
    client.println("Connection: close");
    client.println();

    // Send multipart header
    client.print(header);

    // Stream image data in chunks (4KB chunks for optimal performance)
    const size_t CHUNK_SIZE = 4096;
    size_t sent = 0;
    Serial.printf("Streaming %d bytes in %dB chunks...\n", fb->len, CHUNK_SIZE);

    while (sent < fb->len) {
        size_t chunk = min(CHUNK_SIZE, fb->len - sent);
        size_t written = client.write(fb->buf + sent, chunk);
        if (written == 0) {
            Serial.println("Write failed during streaming");
            break;
        }
        sent += written;
        yield();  // Allow WiFi stack to process
    }

    // Send multipart footer
    client.print(footer);

    // Return frame buffer immediately after sending
    esp_camera_fb_return(fb);

    // Wait for response (with timeout)
    unsigned long timeout = millis() + 15000;  // 15 second timeout for response
    while (client.connected() && !client.available() && millis() < timeout) {
        delay(10);
    }

    // Read HTTP response
    int httpCode = 0;
    bool success = false;
    if (client.available()) {
        String statusLine = client.readStringUntil('\n');
        if (statusLine.indexOf("200") > 0) {
            httpCode = 200;
            success = true;
        } else if (statusLine.indexOf("201") > 0) {
            httpCode = 201;
            success = true;
        }
        Serial.printf("HTTP response: %s\n", statusLine.c_str());

        // Skip headers, read body
        while (client.available()) {
            String line = client.readStringUntil('\n');
            if (line == "\r" || line == "") break;  // End of headers
        }
        if (client.available()) {
            String body = client.readString();
            Serial.printf("Response body: %s\n", body.c_str());
        }
    } else {
        Serial.println("No response from server (timeout)");
    }

    client.stop();

    unsigned long elapsed = millis() - startTime;
    Serial.printf("Image upload %s in %lu ms (%d bytes, %.1f KB/s)\n",
                  success ? "completed" : "failed",
                  elapsed,
                  (int)(header.length() + sent + footer.length()),
                  (float)(header.length() + sent + footer.length()) / elapsed * 1000 / 1024);

    return success;
}

bool recordAndSendAudio() {
    if (audioSamplesRecorded == 0) {
        Serial.println("No audio samples recorded");
        return false;
    }

    // Build WAV header
    uint32_t dataSize = audioSamplesRecorded * sizeof(int16_t);
    uint32_t fileSize = dataSize + 44 - 8;  // Total size minus RIFF header

    uint8_t wavHeader[44] = {
        // RIFF chunk
        'R', 'I', 'F', 'F',
        (uint8_t)(fileSize & 0xFF),
        (uint8_t)((fileSize >> 8) & 0xFF),
        (uint8_t)((fileSize >> 16) & 0xFF),
        (uint8_t)((fileSize >> 24) & 0xFF),
        'W', 'A', 'V', 'E',
        // fmt chunk
        'f', 'm', 't', ' ',
        16, 0, 0, 0,                          // Subchunk1Size (16 for PCM)
        1, 0,                                  // AudioFormat (1 = PCM)
        AUDIO_CHANNELS, 0,                     // NumChannels
        (uint8_t)(AUDIO_SAMPLE_RATE & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 8) & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 16) & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 24) & 0xFF),
        // ByteRate = SampleRate * NumChannels * BitsPerSample/8
        (uint8_t)((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 8) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 16) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 24) & 0xFF),
        // BlockAlign = NumChannels * BitsPerSample/8
        (uint8_t)(AUDIO_CHANNELS * 2), 0,
        // BitsPerSample
        AUDIO_BITS_PER_SAMPLE, 0,
        // data chunk
        'd', 'a', 't', 'a',
        (uint8_t)(dataSize & 0xFF),
        (uint8_t)((dataSize >> 8) & 0xFF),
        (uint8_t)((dataSize >> 16) & 0xFF),
        (uint8_t)((dataSize >> 24) & 0xFF)
    };

    Serial.printf("Sending audio: %d bytes WAV (%d samples)\n", dataSize + 44, audioSamplesRecorded);

    // Send via HTTP POST
    HTTPClient http;
    http.begin(RELAY_URL_AUDIO);
    http.setTimeout(HTTP_TIMEOUT_MS);

    // Build multipart form data
    String boundary = "----ESP32AudioBoundary";
    String contentType = "multipart/form-data; boundary=" + boundary;
    http.addHeader("Content-Type", contentType);

    // Create multipart body
    String bodyStart = "--" + boundary + "\r\n";
    bodyStart += "Content-Disposition: form-data; name=\"audio\"; filename=\"recording.wav\"\r\n";
    bodyStart += "Content-Type: audio/wav\r\n\r\n";

    String bodyEnd = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = bodyStart.length() + 44 + dataSize + bodyEnd.length();

    // Allocate buffer for complete body
    uint8_t *body = (uint8_t *)ps_malloc(totalLen);
    if (!body) {
        Serial.println("Failed to allocate memory for HTTP body");
        return false;
    }

    // Assemble body
    size_t offset = 0;
    memcpy(body + offset, bodyStart.c_str(), bodyStart.length());
    offset += bodyStart.length();
    memcpy(body + offset, wavHeader, 44);
    offset += 44;
    memcpy(body + offset, audioBuffer, dataSize);
    offset += dataSize;
    memcpy(body + offset, bodyEnd.c_str(), bodyEnd.length());

    // Send request
    Serial.println("Sending to relay server...");
    int httpCode = http.POST(body, totalLen);

    free(body);

    if (httpCode > 0) {
        Serial.printf("HTTP response code: %d\n", httpCode);
        if (httpCode == HTTP_CODE_OK) {
            String response = http.getString();
            Serial.println("Response: " + response);
            http.end();
            return true;
        }
    } else {
        Serial.printf("HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
    }

    http.end();
    return false;
}

// =============================================================================
// SESSION ENDPOINT FUNCTIONS
// These send to session buffer endpoints for accumulation, not direct processing
// =============================================================================

bool sendAudioToSession() {
    if (audioSamplesRecorded == 0) {
        Serial.println("No audio samples recorded");
        return false;
    }

    // Build WAV header
    uint32_t dataSize = audioSamplesRecorded * sizeof(int16_t);
    uint32_t fileSize = dataSize + 44 - 8;

    uint8_t wavHeader[44] = {
        'R', 'I', 'F', 'F',
        (uint8_t)(fileSize & 0xFF),
        (uint8_t)((fileSize >> 8) & 0xFF),
        (uint8_t)((fileSize >> 16) & 0xFF),
        (uint8_t)((fileSize >> 24) & 0xFF),
        'W', 'A', 'V', 'E',
        'f', 'm', 't', ' ',
        16, 0, 0, 0,
        1, 0,
        AUDIO_CHANNELS, 0,
        (uint8_t)(AUDIO_SAMPLE_RATE & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 8) & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 16) & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE >> 24) & 0xFF),
        (uint8_t)((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 8) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 16) & 0xFF),
        (uint8_t)(((AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2) >> 24) & 0xFF),
        (uint8_t)(AUDIO_CHANNELS * 2), 0,
        AUDIO_BITS_PER_SAMPLE, 0,
        'd', 'a', 't', 'a',
        (uint8_t)(dataSize & 0xFF),
        (uint8_t)((dataSize >> 8) & 0xFF),
        (uint8_t)((dataSize >> 16) & 0xFF),
        (uint8_t)((dataSize >> 24) & 0xFF)
    };

    Serial.printf("Sending audio to session: %d bytes WAV (%d samples)\n", dataSize + 44, audioSamplesRecorded);

    HTTPClient http;
    http.begin(RELAY_URL_SESSION_AUDIO);
    http.setTimeout(HTTP_TIMEOUT_MS);

    String boundary = "----ESP32SessionAudio";
    String contentType = "multipart/form-data; boundary=" + boundary;
    http.addHeader("Content-Type", contentType);

    String bodyStart = "--" + boundary + "\r\n";
    bodyStart += "Content-Disposition: form-data; name=\"audio\"; filename=\"recording.wav\"\r\n";
    bodyStart += "Content-Type: audio/wav\r\n\r\n";

    String bodyEnd = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = bodyStart.length() + 44 + dataSize + bodyEnd.length();

    uint8_t *body = (uint8_t *)ps_malloc(totalLen);
    if (!body) {
        Serial.println("Failed to allocate memory for HTTP body");
        return false;
    }

    size_t offset = 0;
    memcpy(body + offset, bodyStart.c_str(), bodyStart.length());
    offset += bodyStart.length();
    memcpy(body + offset, wavHeader, 44);
    offset += 44;
    memcpy(body + offset, audioBuffer, dataSize);
    offset += dataSize;
    memcpy(body + offset, bodyEnd.c_str(), bodyEnd.length());

    Serial.println("Sending to session/audio...");
    int httpCode = http.POST(body, totalLen);

    free(body);

    if (httpCode > 0) {
        Serial.printf("HTTP response code: %d\n", httpCode);
        if (httpCode == HTTP_CODE_OK) {
            String response = http.getString();
            Serial.println("Session audio response: " + response);
            http.end();
            return true;
        }
    } else {
        Serial.printf("HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
    }

    http.end();
    return false;
}

bool sendImageToSession() {
    // Capture frame
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("Camera capture failed");
        return false;
    }

    Serial.printf("Captured image for session: %dx%d, %d bytes\n", fb->width, fb->height, fb->len);
    unsigned long startTime = millis();

    // Use streaming upload instead of large memory allocation
    WiFiClient client;
    if (!client.connect(RELAY_IP, RELAY_PORT)) {
        Serial.println("Connection to relay server failed");
        esp_camera_fb_return(fb);
        return false;
    }

    String boundary = "----ESP32SessionImage";
    String header = "--" + boundary + "\r\n";
    header += "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n";
    header += "Content-Type: image/jpeg\r\n\r\n";
    String footer = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = header.length() + fb->len + footer.length();

    // Send HTTP request headers
    client.println("POST /session/image HTTP/1.1");
    client.printf("Host: %s:%d\r\n", RELAY_IP, RELAY_PORT);
    client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary.c_str());
    client.printf("Content-Length: %d\r\n", totalLen);
    client.println("Connection: close");
    client.println();

    // Send multipart header
    client.print(header);

    // Stream image data in chunks
    const size_t CHUNK_SIZE = 4096;
    size_t sent = 0;
    while (sent < fb->len) {
        size_t chunk = min(CHUNK_SIZE, fb->len - sent);
        size_t written = client.write(fb->buf + sent, chunk);
        if (written == 0) break;
        sent += written;
        yield();
    }

    // Send footer
    client.print(footer);

    // Return frame buffer immediately
    esp_camera_fb_return(fb);

    // Wait for response
    unsigned long timeout = millis() + 15000;
    while (client.connected() && !client.available() && millis() < timeout) {
        delay(10);
    }

    // Read response
    bool success = false;
    if (client.available()) {
        String statusLine = client.readStringUntil('\n');
        if (statusLine.indexOf("200") > 0 || statusLine.indexOf("201") > 0) {
            success = true;
        }
        Serial.printf("Session image response: %s\n", statusLine.c_str());

        // Read response body
        while (client.available()) {
            String line = client.readStringUntil('\n');
            if (line == "\r" || line == "") break;
        }
        if (client.available()) {
            String body = client.readString();
            Serial.printf("Response: %s\n", body.c_str());
        }
    }

    client.stop();

    unsigned long elapsed = millis() - startTime;
    Serial.printf("Session image upload %s in %lu ms\n", success ? "completed" : "failed", elapsed);

    return success;
}

bool sendSessionProcess() {
    Serial.println("Requesting session EXECUTE (Claude Code)...");

    HTTPClient http;
    http.begin(RELAY_URL_SESSION_EXECUTE);  // Use execute endpoint for Claude Code actions
    http.setTimeout(60000);   // 60 second timeout for Claude Code execution
    http.addHeader("Content-Type", "application/json");

    // Send empty JSON body to trigger execution
    int httpCode = http.POST("{}");

    if (httpCode > 0) {
        Serial.printf("HTTP response code: %d\n", httpCode);
        if (httpCode == HTTP_CODE_OK) {
            String response = http.getString();
            Serial.println("Session execute response: " + response);
            http.end();
            return true;
        }
    } else {
        Serial.printf("HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
    }

    http.end();
    return false;
}

void setLED(uint32_t color) {
    led.setPixelColor(0, color);
    led.show();
}

// Non-blocking flash - call updateLED() in main loop
void flashLEDNonBlocking(uint32_t color, int times, int delayMs, uint32_t returnColor) {
    ledState.color = color;
    ledState.returnColor = returnColor;
    ledState.flashCount = times * 2;  // Each flash = on + off
    ledState.flashDelay = delayMs;
    ledState.lastTime = millis();
    ledState.isOn = false;
    ledState.active = true;
    setLED(color);  // Start with LED on
    ledState.isOn = true;
}

// Process non-blocking LED animations - call in main loop
void updateLED() {
    unsigned long now = millis();

    // Handle pending delayed LED change
    if (hasPendingLED && now >= pendingLEDTime) {
        setLED(pendingLEDColor);
        hasPendingLED = false;
    }

    // Handle flash sequence
    if (!ledState.active) return;

    if (now - ledState.lastTime >= ledState.flashDelay) {
        ledState.lastTime = now;

        if (ledState.isOn) {
            setLED(0);  // Turn off
            ledState.flashCount--;
        } else {
            setLED(ledState.color);  // Turn on
        }
        ledState.isOn = !ledState.isOn;

        // Check if flash sequence complete
        if (ledState.flashCount <= 0) {
            ledState.active = false;
            setLED(ledState.returnColor);  // Return to specified color
        }
    }
}

// Set LED color after a delay (non-blocking)
void setLEDDelayed(uint32_t color, unsigned long delayMs) {
    pendingLEDColor = color;
    pendingLEDTime = millis() + delayMs;
    hasPendingLED = true;
}

void flashLED(uint32_t color, int times, int delayMs) {
    for (int i = 0; i < times; i++) {
        setLED(color);
        delay(delayMs);
        setLED(0);
        delay(delayMs);
    }
}

void breatheLED(uint32_t color, int duration) {
    int steps = duration / 20;
    for (int i = 0; i < steps; i++) {
        float brightness = (sin(i * 0.1) + 1) / 2;
        uint8_t r = ((color >> 16) & 0xFF) * brightness;
        uint8_t g = ((color >> 8) & 0xFF) * brightness;
        uint8_t b = (color & 0xFF) * brightness;
        led.setPixelColor(0, led.Color(r, g, b));
        led.show();
        delay(20);
    }
}

// DEPRECATED: Use non-blocking photo state machine instead
// Kept for reference but should not be called
void countdownLED(int seconds) {
    Serial.println("WARNING: countdownLED() is deprecated, use state machine instead");
}

// Single non-blocking LED flash
void flashLEDOnce(uint32_t color, int durationMs) {
    setLED(color);
    delay(durationMs);
    setLED(0);
}

// =============================================================================
// NON-BLOCKING PHOTO STATE MACHINE
// Handles 3-second countdown without blocking keyword detection
// =============================================================================
void handlePhotoStateMachine() {
    switch (photoState) {
        case PHOTO_STATE_COUNTDOWN:
            {
                // NOTE: Inference task keeps I2S DMA buffer drained (sole reader)
                // No need to flush here anymore

                unsigned long now = millis();

                // Update countdown every second
                if (now - photoCountdownLastTick >= 1000) {
                    photoCountdownLastTick = now;
                    photoCountdownValue--;

                    if (photoCountdownValue > 0) {
                        Serial.printf("  %d...\n", photoCountdownValue);
                        // Brief flash for visual feedback (non-blocking)
                        setLED(LED_COLOR_ERROR);
                        setLEDDelayed(0, 100);  // Turn off after 100ms
                    } else {
                        // Countdown complete - transition to capturing
                        Serial.println("  Capture!");
                        photoState = PHOTO_STATE_CAPTURING;
                        setLED(LED_COLOR_CAPTURING);  // White
                    }
                }
            }
            break;

        case PHOTO_STATE_CAPTURING:
            {
                // NOTE: Inference task keeps I2S DMA buffer drained (sole reader)
                // Capture and send image to SESSION (buffered, not processed yet)
                if (photoPending) {
                    photoPending = false;

                    if (sendImageToSession()) {
                        Serial.println("Image buffered in session - say POST to process");
                        flashLED(LED_COLOR_SUCCESS, 2, 150);
                    } else {
                        flashLED(LED_COLOR_ERROR, 3, 200);
                    }

                    // Return to idle with extended cooldown
                    setLED(LED_COLOR_IDLE);
                    photoState = PHOTO_STATE_IDLE;
                    lastKeywordTime = millis();  // Reset cooldown AFTER photo completes

                    // Reset inference again to clear any audio accumulated during capture
                    resetInferenceBuffers();

                    Serial.println("Photo complete - extended cooldown active");
                }
            }
            break;

        case PHOTO_STATE_IDLE:
        default:
            // DO NOT read I2S here - let checkKeywordInIdle() handle audio
            // The DMA ring buffer naturally discards old samples (safe overflow)
            break;
    }
}

// =============================================================================
// WAKE WORD DETECTION (Edge Impulse)
// =============================================================================

// Properly reset all inference buffers to prevent stale audio contamination
void resetInferenceBuffers() {
    if (!wakeWordInitialized) return;

    // Reset buffer indices
    inference.buf_select = 0;
    inference.buf_count = 0;
    inference.buf_ready = 0;

    // Clear actual buffer contents to remove stale audio
    memset(inference.buffers[0], 0, EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));
    memset(inference.buffers[1], 0, EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));

    // Flush I2S DMA buffer to prevent old audio from being read
    i2s_zero_dma_buffer(I2S_PORT);

    // Reset the continuous classifier's internal temporal smoothing state
    // This prevents the classifier from re-detecting the same keyword
    // due to persistent averaging state from before the action was taken
    run_classifier_init();

    // Start warmup counter - skip next N inferences while classifier stabilizes
    // The continuous classifier needs EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW (typically 4)
    // slices of fresh audio before temporal smoothing produces reliable results
    inferenceWarmupCounter = -(EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW + 2);  // 6 slices total

    Serial.println("Inference buffers reset (warmup started)");
}

void initWakeWord() {
    Serial.println("Initializing wake word detection...");

    // Allocate inference buffers
    inference.buffers[0] = (signed short *)ps_malloc(EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));
    inference.buffers[1] = (signed short *)ps_malloc(EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));

    if (inference.buffers[0] == NULL || inference.buffers[1] == NULL) {
        Serial.println("ERROR: Failed to allocate wake word buffers!");
        wakeWordInitialized = false;
        return;
    }

    // CRITICAL: Clear buffers to prevent inference on garbage data
    memset(inference.buffers[0], 0, EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));
    memset(inference.buffers[1], 0, EI_CLASSIFIER_SLICE_SIZE * sizeof(signed short));

    inference.buf_select = 0;
    inference.buf_count = 0;
    inference.n_samples = EI_CLASSIFIER_SLICE_SIZE;
    inference.buf_ready = 0;

    // Initialize the classifier
    run_classifier_init();

    wakeWordInitialized = true;
    Serial.printf("Keyword detection ready (slice size: %d samples)\n", EI_CLASSIFIER_SLICE_SIZE);
    Serial.printf("Model params: raw_samples=%d, slice_size=%d, slices_per_window=%d, threshold=%.2f, post_threshold=%.2f\n",
                  EI_CLASSIFIER_RAW_SAMPLE_COUNT, EI_CLASSIFIER_SLICE_SIZE,
                  EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW, WAKE_WORD_THRESHOLD, POST_KEYWORD_THRESHOLD);
    Serial.println("Keywords: RECORD, STOP, CAPTURE, POST");
}

// Get audio data for Edge Impulse inference
static int microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr) {
    numpy::int16_to_float(&inference.buffers[inference.buf_select ^ 1][offset], out_ptr, length);
    return 0;
}

bool checkWakeWord() {
    if (!wakeWordInitialized) return false;

    // Read audio samples for wake word detection
    size_t bytesRead = 0;
    i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 10);

    if (bytesRead == 0) return false;

    // Scale audio (PDM mic is quiet)
    size_t samplesRead = bytesRead / sizeof(int16_t);
    for (size_t i = 0; i < samplesRead; i++) {
        wakeWordSampleBuffer[i] = (int16_t)(wakeWordSampleBuffer[i] * 8);
    }

    // Fill inference buffer
    for (size_t i = 0; i < samplesRead && inference.buf_count < inference.n_samples; i++) {
        inference.buffers[inference.buf_select][inference.buf_count++] = wakeWordSampleBuffer[i];
    }

    // Check if buffer is full
    if (inference.buf_count >= inference.n_samples) {
        inference.buf_select ^= 1;
        inference.buf_count = 0;
        inference.buf_ready = 1;
    }

    // Run inference if buffer ready
    if (inference.buf_ready) {
        inference.buf_ready = 0;

        signal_t signal;
        signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
        signal.get_data = &microphone_audio_signal_get_data;

        ei_impulse_result_t result = { 0 };
        EI_IMPULSE_ERROR err = run_classifier_continuous(&impulse_handle_936625_1, &signal, &result, false);

        if (err != EI_IMPULSE_OK) {
            return false;
        }

        // Check for "on" keyword
        for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
            if (strcmp(result.classification[ix].label, "on") == 0) {
                if (result.classification[ix].value > WAKE_WORD_THRESHOLD) {
                    Serial.printf("Wake word confidence: %.2f\n", result.classification[ix].value);
                    return true;
                }
            }
        }
    }

    return false;
}

// Check for any keyword when idle (reads from I2S) with confidence smoothing
// Returns: -1=none, 0=noise, 1=off, 2=on, 3=unknown
int checkKeywordInIdle() {
    if (!wakeWordInitialized || !i2sInitialized) return -1;

    // Read audio samples for keyword detection
    // Use longer timeout (50ms) to ensure we get consistent audio samples
    size_t bytesRead = 0;
    i2s_read(I2S_PORT, wakeWordSampleBuffer, sizeof(wakeWordSampleBuffer), &bytesRead, 50);

    if (bytesRead == 0) {
        // No audio available - this is normal if loop runs faster than audio arrives
        return -1;
    }

    // Scale audio (PDM mic is quiet)
    size_t samplesRead = bytesRead / sizeof(int16_t);
    for (size_t i = 0; i < samplesRead; i++) {
        wakeWordSampleBuffer[i] = (int16_t)(wakeWordSampleBuffer[i] * 8);
    }

    // Fill inference buffer
    for (size_t i = 0; i < samplesRead && inference.buf_count < inference.n_samples; i++) {
        inference.buffers[inference.buf_select][inference.buf_count++] = wakeWordSampleBuffer[i];
    }

    // Check if buffer is full
    if (inference.buf_count >= inference.n_samples) {
        inference.buf_select ^= 1;
        inference.buf_count = 0;
        inference.buf_ready = 1;
    }

    // Run inference if buffer ready
    if (inference.buf_ready) {
        inference.buf_ready = 0;
        
        #if DEBUG_KEYWORD_DETECTION
        static int inferenceCount = 0;
        inferenceCount++;
        if (inferenceCount % 20 == 0) {
            Serial.printf("[Inference] Running... (count=%d, buf_select=%d)\n", inferenceCount, inference.buf_select);
        }
        #endif

        signal_t signal;
        signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
        signal.get_data = &microphone_audio_signal_get_data;

        ei_impulse_result_t result = { 0 };
        EI_IMPULSE_ERROR err = run_classifier_continuous(&impulse_handle_936625_1, &signal, &result, false);

        if (err != EI_IMPULSE_OK) {
            Serial.printf("[Inference] ERROR: %d\n", err);
            return -1;
        }

        // Warmup period: skip inference results while classifier stabilizes after reset
        if (inferenceWarmupCounter < 0) {
            inferenceWarmupCounter++;
            return -1;  // Ignore result during warmup
        }

        // Extract all class confidences
        float captureConf = 0.0f, noiseConf = 0.0f, postConf = 0.0f;
        float recordConf = 0.0f, stopConf = 0.0f, unknownConf = 0.0f;

        for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
            const char* label = result.classification[ix].label;
            float value = result.classification[ix].value;
            // Order: noise first for early rejection, then actionable keywords
            if (strcmp(label, "noise") == 0) noiseConf = value;
            else if (strcmp(label, "unknown") == 0) unknownConf = value;
            else if (strcmp(label, "capture") == 0) captureConf = value;
            else if (strcmp(label, "stop") == 0) stopConf = value;
            else if (strcmp(label, "post") == 0) postConf = value;
            else if (strcmp(label, "record") == 0) recordConf = value;
        }

        // =============================================================
        // PER-KEYWORD THRESHOLD DETECTION
        // =============================================================
        int bestIdx = -1;
        float bestValue = 0.0f;
        float usedThreshold = 0.0f;

        // Check each keyword against its specific threshold
        struct {
            int idx;
            float conf;
            float threshold;
            float noiseReject;
        } candidates[] = {
            // Order: capture, stop, post, record (record last - most false positive prone)
            { KW_CAPTURE, captureConf, THRESHOLD_CAPTURE, NOISE_REJECT_CAPTURE },
            { KW_STOP,    stopConf,    THRESHOLD_STOP,    NOISE_REJECT_STOP },
            { KW_POST,    postConf,    THRESHOLD_POST,    NOISE_REJECT_POST },
            { KW_RECORD,  recordConf,  THRESHOLD_RECORD,  NOISE_REJECT_RECORD },
        };

        for (int i = 0; i < 4; i++) {
            if (candidates[i].conf >= candidates[i].threshold) {
                float margin = candidates[i].conf - candidates[i].threshold;
                if (margin > (bestValue - usedThreshold)) {
                    bestIdx = candidates[i].idx;
                    bestValue = candidates[i].conf;
                    usedThreshold = candidates[i].threshold;
                }
            }
        }

        // Apply per-keyword noise rejection
        if (bestIdx >= 0) {
            float noiseRejectThreshold = NOISE_REJECTION_THRESHOLD;
            for (int i = 0; i < 4; i++) {
                if (candidates[i].idx == bestIdx) {
                    noiseRejectThreshold = candidates[i].noiseReject;
                    break;
                }
            }

            if (noiseConf > noiseRejectThreshold ||
                unknownConf > noiseRejectThreshold ||
                (noiseConf + unknownConf) > 0.60f) {
                const char* kwNames[] = {"CAPTURE", "noise", "POST", "RECORD", "STOP", "unknown"};
                Serial.printf("REJECTED %s: noise=%.2f unk=%.2f (reject_thr=%.2f)\n",
                    kwNames[bestIdx], noiseConf, unknownConf, noiseRejectThreshold);
                bestIdx = -1;
            }
        }

        // Report detected keywords
        if (bestIdx >= 0) {
            const char* kwNames[] = {"CAPTURE", "noise", "POST", "RECORD", "STOP", "unknown"};
            Serial.printf("KEYWORD DETECTED: %s (%.2f >= %.2f)\n", kwNames[bestIdx], bestValue, usedThreshold);
        }

        return bestIdx;
    }

    return -1;
}

// =============================================================================
// KEYWORD DETECTION HELPERS (for both ON start and OFF stop)
// =============================================================================

// Feed audio samples to the inference buffer
void feedToInference(int16_t *samples, size_t count) {
    if (!wakeWordInitialized) return;

    // Fill inference buffer
    for (size_t i = 0; i < count && inference.buf_count < inference.n_samples; i++) {
        inference.buffers[inference.buf_select][inference.buf_count++] = samples[i];
    }

    // Check if buffer is full - swap buffers
    if (inference.buf_count >= inference.n_samples) {
        inference.buf_select ^= 1;
        inference.buf_count = 0;
        inference.buf_ready = 1;
    }
}

// Run inference and return detected keyword with confidence smoothing
// Returns: -1=none/not ready, 0=noise, 1=off, 2=on, 3=unknown
int checkKeywordInference() {
    if (!wakeWordInitialized || !inference.buf_ready) return -1;

    inference.buf_ready = 0;

    signal_t signal;
    signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
    signal.get_data = &microphone_audio_signal_get_data;

    ei_impulse_result_t result = { 0 };
    EI_IMPULSE_ERROR err = run_classifier_continuous(&impulse_handle_936625_1, &signal, &result, false);

    if (err != EI_IMPULSE_OK) {
        return -1;
    }

    // Warmup period: skip inference results while classifier stabilizes after reset
    // This prevents the "record" word from being stuck in temporal averaging
    if (inferenceWarmupCounter < 0) {
        inferenceWarmupCounter++;
        return -1;  // Ignore result during warmup
    }

    // Use raw confidence directly (no smoothing) for better responsiveness
    int bestIdx = -1;
    float bestValue = WAKE_WORD_THRESHOLD;

    // Map labels to keyword indices
    // Labels: { "capture", "noise", "post", "record", "stop", "unknown" }
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        if (result.classification[ix].value > bestValue) {
            bestValue = result.classification[ix].value;
            const char* label = result.classification[ix].label;
            // Order: noise first for early rejection, then actionable keywords
            if (strcmp(label, "noise") == 0) bestIdx = KW_NOISE;
            else if (strcmp(label, "unknown") == 0) bestIdx = KW_UNKNOWN;
            else if (strcmp(label, "capture") == 0) bestIdx = KW_CAPTURE;
            else if (strcmp(label, "stop") == 0) bestIdx = KW_STOP;
            else if (strcmp(label, "post") == 0) bestIdx = KW_POST;
            else if (strcmp(label, "record") == 0) bestIdx = KW_RECORD;
        }
    }

    // During recording, only look for STOP keyword - ignore others including false "record" detections
    if (bestIdx == KW_STOP) {
        Serial.printf("KEYWORD during recording: stop (confidence=%.2f)\n", bestValue);
    }

    return bestIdx;
}
