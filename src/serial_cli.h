#pragma once
#include <cstdint>

namespace serial_cli {
    // Wait up to g_deviceConfig.bootWindowSec seconds (minimum 5 s) for a key
    // press on Serial or a web form submission from the built-in HTTP config server.
    //
    // Behaviour:
    //   - Always waits at least 5 s (safety CLI window).
    //   - If bootWindowSec > 0: connects Wi-Fi and serves a graphical config page
    //     on http://<device-ip>/ for the full window duration.
    //   - Any key on Serial during the window opens the interactive CLI menu.
    //   - Web "Save and continue" / "Save and reboot" actions are handled.
    //   - On timeout, returns and normal boot continues.
    //
    // Typical call in setup() (after device_config::load()):
    //   serial_cli::offerConfigWindow();
    void offerConfigWindow();
}
