#include "uploader.h"
#include "../config.h"
#include "../device_config.h"
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <vector>

bool uploader::wifiConnect() {
    Serial.printf("[WiFi] Connecting to %s ...", g_deviceConfig.wifiSsid);
    WiFi.mode(WIFI_STA);
    WiFi.begin(g_deviceConfig.wifiSsid, g_deviceConfig.wifiPassword);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start > WIFI_TIMEOUT_MS) {
            Serial.println(" TIMEOUT\r\n");
            WiFi.disconnect(true);
            WiFi.mode(WIFI_OFF);
            return false;
        }
        delay(300);
        Serial.print(".");
    }
    Serial.printf(" OK (IP: %s)\r\n", WiFi.localIP().toString().c_str());
    return true;
}

void uploader::wifiDisconnect() {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    Serial.println("[WiFi] Disconnected");
}

int uploader::uploadPhoto(const uint8_t* jpegBuf, size_t jpegLen, const UploadMetadata& meta) {
    // Build JSON metadata string (device_id capped at 64 chars; buffer sized for worst case)
    char jsonBuf[384];
    snprintf(jsonBuf, sizeof(jsonBuf),
             "{\"device_id\":\"%.64s\",\"timestamp\":%lld,\"lat\":%.6f,\"lon\":%.6f}",
             meta.deviceId ? meta.deviceId : "", (long long)meta.timestampS,
             meta.latitude, meta.longitude);

    // Build multipart body manually (HTTPClient doesn't have built-in multipart support)
    const char* boundary = "----ESP32Boundary7a3b9c";
    // Pre-compute header size: boundary(~23) + fixed field headers(~200) + JSON payload
    String body;
    body.reserve(300 + strlen(jsonBuf));

    // Part 1: JSON metadata
    body += "--";  body += boundary; body += "\r\n";
    body += "Content-Disposition: form-data; name=\"metadata\"\r\n";
    body += "Content-Type: application/json\r\n\r\n";
    body += jsonBuf;
    body += "\r\n";

    // Part 2: JPEG image header
    body += "--";  body += boundary; body += "\r\n";
    body += "Content-Disposition: form-data; name=\"image\"; filename=\"photo.jpg\"\r\n";
    body += "Content-Type: image/jpeg\r\n\r\n";

    // Assemble into a single binary buffer for safe multipart encoding
    String tail = "\r\n--";
    tail += boundary; tail += "--\r\n";

    std::vector<uint8_t> multipart;
    multipart.reserve(body.length() + jpegLen + tail.length());
    multipart.insert(multipart.end(), (const uint8_t*)body.c_str(),
                     (const uint8_t*)body.c_str() + body.length());
    multipart.insert(multipart.end(), jpegBuf, jpegBuf + jpegLen);
    multipart.insert(multipart.end(), (const uint8_t*)tail.c_str(),
                     (const uint8_t*)tail.c_str() + tail.length());

    // POST
    HTTPClient http;
    http.begin(g_deviceConfig.uploadEndpoint);
    String contentType = "multipart/form-data; boundary=";
    contentType += boundary;
    http.addHeader("Content-Type", contentType);
    http.setTimeout(UPLOAD_TIMEOUT_MS);

    int code = http.POST(multipart.data(), multipart.size());

    if (code > 0) {
        Serial.printf("[Upload] HTTP %d\r\n", code);
    } else {
        Serial.printf("[Upload] Error: %s\r\n", http.errorToString(code).c_str());
    }

    http.end();
    return code;
}
