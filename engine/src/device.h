// The output device.
//
// This is the line between "a program that makes sound" and a studio tool.
// A browser hands audio to a mixer thread it does not control, at a buffer
// size it chooses, at whatever priority the tab happens to have: measured on
// this machine, WebAudio through WebView2 sits around 90-180ms of output
// latency and drops buffers whenever the page does layout. WASAPI in
// exclusive mode with an event-driven callback on an MMCSS Pro Audio thread
// gets the same machine into single-digit milliseconds and stays there while
// the UI is busy, because the UI is no longer in the path at all.
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace cashout {

struct DeviceInfo {
    std::string id;
    std::string name;
    bool isDefault{false};
    std::uint32_t mixRate{0};
    int mixChannels{0};
};

struct DeviceConfig {
    std::string deviceId;            // empty means the system default
    std::uint32_t sampleRate{48000};  // ignored in shared mode; the mixer follows the device
    std::uint32_t bufferFrames{256};  // requested; the device may round it
    bool exclusive{false};
};

// What the device actually gave us, which is rarely exactly what was asked.
struct DeviceStatus {
    bool open{false};
    bool exclusive{false};
    std::string deviceName;
    std::uint32_t sampleRate{0};
    std::uint32_t bufferFrames{0};
    int channels{0};
    double outputLatencyMs{0.0};
    std::uint64_t framesRendered{0};
    std::uint64_t xruns{0};
    // Worst and recent fraction of the callback budget spent rendering. Over
    // 1.0 means the render took longer than the audio it produced, which is
    // the definition of not keeping up.
    double loadPeak{0.0};
    double loadRecent{0.0};
};

// Called on the audio thread. Interleaved, `frames` long, `channels` wide.
// Everything this touches must be real-time safe.
using RenderCallback = std::function<void(float* out, std::uint32_t frames, int channels)>;

std::vector<DeviceInfo> enumerateOutputDevices();

class AudioDevice {
public:
    AudioDevice();
    ~AudioDevice();

    AudioDevice(const AudioDevice&) = delete;
    AudioDevice& operator=(const AudioDevice&) = delete;

    bool open(const DeviceConfig& config, RenderCallback callback, std::string& error);
    void close();

    DeviceStatus status() const;
    bool isOpen() const { return running_.load(std::memory_order_acquire); }

private:
    void threadMain();

    struct Impl;
    Impl* impl_;
    std::atomic<bool> running_{false};
};

}  // namespace cashout
