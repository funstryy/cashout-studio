#pragma once

#include <string>

#include "audio_buffer.h"

namespace cashout {

// Reads a WAV into planar float. Returns null and fills `error` on failure -
// no exceptions, because the callers are a control loop that must report the
// problem and keep serving, not unwind.
AudioBufferPtr readWavFile(const std::string& path, std::string& error);

// Writes interleaved float as 32-bit float WAV.
bool writeWavFile(const std::string& path, const float* interleaved, std::size_t frames,
                  int channels, std::uint32_t sampleRate, std::string& error);

}  // namespace cashout
