/**
 * OpenCharm - WiFi HTTP POST Firmware
 *
 * XIAO ESP32S3 Sense with OV2640 camera + PDM microphone
 *
 * Controls:
 * - Short press: capture image and send to relay server
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
#include <math.h>  // For sin() in LED breathing effect
#include "config.h"

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

static bool i2sInitialized = false;  // Track I2S driver status for safety

// =============================================================================
// NON-BLOCKING LED STATE MACHINE
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

// =============================================================================
// FREERTOS AUDIO TASK
// Dedicated task for audio capture - SOLE I2S READER
// =============================================================================
TaskHandle_t audioTaskHandle = NULL;

// SHARED RECORDING STATE: Audio task writes audio here when recording is active
volatile bool recordingActive = false;
volatile size_t recordingSamplesWritten = 0;
SemaphoreHandle_t recordingMutex = NULL;

// Connection state
bool wifiConnected = false;
unsigned long lastHealthCheck = 0;

// I2S configuration
#define I2S_PORT I2S_NUM_0

// Forward declarations
void initCamera();
void initAudio();
void initButton();
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
void updateLED();
void breatheLED(uint32_t color, int duration);
void audioTaskFunc(void* parameter);
void flashLEDOnce(uint32_t color, int durationMs);

// =============================================================================
// FREERTOS AUDIO CAPTURE TASK
// Sole I2S reader - runs on Core 1 to avoid WiFi contention on Core 0
// =============================================================================
void audioTaskFunc(void* parameter) {
    const TickType_t xDelay = pdMS_TO_TICKS(10);
    int16_t readBuffer[2048];

    Serial.println("[AudioTask] Started on Core " + String(xPortGetCoreID()));

    while (true) {
        if (!i2sInitialized || !recordingActive) {
            vTaskDelay(xDelay);
            continue;
        }

        size_t bytesRead = 0;
        i2s_read(I2S_PORT, readBuffer, sizeof(readBuffer), &bytesRead, 50);

        if (bytesRead == 0) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        size_t samplesRead = bytesRead / sizeof(int16_t);
        size_t maxSamples = AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS;

        // Scale samples by 8x (PDM microphone output is very quiet)
        for (size_t i = 0; i < samplesRead && i < 2048; i++) {
            int32_t scaled = (int32_t)readBuffer[i] * 8;
            if (scaled > 32767) scaled = 32767;
            if (scaled < -32768) scaled = -32768;
            readBuffer[i] = (int16_t)scaled;
        }

        // Copy scaled samples to shared audio buffer
        size_t currentPos = recordingSamplesWritten;
        size_t space = maxSamples - currentPos;
        size_t toCopy = (samplesRead < space) ? samplesRead : space;

        if (toCopy > 0 && audioBuffer != nullptr) {
            memcpy(audioBuffer + currentPos, readBuffer, toCopy * sizeof(int16_t));
            if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
                recordingSamplesWritten = currentPos + toCopy;
                xSemaphoreGive(recordingMutex);
            } else {
                recordingSamplesWritten = currentPos + toCopy;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1));  // Yield briefly
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println("\n=================================");
    Serial.println("OpenCharm - WiFi Firmware");
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

    // Create mutex for recording state synchronization
    recordingMutex = xSemaphoreCreateMutex();
    if (recordingMutex == NULL) {
        Serial.println("ERROR: Failed to create recording mutex!");
    }

    // Create dedicated audio capture task on Core 1 (WiFi runs on Core 0)
    BaseType_t taskResult = xTaskCreatePinnedToCore(
        audioTaskFunc,          // Task function
        "AudioCapture",         // Task name
        4096,                   // Stack size (smaller, no ML)
        NULL,                   // Parameters
        configMAX_PRIORITIES - 2,  // High priority
        &audioTaskHandle,       // Task handle
        1                       // Core 1
    );

    if (taskResult != pdPASS) {
        Serial.println("ERROR: Failed to create audio task!");
    } else {
        Serial.println("Audio capture task created on Core 1");
    }

    // Ready state
    setLED(LED_COLOR_IDLE);
    Serial.println("\nReady!");
    Serial.println("Controls:");
    Serial.println("  Short press: capture and send image");
    Serial.println("  Long press (hold): record audio, release to send");
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
    // BUTTON-TRIGGERED RECORDING
    // =========================================================================
    // Check for long press threshold while button is held
    if (buttonDown && !isRecording) {
        unsigned long heldDuration = millis() - buttonPressTime;

        if (heldDuration >= LONG_PRESS_MS) {
            // Start recording audio
            Serial.println("Long press -> starting audio recording...");
            Serial.println("Release button to stop and send.");
            isRecording = true;
            audioSamplesRecorded = 0;

            // Initialize shared recording state for audio task
            recordingSamplesWritten = 0;
            recordingActive = true;  // Tell audio task to start capturing

            setLED(LED_COLOR_RECORDING);
        }
    }

    // Continue recording while button is held
    // Audio is captured by audio task (sole I2S reader) - we just monitor state
    if (isRecording && buttonDown) {
        size_t maxSamples = (AUDIO_SAMPLE_RATE * MAX_RECORDING_SECONDS);

        // Update audioSamplesRecorded from audio task's counter (mutex protected)
        if (recordingMutex != NULL && xSemaphoreTake(recordingMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
            audioSamplesRecorded = recordingSamplesWritten;
            xSemaphoreGive(recordingMutex);
        } else {
            audioSamplesRecorded = recordingSamplesWritten;
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
            recordingActive = false;  // Stop audio task from recording
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
                recordingActive = false;  // Stop audio task from recording
                isRecording = false;
                // Get final count from audio task (mutex protected)
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
    if (!ledState.active) return;

    unsigned long now = millis();

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

// Single non-blocking LED flash
void flashLEDOnce(uint32_t color, int durationMs) {
    setLED(color);
    delay(durationMs);
    setLED(0);
}
