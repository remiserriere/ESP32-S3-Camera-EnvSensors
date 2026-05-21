#pragma once
#include <cstdint>

namespace serial_cli {
    // Wait up to windowMs milliseconds for a key press on Serial.
    // If a key is received, enter the interactive configuration menu.
    // On exit (save or discard), returns and normal boot continues.
    //
    // Typical call in setup():
    //   device_config::load();
    //   serial_cli::offerConfigWindow(3000);
    void offerConfigWindow(uint32_t windowMs = 3000);
}
