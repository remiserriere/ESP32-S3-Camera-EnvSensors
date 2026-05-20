#pragma once
#include <cstdint>

struct UploadMetadata {
    const char* deviceId;    // e.g. "esp32-s3-env-01"
    int64_t     timestampS;  // Unix epoch seconds
    float       latitude;    // optional GPS; set 0 if unused
    float       longitude;
};

namespace uploader {
    // Connect to Wi-Fi. Returns true on success.
    bool wifiConnect();

    // Disconnect Wi-Fi to save power.
    void wifiDisconnect();

    // Upload JPEG bytes as multipart/form-data POST.
    //   - Part "metadata": JSON string
    //   - Part "image"   : JPEG binary (filename "photo.jpg")
    // Returns HTTP response code (200-299 = success, negative = network error).
    int uploadPhoto(const uint8_t* jpegBuf, size_t jpegLen, const UploadMetadata& meta);
}
