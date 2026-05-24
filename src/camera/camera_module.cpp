#include "camera_module.h"
#include "../config.h"
#include <Arduino.h>
#include "esp_camera.h"

// TODO: The pin mapping below is based on community reports for Freenove ESP32-S3 WROOM.
//       Verify against the official Freenove schematic before flashing on hardware.
//       The camera I2C bus (SIOD/SIOC) is separate from the sensor I2C bus defined in config.h.
static const camera_config_t CAM_CONFIG = {
    .pin_pwdn     = CAM_PIN_PWDN,
    .pin_reset    = CAM_PIN_RESET,
    .pin_xclk     = CAM_PIN_XCLK,
    .pin_sccb_sda = CAM_PIN_SIOD,
    .pin_sccb_scl = CAM_PIN_SIOC,
    .pin_d7       = CAM_PIN_D7,
    .pin_d6       = CAM_PIN_D6,
    .pin_d5       = CAM_PIN_D5,
    .pin_d4       = CAM_PIN_D4,
    .pin_d3       = CAM_PIN_D3,
    .pin_d2       = CAM_PIN_D2,
    .pin_d1       = CAM_PIN_D1,
    .pin_d0       = CAM_PIN_D0,
    .pin_vsync    = CAM_PIN_VSYNC,
    .pin_href     = CAM_PIN_HREF,
    .pin_pclk     = CAM_PIN_PCLK,
    .xclk_freq_hz = 20000000,
    .ledc_timer   = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_JPEG,
    .frame_size   = FRAMESIZE_UXGA,   // 1600×1200 – reduce if PSRAM is insufficient
    .jpeg_quality = 12,               // 0–63 (lower = better quality, larger file)
    .fb_count     = 1,
    .fb_location  = CAMERA_FB_IN_PSRAM,
    .grab_mode    = CAMERA_GRAB_WHEN_EMPTY,
};

static bool _ready = false;

bool camera_module::begin() {
    esp_err_t err = esp_camera_init(&CAM_CONFIG);
    if (err != ESP_OK) {
        Serial.printf("[CAM] Init failed: 0x%x\r\n", err);
        return false;
    }
    _ready = true;
    Serial.println("[CAM] Initialised OK");
    return true;
}

CameraFrame camera_module::capture() {
    CameraFrame frame = {};
    if (!_ready) return frame;

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("[CAM] Frame capture failed");
        return frame;
    }

    if (fb->format != PIXFORMAT_JPEG) {
        Serial.println("[CAM] Unexpected pixel format");
        esp_camera_fb_return(fb);
        return frame;
    }

    frame.buf   = fb->buf;
    frame.len   = fb->len;
    frame.valid = true;
    frame._fb   = static_cast<void*>(fb);

    Serial.printf("[CAM] Captured %zu bytes\r\n", frame.len);
    return frame;
}

void camera_module::releaseFrame(CameraFrame& frame) {
    if (frame._fb) {
        esp_camera_fb_return(static_cast<camera_fb_t*>(frame._fb));
        frame._fb   = nullptr;
        frame.buf   = nullptr;
        frame.len   = 0;
        frame.valid = false;
    }
}

void camera_module::end() {
    if (_ready) {
        esp_camera_deinit();
        _ready = false;
        Serial.println("[CAM] Deinitialised");
    }
}
