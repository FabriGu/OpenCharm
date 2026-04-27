/**
 * ESP32 Sample Recorder - Serial Mode
 *
 * Records audio and sends it over serial. Simple and reliable.
 *
 * Flow:
 *   1. Blue LED pulse = countdown (3 sec)
 *   2. Red LED = SPEAK NOW!
 *   3. Records for 2 seconds
 *   4. Sends audio over serial
 *   5. Green flash = done, repeat
 */

#include <Arduino.h>
#include <driver/i2s.h>
#include <Adafruit_NeoPixel.h>

// Hardware pins
#define PDM_CLK_PIN       42
#define PDM_DATA_PIN      41
#define LED_PIN           1
#define NUM_LEDS          1

// Audio config
#define SAMPLE_RATE       16000
#define RECORD_SECONDS    2.0f
#define RECORD_SAMPLES    ((int)(SAMPLE_RATE * RECORD_SECONDS))

// Timing
#define COUNTDOWN_SECONDS 3
#define PAUSE_BETWEEN_MS  2000

// I2S
#define I2S_PORT I2S_NUM_0

// LED Colors
#define COLOR_COUNTDOWN   0x000044  // Blue
#define COLOR_RECORDING   0xFF0000  // Red
#define COLOR_SENDING     0x0022FF  // Blue
#define COLOR_SUCCESS     0x00FF00  // Green
#define COLOR_WAITING     0x220022  // Purple

Adafruit_NeoPixel led(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);
int16_t* audioBuffer = nullptr;

void setLED(uint32_t color) {
    led.setPixelColor(0, color);
    led.show();
}

void flashLED(uint32_t color, int times, int delayMs) {
    for (int i = 0; i < times; i++) {
        setLED(color);
        delay(delayMs);
        setLED(0);
        delay(delayMs);
    }
}

void initI2S() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 1024,
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

    i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_PORT, &pin_config);
}

void countdownWithLED(int seconds) {
    for (int i = seconds; i > 0; i--) {
        Serial.printf("  %d...\n", i);

        // Pulsing blue
        for (int j = 0; j < 10; j++) {
            int brightness = (j < 5) ? (j * 10) : ((10 - j) * 10);
            led.setPixelColor(0, led.Color(0, 0, brightness + 20));
            led.show();
            delay(100);
        }
    }
}

void recordAudio() {
    Serial.println(">>> SPEAK NOW! <<<");
    setLED(COLOR_RECORDING);

    i2s_zero_dma_buffer(I2S_PORT);
    delay(50);

    size_t totalSamples = 0;
    size_t bytesRead = 0;
    unsigned long startTime = millis();

    while (totalSamples < RECORD_SAMPLES) {
        size_t samplesToRead = min((size_t)1024, (size_t)(RECORD_SAMPLES - totalSamples));

        i2s_read(I2S_PORT, audioBuffer + totalSamples,
                 samplesToRead * sizeof(int16_t), &bytesRead, portMAX_DELAY);

        totalSamples += bytesRead / sizeof(int16_t);

        // Breathing red LED
        float t = (millis() - startTime) / 200.0f;
        int brightness = 128 + (int)(127 * sin(t));
        led.setPixelColor(0, led.Color(brightness, 0, 0));
        led.show();
    }

    Serial.printf("Recorded %d samples\n", totalSamples);
}

void sendAudioSerial() {
    Serial.println("AUDIO_START");
    setLED(COLOR_SENDING);

    // Send as raw bytes (faster than text)
    Serial.write((uint8_t*)audioBuffer, RECORD_SAMPLES * sizeof(int16_t));

    Serial.println("\nAUDIO_END");
}

void setup() {
    Serial.begin(115200);
    delay(2000);

    Serial.println("\n========================================");
    Serial.println("  ESP32 Sample Recorder - Serial Mode");
    Serial.println("========================================\n");

    led.begin();
    led.setBrightness(150);
    setLED(COLOR_WAITING);

    audioBuffer = (int16_t*)ps_malloc(RECORD_SAMPLES * sizeof(int16_t));
    if (!audioBuffer) {
        Serial.println("ERROR: Failed to allocate buffer!");
        while(1) { flashLED(0xFF0000, 1, 500); }
    }
    Serial.printf("Buffer allocated: %d bytes\n", RECORD_SAMPLES * sizeof(int16_t));

    initI2S();
    Serial.println("Microphone ready");

    Serial.println("\nWatch the LED:");
    Serial.println("  BLUE pulse = Get ready");
    Serial.println("  RED = SPEAK NOW!");
    Serial.println("  GREEN flash = Done");
    Serial.println("\nStarting in 3 seconds...\n");

    delay(3000);
}

void loop() {
    // Auto-record loop - no waiting for commands

    // Countdown
    countdownWithLED(COUNTDOWN_SECONDS);

    // Record
    recordAudio();

    // Send over serial
    sendAudioSerial();

    // Success
    flashLED(COLOR_SUCCESS, 2, 100);

    delay(PAUSE_BETWEEN_MS);
}
