# SoftAP Mode Implementation Notes

**Date:** March 26, 2026
**Status:** Partially implemented, requires captive portal fix
**Branch:** wifi

---

## Overview

Attempted to implement WiFi SoftAP (Access Point) mode on the ESP32, where the device creates its own WiFi network and the laptop connects directly to it. This eliminates dependency on mobile hotspots which proved unreliable.

## Architecture

**Current (Station Mode):**
```
ESP32 --[WiFi STA]--> iPhone Hotspot --[Internet]--> Laptop
                      (unreliable)
```

**Target (SoftAP Mode):**
```
ESP32 [192.168.4.1] --[WiFi AP "SmartBracelet"]--> Laptop [192.168.4.2]
                                                   (direct, reliable)
```

---

## Code Implemented (March 26, 2026)

### 1. config.h Additions

```cpp
// =============================================================================
// WiFi Mode Selection
// =============================================================================
// Set to 1 for SoftAP mode (ESP32 creates WiFi network, laptop connects to it)
// Set to 0 for Station mode (ESP32 connects to existing WiFi network)
#define WIFI_MODE_SOFTAP        1

// =============================================================================
// WiFi SoftAP Configuration (when WIFI_MODE_SOFTAP = 1)
// ESP32 creates its own WiFi network - laptop connects directly
// =============================================================================
#define SOFTAP_SSID             "SmartBracelet"
#define SOFTAP_PASSWORD         "spatial123"      // Min 8 characters for WPA2
#define SOFTAP_CHANNEL          6                 // WiFi channel (1, 6, or 11 recommended)
#define SOFTAP_MAX_CLIENTS      2                 // Max simultaneous connections
#define SOFTAP_HIDDEN           0                 // 0 = broadcast SSID, 1 = hidden

// SoftAP IP configuration (ESP32's address)
#define SOFTAP_LOCAL_IP         192, 168, 4, 1
#define SOFTAP_GATEWAY_IP       192, 168, 4, 1
#define SOFTAP_SUBNET_MASK      255, 255, 255, 0

// When laptop connects to ESP32's network, it gets 192.168.4.2 via DHCP
// The relay server running on laptop binds to that IP
#define RELAY_IP_SOFTAP         "192.168.4.2"

// =============================================================================
// Relay Server Configuration (auto-selected based on WiFi mode)
// =============================================================================
#if WIFI_MODE_SOFTAP
    #define RELAY_IP        RELAY_IP_SOFTAP
#else
    #define RELAY_IP        RELAY_IP_STATION
#endif
```

### 2. main.cpp - setupSoftAP() Function

```cpp
// =============================================================================
// SOFTAP MODE SETUP
// ESP32 creates its own WiFi network - laptop connects directly to it
// =============================================================================
#if WIFI_MODE_SOFTAP
void setupSoftAP() {
    Serial.println("\n========================================");
    Serial.println("Initializing WiFi SoftAP (Hotspot Mode)");
    Serial.println("========================================");

    setLED(LED_COLOR_CONNECTING);

    // Set WiFi mode to AP-only
    WiFi.mode(WIFI_AP);

    // Configure static IP before starting AP (CRITICAL: must be before softAP())
    IPAddress local_IP(SOFTAP_LOCAL_IP);
    IPAddress gateway(SOFTAP_GATEWAY_IP);
    IPAddress subnet(SOFTAP_SUBNET_MASK);

    if (!WiFi.softAPConfig(local_IP, gateway, subnet)) {
        Serial.println("ERROR: Failed to configure AP IP!");
        flashLED(LED_COLOR_ERROR, 5, 200);
        ESP.restart();
    }

    Serial.println("AP IP configuration set:");
    Serial.printf("  Local IP: %s\n", local_IP.toString().c_str());
    Serial.printf("  Gateway: %s\n", gateway.toString().c_str());
    Serial.printf("  Subnet: %s\n", subnet.toString().c_str());

    // Start SoftAP
    bool apStarted = WiFi.softAP(
        SOFTAP_SSID,
        SOFTAP_PASSWORD,
        SOFTAP_CHANNEL,
        SOFTAP_HIDDEN,
        SOFTAP_MAX_CLIENTS
    );

    if (!apStarted) {
        Serial.println("ERROR: Failed to start SoftAP!");
        flashLED(LED_COLOR_ERROR, 5, 200);
        ESP.restart();
    }

    // Disable WiFi power save for reliability
    esp_wifi_set_ps(WIFI_PS_NONE);

    // Register connection event handlers for client connect/disconnect
    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        Serial.print("[AP] Client connected - MAC: ");
        for (int i = 0; i < 6; i++) {
            Serial.printf("%02X", info.wifi_ap_staconnected.mac[i]);
            if (i < 5) Serial.print(":");
        }
        Serial.println();
        Serial.printf("[AP] Total connected clients: %d\n", WiFi.softAPgetStationNum());
    }, ARDUINO_EVENT_WIFI_AP_STACONNECTED);

    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        Serial.print("[AP] Client disconnected - MAC: ");
        for (int i = 0; i < 6; i++) {
            Serial.printf("%02X", info.wifi_ap_stadisconnected.mac[i]);
            if (i < 5) Serial.print(":");
        }
        Serial.println();
        Serial.printf("[AP] Total connected clients: %d\n", WiFi.softAPgetStationNum());
    }, ARDUINO_EVENT_WIFI_AP_STADISCONNECTED);

    wifiConnected = true;  // SoftAP is always "connected" once started

    Serial.println("\nSoftAP Started Successfully!");
    Serial.printf("SSID: %s\n", SOFTAP_SSID);
    Serial.printf("Password: %s\n", SOFTAP_PASSWORD);
    Serial.printf("Channel: %d\n", SOFTAP_CHANNEL);
    Serial.printf("AP IP: %s\n", WiFi.softAPIP().toString().c_str());

    flashLED(LED_COLOR_SUCCESS, 3, 100);
}
#endif
```

### 3. main.cpp - setup() Modification

```cpp
// Initialize WiFi based on mode
#if WIFI_MODE_SOFTAP
    setupSoftAP();  // ESP32 creates WiFi network, laptop connects to it
#else
    connectWiFi();  // ESP32 connects to existing WiFi network
#endif
```

### 4. main.cpp - loop() Modification

```cpp
// Check WiFi connection based on mode
#if WIFI_MODE_SOFTAP
    // In SoftAP mode, check for connected clients periodically
    static unsigned long lastClientCheck = 0;
    if (millis() - lastClientCheck >= 5000) {  // Check every 5 seconds
        lastClientCheck = millis();
        uint8_t clients = WiFi.softAPgetStationNum();
        static uint8_t lastClientCount = 0;
        if (clients != lastClientCount) {
            lastClientCount = clients;
            if (clients == 0) {
                Serial.println("[AP] Waiting for laptop to connect...");
            } else {
                Serial.printf("[AP] %d client(s) connected - ready!\n", clients);
            }
        }
    }
#else
    // Station mode: existing reconnection logic
    ...
#endif
```

---

## Problem Encountered

**Symptom:** macOS connects to "SmartBracelet" WiFi for ~1 second, then automatically disconnects.

**Root Cause:** macOS Captive Portal Detection

When connecting to ANY WiFi network, macOS automatically:
1. Sends DNS query for `captive.apple.com`
2. Sends HTTP GET to `http://captive.apple.com/hotspot-detect.html`
3. Expects response: `"Success"` with HTTP 200
4. If check fails → marks network as "No Internet"
5. Auto-switches to a saved network that HAS internet

Since ESP32 SoftAP has no DNS server or web server, the captive portal check fails and macOS switches away.

---

## Future Work Required: Captive Portal Spoofing

To make SoftAP work reliably, the ESP32 must "fake" internet connectivity by responding to captive portal checks.

### Required Components

1. **DNS Server (port 53)** - Respond to ALL DNS queries with ESP32's IP (192.168.4.1)
2. **Web Server (port 80)** - Serve captive portal detection endpoints

### Captive Portal Endpoints to Implement

| Device | URL Path | Expected Response |
|--------|----------|-------------------|
| Apple (macOS/iOS) | `/hotspot-detect.html` | `"Success"` (HTTP 200) |
| Apple alternate | `/library/test/success.html` | `"Success"` (HTTP 200) |
| Android | `/generate_204` | HTTP 204 No Content |
| Windows 10 | `/ncsi.txt` | `"Microsoft NCSI"` |
| Windows 11 | `/connecttest.txt` | `"Microsoft Connect Test"` |
| Firefox | `/success.txt` | `"success"` |

### Implementation Approach

```cpp
#include <DNSServer.h>
#include <WebServer.h>

DNSServer dnsServer;
WebServer captivePortal(80);

void setupCaptivePortal() {
    // DNS server: redirect ALL queries to ESP32's IP
    dnsServer.start(53, "*", WiFi.softAPIP());

    // Web server: handle captive portal detection
    captivePortal.on("/hotspot-detect.html", HTTP_GET, []() {
        captivePortal.send(200, "text/plain", "Success");
    });

    captivePortal.on("/generate_204", HTTP_GET, []() {
        captivePortal.send(204, "", "");
    });

    captivePortal.on("/connecttest.txt", HTTP_GET, []() {
        captivePortal.send(200, "text/plain", "Microsoft Connect Test");
    });

    // Catch-all for any other requests
    captivePortal.onNotFound([]() {
        captivePortal.send(200, "text/plain", "Success");
    });

    captivePortal.begin();
}

void loop() {
    dnsServer.processNextRequest();
    captivePortal.handleClient();
    // ... rest of loop
}
```

### Libraries Required

- `DNSServer` - Built into ESP32 Arduino core
- `WebServer` - Built into ESP32 Arduino core (or `ESPAsyncWebServer` for non-blocking)

### Considerations

1. **Port Conflict:** Relay server uses port 8080, captive portal uses port 80 - no conflict
2. **Memory:** Adds ~2-4KB RAM, ~10-15KB Flash
3. **CPU:** DNS/HTTP handling should be lightweight, but test with inference task
4. **Non-blocking:** Use `ESPAsyncWebServer` if blocking becomes an issue

### Alternative: User-Side Workaround

If captive portal spoofing is too complex, users can disable macOS captive portal detection:

```bash
# Disable captive portal detection (requires sudo)
sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.captive.control Active -bool false

# Re-enable later
sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.captive.control Active -bool true
```

Or disable auto-join on other networks:
- System Settings → WiFi → click (i) on each network → uncheck "Auto-Join"

---

## References

- [ESP32 Captive Portal Implementation](https://github.com/CDFER/Captive-Portal-ESP32)
- [Apple Captive Portal Detection](https://developer.apple.com/forums/thread/86589)
- [ESP32 SoftAP Documentation](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html)
- [Random Nerd Tutorials - ESP32 Access Point](https://randomnerdtutorials.com/esp32-access-point-ap-web-server/)

---

## Files Modified (to be reverted)

- `firmware/include/config.h` - Added SoftAP configuration
- `firmware/src/main.cpp` - Added setupSoftAP(), modified setup() and loop()
- `relay/relay_server.py` - Added SoftAP detection in startup

---

## Summary

SoftAP mode is technically working at the WiFi layer, but macOS auto-disconnects due to captive portal detection failure. The solution is to add DNS + HTTP servers that respond to captive portal checks, making macOS believe the network has internet access. This is a non-trivial addition that requires careful integration with the existing inference task architecture.
