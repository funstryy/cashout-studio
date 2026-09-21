// Measurement and filtering, written out rather than pulled in.
//
// Mastering is a measurement problem before it is a processing one: you
// cannot decide what a mix needs without knowing how loud it actually is,
// how its energy is spread across the spectrum, and how much headroom is
// left. All of that is specified precisely enough (ITU-R BS.1770 for
// loudness, EBU R128 for gating) that implementing it is a matter of
// following the standard, and doing so means the numbers here are the same
// numbers Spotify and YouTube will compute.
#pragma once

#include <cmath>
#include <complex>
#include <cstdint>
#include <vector>

namespace cashout {
namespace dsp {

constexpr double kPi = 3.14159265358979323846;

// A direct-form-II biquad. The whole EQ is built from these.
struct Biquad {
    double b0{1.0}, b1{0.0}, b2{0.0}, a1{0.0}, a2{0.0};
    double z1{0.0}, z2{0.0};

    void reset() { z1 = z2 = 0.0; }

    inline float process(float input) noexcept {
        const double x = input;
        const double y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        return static_cast<float>(y);
    }

    // Magnitude response at one frequency, used to check a designed curve
    // against the one that was asked for rather than assuming they match.
    double magnitudeAt(double hz, double sampleRate) const;
};

Biquad peaking(double hz, double q, double gainDb, double sampleRate);
Biquad lowShelf(double hz, double q, double gainDb, double sampleRate);
Biquad highShelf(double hz, double q, double gainDb, double sampleRate);
Biquad highPass(double hz, double q, double sampleRate);

// Radix-2 in place. Sizes here are powers of two by construction.
void fft(std::vector<std::complex<double>>& data);

// ---------------------------------------------------------------- loudness

// ITU-R BS.1770-4 K-weighting: a high shelf that approximates the head's
// acoustic effect, then a high-pass that discards subsonic energy no one
// hears but which would otherwise dominate the mean square.
//
// The published coefficients are for 48kHz, so everything is measured at
// 48kHz. Resampling once for measurement is exact and cheap; re-deriving
// the filters per rate is neither.
struct KWeighting {
    Biquad shelf;
    Biquad highpass;
    void prepare();
    void reset();
    inline float process(float x) noexcept { return highpass.process(shelf.process(x)); }
};

struct LoudnessResult {
    double integratedLufs{-70.0};
    double shortTermMaxLufs{-70.0};
    double loudnessRange{0.0};   // LRA, the spread of the loud parts
    double truePeakDb{-120.0};
    double samplePeakDb{-120.0};
    double crestDb{0.0};
};

// Integrated loudness with EBU R128's two gates: an absolute one at -70
// LUFS to discard silence, then a relative one 10 LU below the ungated mean
// so that a quiet intro does not drag the number down.
LoudnessResult measureLoudness(const std::vector<std::vector<float>>& channels,
                               std::uint32_t sampleRate);

// Inter-sample peak, 4x oversampled. A signal can sit at exactly 0 dBFS on
// every sample and still reconstruct above it between them, which is what
// clips a consumer DAC and what every streaming service checks for.
double truePeakDb(const std::vector<std::vector<float>>& channels);

// ---------------------------------------------------------------- spectrum

// Energy per band in dB, averaged over the whole file with a Hann window.
// Log-spaced because that is how the ear divides the spectrum and how a
// mastering engineer talks about it.
struct SpectralProfile {
    static constexpr int kBands = 10;
    double bandDb[kBands]{};
    double centres[kBands]{};
    // How much of the total energy is out of phase between the channels.
    // -1 is out of phase, 0 is wide, 1 is mono.
    double correlation{1.0};
    double widthRatio{0.0};  // side energy over mid energy
};

SpectralProfile measureSpectrum(const std::vector<std::vector<float>>& channels,
                                std::uint32_t sampleRate);

double linearToDb(double linear);
double dbToLinear(double db);


// ---------------------------------------------------------------- harmony

// Pitch-class energy: how much of each of the twelve notes is sounding,
// regardless of octave. This is the bridge from "a spectrum" to "music" -
// everything below, key and chords alike, is built on it.
struct Chroma {
    double bins[12]{};
    void normalise();
};

struct ChordSpan {
    int root{-1};          // 0 = C, 11 = B; -1 when nothing convincing
    bool minorQuality{false};
    double startSeconds{0.0};
    double seconds{0.0};
    double confidence{0.0};
};

struct HarmonyResult {
    int keyRoot{-1};
    bool keyMinor{false};
    double keyConfidence{0.0};
    Chroma average;
    std::vector<ChordSpan> chords;
    // Distinct chords over total spans: a progression that never leaves one
    // chord scores near zero, one that changes constantly scores near one.
    double harmonicMovement{0.0};
};

HarmonyResult analyseHarmony(const std::vector<std::vector<float>>& channels,
                             std::uint32_t sampleRate);

const char* noteName(int pitchClass);

}  // namespace dsp
}  // namespace cashout
