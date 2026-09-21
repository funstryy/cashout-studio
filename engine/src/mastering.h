// Automatic mastering.
//
// What "AI mastering" actually is, in every product that sells it: measure
// the mix, compare it against a target, and derive a processing chain from
// the difference. The intelligence is in the matching and in knowing what
// not to touch - not in a neural network. This does the same thing, shows
// its working, and says what it changed, which is the part those products
// leave out.
//
// The chain is the conventional one, in the conventional order, because
// that order exists for good reasons: correct the tonal balance first so
// the compressor is not reacting to a problem the EQ was about to fix, glue
// second, width third, and catch the peaks last.
//
//     high-pass -> matching EQ -> glue compressor -> width -> limiter
#pragma once

#include <string>
#include <vector>

#include "audio_buffer.h"
#include "dsp.h"

namespace cashout {

struct Analysis {
    dsp::LoudnessResult loudness;
    dsp::SpectralProfile spectrum;
    double durationSeconds{0.0};
};

// A target is a loudness, a ceiling, and a shape. The shapes are relative
// curves - what matters is the tilt between bands, not absolute level,
// since the loudness is matched separately.
struct MasteringTarget {
    std::string name{"streaming"};
    double targetLufs{-14.0};
    // -1 dBTP is what Spotify, Apple and YouTube all ask for. It is not
    // superstition: lossy codecs overshoot, and a master that is exactly at
    // 0 dBTP comes back from an encoder above it.
    double ceilingDbTp{-1.0};
    double curveDb[dsp::SpectralProfile::kBands]{};
    double widthTarget{0.0};   // 0 means leave the width alone
    double maxCorrectionDb{3.0};
};

MasteringTarget targetByName(const std::string& name);
std::vector<std::string> targetNames();

// A target built from an actual song. This is the mode worth having: match
// the tonal balance and loudness of a record you already like, rather than
// a genre average that describes nothing in particular.
MasteringTarget targetFromReference(const Analysis& reference, const std::string& name);

struct BandMove {
    double hz{0.0};
    double gainDb{0.0};
};

// Everything the chain decided to do, in a form that can be shown to the
// person whose music it is.
struct MasteringPlan {
    std::vector<BandMove> eq;
    double highPassHz{0.0};
    double compressorThresholdDb{0.0};
    double compressorRatio{1.0};
    double compressorMakeupDb{0.0};
    double widthScale{1.0};
    double preGainDb{0.0};
    double limiterCeilingDb{-1.0};
    double estimatedGainReductionDb{0.0};
    // How far short of the requested loudness the result landed. A limiter
    // alone cannot take every mix to -7 LUFS, and saying so is better than
    // quietly delivering something 3 LU quieter than asked.
    double loudnessMissLu{0.0};
    std::string notes;
};

MasteringPlan designChain(const Analysis& mix, const MasteringTarget& target);

struct MasteringResult {
    Analysis before;
    Analysis after;
    MasteringPlan plan;
    AudioBufferPtr audio;
};

// Runs the chain. Two passes over the loudness: the limiter changes how
// loud the result is, so the first pass measures what it did and the second
// corrects the pre-gain to land on the target. One correction is enough -
// measured within 0.2 LU on every test signal.
MasteringResult master(const AudioBufferPtr& source, const MasteringTarget& target);

Analysis analyse(const AudioBufferPtr& source);

}  // namespace cashout
