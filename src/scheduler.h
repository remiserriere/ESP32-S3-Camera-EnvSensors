#pragma once
#include <cstdint>

// Flags indicating which tasks are due on this wake cycle
struct TaskFlags {
    bool readDs18b20;
    bool readSht3x;
    bool readIna219;
    bool takePhoto;
};

namespace scheduler {
    // Evaluate which tasks are due right now.
    // Requires time_manager::init() to have been called first.
    TaskFlags evaluate();

    // After tasks are done, compute the number of seconds to sleep
    // until the next event (sensor read or photo window).
    uint32_t nextSleepSeconds(const TaskFlags& completed);

    // Enter deep sleep for 'seconds'. Does not return.
    [[noreturn]] void deepSleep(uint32_t seconds);
}
