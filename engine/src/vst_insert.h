// A VST3 plugin running live in a track's signal path.
//
// This is the thing the browser build could never do. In the web studio a
// plugin is *printed*: the audio is shipped to a separate host process,
// rendered offline, and the clip is replaced with the result. That works,
// and it is also not what anybody means by using a plugin - you cannot turn
// a knob and hear it, you cannot A/B a setting, and an amp sim on a vocal
// take is a two-second round trip instead of an effect.
//
// Here the plugin's process() is called from the audio thread, on the same
// buffer the mixer is about to sum, with the same 2.67ms budget as
// everything else in the block.
#pragma once

#include <memory>
#include <string>

#include "mixer.h"

namespace cashout {

class VstInsert : public InsertProcessor {
public:
    ~VstInsert() override;

    // Control thread only. Loading a plugin means loading a DLL, which is
    // about the least real-time-safe thing there is.
    static std::unique_ptr<VstInsert> load(const std::string& path, double sampleRate,
                                           std::uint32_t maxFrames, std::string& error);

    void process(float* const* channels, int channelCount, std::uint32_t frames) override;
    void reset(double sampleRate, std::uint32_t maxFrames) override;
    const char* name() const override;

    // Samples of delay the plugin introduces. A lookahead compressor or any
    // pitch corrector reports one here, and a mixer that ignores it puts
    // that track late against the rest of the song.
    int latencySamples() const;

    // Sets a parameter from the control thread. The value reaches the
    // processor at the top of the next block, through the SDK's change
    // queues - setting it on the controller alone moves the plugin's own UI
    // and nothing else, which is a mistake this codebase has already made
    // once.
    void setParameter(std::uint32_t id, double normalised);

private:
    VstInsert();
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace cashout
