"""
PlatformIO pre-build script: inject firmware version as a C macro.

Version resolution order (first match wins):
  1. FIRMWARE_VERSION environment variable  – set by CI / release workflow
  2. `git describe --tags --always --dirty` – works in a developer checkout
  3. Fallback: "dev"

The macro FIRMWARE_VERSION is available in all translation units via version.h,
but it is also injected here so that code that #define-checks it at compile time
gets the real value even if version.h is not explicitly included.
"""

Import("env")  # noqa: F821  (PlatformIO SCons environment)

import os
import subprocess


def _resolve_version() -> str:
    # 1. Explicit environment variable (CI / release workflow)
    ver = os.environ.get("FIRMWARE_VERSION", "").strip()
    if ver:
        return ver

    # 2. Git describe
    try:
        ver = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            stderr=subprocess.DEVNULL,
            cwd=env.subst("$PROJECT_DIR"),  # noqa: F821
        ).decode().strip()
        if ver:
            return ver
    except Exception:
        pass

    return "dev"


version = _resolve_version()
print(f"[set_version] FIRMWARE_VERSION = {version}")

# Inject as a C string macro.
# The value must arrive at the compiler as: -DFIRMWARE_VERSION=\"v1.2.3\"
# SCons strips one level of escaping, so we need \\\" here to produce \" in the
# compiler command line, which in turn produces the literal string "v1.2.3" in C.
env.Append(CPPDEFINES=[("FIRMWARE_VERSION", f'\\"{version}\\"')])  # noqa: F821
