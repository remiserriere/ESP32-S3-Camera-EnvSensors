#pragma once
#include <cstddef>
#include <cstdint>

struct CameraFrame {
    uint8_t* buf;    // JPEG data pointer (owned by camera driver)
    size_t   len;    // byte length
    bool     valid;
    void*    _fb;    // opaque handle – do not use directly; pass to releaseFrame()
};

namespace camera_module {
    // Initialise the camera.  Returns true on success.
    // TODO: validate pin assignments against actual Freenove ESP32-S3 WROOM schematic.
    bool begin();

    // Capture a single JPEG frame.
    // The returned CameraFrame::buf is valid until releaseFrame() is called.
    CameraFrame capture();

    // Return the frame buffer to the driver.
    void releaseFrame(CameraFrame& frame);

    // Power down the camera to save energy.
    void end();
}
