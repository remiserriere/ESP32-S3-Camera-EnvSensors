#pragma once

// Firmware version string.
// Injected at build time by scripts/set_version.py from the git tag or the
// FIRMWARE_VERSION environment variable (set by CI/CD).
// Falls back to "dev" for local builds that have no tag.
#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "dev"
#endif
