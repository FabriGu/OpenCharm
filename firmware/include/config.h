#ifndef CONFIG_H
#define CONFIG_H

// =============================================================================
// WiFi Configuration
// =============================================================================
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_PASSWORD"
// =============================================================================
// Relay Server Configuration
// =============================================================================
#define RELAY_IP        "YOUR_RELAY_IP"
// Run: ipconfig getifaddr en0 (on the machine running relay_server.py)
#define RELAY_PORT      8080

// Full URLs for HTTP POST
#define RELAY_URL_IMAGE  "http://" RELAY_IP ":8080/capture/image"
#define RELAY_URL_AUDIO  "http://" RELAY_IP ":8080/capture/audio"
#define RELAY_URL_HEALTH "http://" RELAY_IP ":8080/health"

// Session endpoints (for RECORD/STOP/CAPTURE/POST workflow)
#define RELAY_URL_SESSION_AUDIO   "http://" RELAY_IP ":8080/session/audio"
#define RELAY_URL_SESSION_IMAGE   "http://" RELAY_IP ":8080/session/image"
#define RELAY_URL_SESSION_PROCESS "http://" RELAY_IP ":8080/session/process"
#define RELAY_URL_SESSION_EXECUTE "http://" RELAY_IP ":8080/session/execute"

// =============================================================================
// Camera Configuration
// =============================================================================
// XIAO ESP32S3 Sense OV2640/OV5640 pins
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

// Camera settings
#define CAMERA_FRAME_SIZE   FRAMESIZE_VGA    // 640x480
#define CAMERA_JPEG_QUALITY 12               // 0-63, lower = better quality

// =============================================================================
// Audio Configuration (PDM Microphone)
// =============================================================================
#define PDM_CLK_PIN       42
#define PDM_DATA_PIN      41

#define AUDIO_SAMPLE_RATE     16000   // 16kHz for Whisper
#define AUDIO_BITS_PER_SAMPLE 16
#define AUDIO_CHANNELS        1       // Mono
#define MAX_RECORDING_SECONDS 30      // Maximum recording duration

// =============================================================================
// Button Configuration
// =============================================================================
#define BUTTON_PIN        2         // D1 = GPIO2, active LOW with pullup
#define DEBOUNCE_MS       50          // Debounce time
#define LONG_PRESS_MS     1000        // Long press threshold (1 second)

// =============================================================================
// LED Configuration (NeoPixel)
// =============================================================================
#define LED_PIN           1          // D0 = GPIO1
#define NUM_LEDS          1           // Single onboard LED

// LED colors (RGB)
#define LED_COLOR_IDLE      0x000011  // Dim blue
#define LED_COLOR_CONNECTING 0x110011 // Purple
#define LED_COLOR_CAPTURING  0xFFFFFF // White flash
#define LED_COLOR_SENDING    0x0000FF // Blue
#define LED_COLOR_SUCCESS    0x00FF00 // Green
#define LED_COLOR_ERROR      0xFF0000 // Red
#define LED_COLOR_RECORDING  0xFF0000 // Red breathing

// =============================================================================
// Timeouts
// =============================================================================
#define WIFI_CONNECT_TIMEOUT_MS  15000  // 15 seconds
#define HTTP_TIMEOUT_MS          30000  // 30 seconds for image/audio uploads
#define HEALTH_CHECK_INTERVAL_MS 30000  // 30 seconds between health checks
#define HEALTH_CHECK_TIMEOUT_MS  500    // 500ms max for health check (fast fail)
#define I2S_READ_TIMEOUT_MS      20     // 20ms timeout for I2S reads during recording

// =============================================================================
// Voice Activity Detection (VAD)
// =============================================================================
#define VAD_SILENCE_THRESHOLD   500     // RMS below this = silence (adjust as needed)
#define VAD_SILENCE_MS          1500    // ms of silence to stop recording
#define VAD_MIN_RECORDING_MS    1000    // Minimum recording time before VAD kicks in

#endif // CONFIG_H
