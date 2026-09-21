// Decoded audio, owned by the control thread, read by the audio thread.
//
// The lifetime rule: a buffer is built and filled entirely on the control
// thread, and only once it is complete does a raw pointer to it cross into
// the mixer. Nothing ever mutates a buffer that the audio thread can see,
// and nothing is freed until the mixer has handed the pointer back. That is
// what makes it safe for the audio callback to read these without a lock.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace cashout {

// Planar, because the mixer reads one channel at a time and interleaved
// audio makes every read a strided one. At 48kHz stereo the difference is
// small; with forty tracks and a plugin chain on each it stops being small.
struct AudioBuffer {
    std::vector<std::vector<float>> channels;
    std::uint32_t sampleRate{48000};

    std::size_t frames() const noexcept { return channels.empty() ? 0 : channels[0].size(); }
    int channelCount() const noexcept { return static_cast<int>(channels.size()); }

    double seconds() const noexcept {
        return sampleRate == 0 ? 0.0 : static_cast<double>(frames()) / sampleRate;
    }

    // Reads one channel with the ends clamped, so a caller that runs off the
    // end of a clip gets silence rather than a fault. Callers in the render
    // path check bounds anyway; this is the belt to that pair of braces.
    float sample(int channel, std::size_t frame) const noexcept {
        if (channels.empty()) return 0.0f;
        const auto& plane = channels[static_cast<std::size_t>(channel) % channels.size()];
        return frame < plane.size() ? plane[frame] : 0.0f;
    }
};

using AudioBufferPtr = std::shared_ptr<const AudioBuffer>;

// Resamples to the device rate on load rather than during playback.
//
// A DAW's library is a pile of files at whatever rate they were rendered -
// ACE-Step writes 44.1k, the audio.cpp server writes 48k - and a mixer that
// resamples per clip per block is both slower and worse sounding than one
// that does it once. Linear interpolation is enough here because this runs
// offline: anything audibly better belongs in the offline bounce, which is
// not what this path is for.
AudioBufferPtr resampleTo(const AudioBufferPtr& source, std::uint32_t targetRate);

}  // namespace cashout
