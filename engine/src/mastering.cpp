#include "mastering.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>

namespace cashout {
namespace {

using dsp::SpectralProfile;

// The curves are relative tilts in dB across the ten bands, centred so they
// sum to roughly zero - they describe a shape, not a level.
//
// These come from measuring the tonal balance of commercial masters in each
// style rather than from taste. The rap curve, for instance, is not "more
// bass" as a preference: the records that define the sound sit 5-6dB hotter
// below 100Hz than a pop master does, and a mix that does not will sound
// thin next to them on the same playlist.
struct NamedCurve {
    const char* name;
    double lufs;
    double curve[SpectralProfile::kBands];
    double width;
};

// The baseline: what a finished record's spectrum actually looks like in
// these ten log-spaced bands.
//
// This is the correction that makes the whole thing work. Bands laid out
// with equal log width measure pink noise as flat, so a target of "all
// zeros" does not mean neutral - it means *pink*, and no piece of music has
// ever been pink. Matching to it told the EQ to cut 4dB out of the entire
// midrange of every mix it saw, which is what the first version did.
//
// Music has a broad rise through the low mids and rolls off at both ends.
// Genre curves below are departures from this, not from silence.
//
// Bands, in Hz:      40    78   151   295   573  1116  2172  4226  8222  16k
const double kMusicBaseline[SpectralProfile::kBands] =
    {-6.0, -1.0,  1.5,  2.0,  1.5,  0.0, -1.5, -3.0, -6.0, -11.0};

const NamedCurve kCurves[] = {
    {"streaming", -14.0, {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}, 0.0},
    {"rap",        -9.0, {4.5, 3.5, 1.0, -1.0, -1.5, -1.0, 0.0, 1.0, 1.5, 1.0}, 0.0},
    {"pop",       -10.0, {1.0, 1.5, 0.5, -0.5, -0.5, 0.0, 0.5, 1.5, 2.0, 1.5}, 0.0},
    {"warm",      -14.0, {2.0, 2.0, 1.5, 0.5, 0.0, -0.5, -1.0, -1.5, -1.5, -1.0}, 0.0},
    {"open",      -14.0, {-0.5, 0.0, -0.5, -1.0, -0.5, 0.0, 0.5, 1.5, 2.0, 2.5}, 0.0},
    {"apple",     -16.0, {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}, 0.0},
    {"club",       -7.0, {5.0, 4.0, 1.5, -1.0, -2.0, -1.5, 0.0, 1.5, 2.0, 1.0}, 0.0},
};

// Normalises a measured curve so only its shape remains. Without this, a
// quiet mix would read as needing a boost in every band at once, which the
// loudness stage handles far better than the EQ can.
void centre(double* bands, int count) {
    double mean = 0.0;
    for (int i = 0; i < count; ++i) mean += bands[i];
    mean /= count;
    for (int i = 0; i < count; ++i) bands[i] -= mean;
}

// The mastering-EQ move that is almost always right and almost never made
// by an automatic system: gently less, rather than more. A boost adds
// energy the limiter then has to take back out, so where a correction can
// be made by cutting the neighbours instead, it is.
double softenCorrection(double wanted, double limit) {
    const double sign = wanted < 0 ? -1.0 : 1.0;
    const double magnitude = std::fabs(wanted);
    if (magnitude <= 0.5) return 0.0;  // below this nobody can hear it
    // Compress the top of the range: a 9dB discrepancy is a mix problem, and
    // pretending an automatic EQ can fix it does more harm than applying a
    // firm-but-bounded 4dB and saying so.
    const double scaled = limit * std::tanh(magnitude / limit);
    return sign * scaled;
}

}  // namespace

std::vector<std::string> targetNames() {
    std::vector<std::string> names;
    for (const auto& curve : kCurves) names.emplace_back(curve.name);
    return names;
}

MasteringTarget targetByName(const std::string& name) {
    for (const auto& curve : kCurves) {
        if (name != curve.name) continue;
        MasteringTarget target;
        target.name = curve.name;
        target.targetLufs = curve.lufs;
        for (int b = 0; b < SpectralProfile::kBands; ++b) {
            target.curveDb[b] = kMusicBaseline[b] + curve.curve[b];
        }
        centre(target.curveDb, SpectralProfile::kBands);
        target.widthTarget = curve.width;
        return target;
    }
    return targetByName("streaming");
}

MasteringTarget targetFromReference(const Analysis& reference, const std::string& name) {
    MasteringTarget target;
    target.name = name.empty() ? "reference" : name;
    target.targetLufs = reference.loudness.integratedLufs;
    for (int b = 0; b < SpectralProfile::kBands; ++b) {
        target.curveDb[b] = reference.spectrum.bandDb[b];
    }
    centre(target.curveDb, SpectralProfile::kBands);
    target.widthTarget = reference.spectrum.widthRatio;
    return target;
}

Analysis analyse(const AudioBufferPtr& source) {
    Analysis analysis;
    if (!source) return analysis;
    analysis.loudness = dsp::measureLoudness(source->channels, source->sampleRate);
    analysis.spectrum = dsp::measureSpectrum(source->channels, source->sampleRate);
    analysis.durationSeconds = source->seconds();
    return analysis;
}

MasteringPlan designChain(const Analysis& mix, const MasteringTarget& target) {
    MasteringPlan plan;
    plan.limiterCeilingDb = target.ceilingDbTp;

    double measured[SpectralProfile::kBands];
    for (int b = 0; b < SpectralProfile::kBands; ++b) measured[b] = mix.spectrum.bandDb[b];
    centre(measured, SpectralProfile::kBands);

    for (int b = 0; b < SpectralProfile::kBands; ++b) {
        const double wanted = target.curveDb[b] - measured[b];
        const double move = softenCorrection(wanted, target.maxCorrectionDb);
        if (std::fabs(move) >= 0.25) {
            plan.eq.push_back({mix.spectrum.centres[b], move});
        }
    }

    // A high-pass only when there is something down there to remove.
    // Rumble below 30Hz is inaudible on every system anyone will play this
    // on, and it eats limiter headroom that the rest of the mix wants -
    // but cutting it out of a track that does not have it just loses the
    // bottom octave of an 808.
    if (mix.spectrum.bandDb[0] > mix.spectrum.bandDb[2] + 6.0) {
        plan.highPassHz = 28.0;
    }

    // Glue. The decision is driven by crest factor: a mix that is already
    // squashed gets left alone, and one with a lot of peak-to-average gets
    // a gentle ratio to hold it together before the limiter has to.
    const double crest = mix.loudness.crestDb;
    if (crest > 16.0) {
        plan.compressorRatio = 2.0;
        plan.compressorThresholdDb = mix.loudness.integratedLufs + 4.0;
    } else if (crest > 11.0) {
        plan.compressorRatio = 1.6;
        plan.compressorThresholdDb = mix.loudness.integratedLufs + 6.0;
    } else {
        // Already dense. Adding more would only make it smaller.
        plan.compressorRatio = 1.0;
        plan.compressorThresholdDb = 0.0;
    }
    if (plan.compressorRatio > 1.0) {
        plan.compressorMakeupDb = 0.0;  // the loudness stage handles level
    }

    // Width, only when a reference asked for one and only within a range
    // that cannot collapse the mono compatibility of the mix.
    if (target.widthTarget > 0.01 && mix.spectrum.widthRatio > 0.01) {
        const double ratio = target.widthTarget / mix.spectrum.widthRatio;
        plan.widthScale = (std::max)(0.7, (std::min)(1.4, ratio));
    }
    if (mix.spectrum.correlation < 0.2) {
        // Already close to out of phase. Widening further would make it
        // disappear on a phone speaker.
        plan.widthScale = (std::min)(plan.widthScale, 1.0);
    }

    plan.preGainDb = target.targetLufs - mix.loudness.integratedLufs;
    return plan;
}

namespace {

// The inter-sample peak at each sample, for both channels at once.
//
// Same 4x windowed-sinc interpolation the true-peak meter uses, so the
// limiter is measuring the ceiling with the same ruler the meter will
// afterwards. Anything else and the two disagree about whether the master
// passed.
std::vector<double> interSamplePeaks(const std::vector<float>& left,
                                     const std::vector<float>& right) {
    constexpr int kOversample = 4;
    constexpr int kTaps = 32;
    static std::vector<std::vector<double>> phases;
    if (phases.empty()) {
        phases.resize(kOversample, std::vector<double>(kTaps, 0.0));
        for (int phase = 0; phase < kOversample; ++phase) {
            const double offset = static_cast<double>(phase) / kOversample;
            double sum = 0.0;
            for (int t = 0; t < kTaps; ++t) {
                const double x = static_cast<double>(t - kTaps / 2) - offset;
                const double sinc = std::fabs(x) < 1e-9 ? 1.0
                                                        : std::sin(dsp::kPi * x) / (dsp::kPi * x);
                const double w = 0.42 - 0.5 * std::cos(2.0 * dsp::kPi * t / (kTaps - 1)) +
                                 0.08 * std::cos(4.0 * dsp::kPi * t / (kTaps - 1));
                phases[phase][t] = sinc * w;
                sum += sinc * w;
            }
            for (double& tap : phases[phase]) tap /= sum;
        }
    }

    const std::size_t n = left.size();
    std::vector<double> peaks(n, 0.0);
    for (std::size_t i = 0; i < n; ++i) {
        double peak = (std::max)(std::fabs(static_cast<double>(left[i])),
                                 std::fabs(static_cast<double>(right[i])));
        for (int phase = 1; phase < kOversample; ++phase) {
            double accL = 0.0, accR = 0.0;
            for (int t = 0; t < kTaps; ++t) {
                const std::ptrdiff_t index = static_cast<std::ptrdiff_t>(i) + t - kTaps / 2;
                if (index < 0 || static_cast<std::size_t>(index) >= n) continue;
                const std::size_t k = static_cast<std::size_t>(index);
                accL += left[k] * phases[phase][t];
                accR += right[k] * phases[phase][t];
            }
            peak = (std::max)(peak, (std::max)(std::fabs(accL), std::fabs(accR)));
        }
        peaks[i] = peak;
    }
    return peaks;
}

// A look-ahead limiter, computed offline over the whole signal.
//
// The streaming version of this was wrong in a way that only showed up at
// high gain: it computed the gain from the current sample and applied it to
// one five milliseconds older, so by the time a peak actually reached the
// output the release had already let the gain back up. At modest gain that
// is a third of a dB of overshoot; at the 26dB of pre-gain a -7 LUFS target
// asks for it was +2 dBTP over a ceiling of -1.
//
// Offline there is no reason to guess. The required gain is known for every
// sample, so take a sliding minimum over the look-ahead window and then let
// it recover. Because the applied gain is never above what each sample
// requires, the ceiling cannot be exceeded - that is a property of the
// construction rather than something to tune.
std::vector<double> limiterGains(const std::vector<double>& truePeaks, double ceiling,
                                 double sampleRate, double& maxReductionDb) {
    const std::size_t n = truePeaks.size();
    std::vector<double> required(n, 1.0);
    for (std::size_t i = 0; i < n; ++i) {
        if (truePeaks[i] > ceiling) required[i] = ceiling / truePeaks[i];
    }

    // Sliding minimum over the look-ahead, so the gain is already down
    // before the transient arrives rather than in response to it.
    const std::size_t lookahead = static_cast<std::size_t>(0.005 * sampleRate);
    std::vector<double> gains(n, 1.0);
    {
        // Monotonic deque: each sample enters and leaves once, so the whole
        // window minimum costs one pass rather than n*lookahead.
        std::vector<std::size_t> window;
        window.reserve(lookahead + 2);
        std::size_t head = 0;
        for (std::size_t i = 0; i < n; ++i) {
            const std::size_t limit = (std::min)(n - 1, i + lookahead);
            static std::size_t filled = 0;
            (void)filled;
            for (std::size_t j = (i == 0 ? 0 : (std::min)(n - 1, i - 1 + lookahead) + 1);
                 j <= limit; ++j) {
                while (window.size() > head && required[window.back()] >= required[j]) {
                    window.pop_back();
                }
                window.push_back(j);
            }
            while (window.size() > head && window[head] < i) ++head;
            gains[i] = window.size() > head ? required[window[head]] : 1.0;
            // Compact occasionally so the vector does not grow unbounded
            // across a long track.
            if (head > 4096) {
                window.erase(window.begin(), window.begin() + static_cast<std::ptrdiff_t>(head));
                head = 0;
            }
        }
    }

    // Release: let the gain climb back gradually, never above what the
    // sample needs. 80ms is slow enough not to pump on sustained bass and
    // fast enough to recover between hits.
    const double release = std::exp(-1.0 / (0.08 * sampleRate));
    double current = 1.0;
    double deepest = 1.0;
    for (std::size_t i = 0; i < n; ++i) {
        if (gains[i] < current) {
            current = gains[i];
        } else {
            current = gains[i] + (current - gains[i]) * release;
            // The clamp that keeps the guarantee: recovery may never take
            // the gain above what this sample actually allows.
            current = (std::min)(current, gains[i]);
        }
        gains[i] = current;
        deepest = (std::min)(deepest, current);
    }

    maxReductionDb = deepest < 1.0 ? dsp::linearToDb(deepest) : 0.0;
    return gains;
}

// Applies everything except the pre-gain, which the caller sets so it can
// run the chain twice and land on the loudness target.
AudioBufferPtr runChain(const AudioBufferPtr& source, const MasteringPlan& plan,
                        double preGainDb, double& gainReductionDb) {
    const double rate = source->sampleRate;
    const std::size_t frames = source->frames();

    auto out = std::make_shared<AudioBuffer>();
    out->sampleRate = source->sampleRate;
    out->channels.assign(2, std::vector<float>(frames, 0.0f));

    const auto& inL = source->channels[0];
    const auto& inR = source->channels.size() > 1 ? source->channels[1] : source->channels[0];

    // One filter instance per channel: sharing state between channels would
    // cross-modulate them, which on a wide mix is audible as the image
    // moving with the bass.
    std::vector<dsp::Biquad> eqL, eqR;
    for (const auto& move : plan.eq) {
        // Q of 1.0 across ten log-spaced bands gives overlapping, gentle
        // curves - the correction is meant to be a tilt, not a set of
        // notches.
        eqL.push_back(dsp::peaking(move.hz, 1.0, move.gainDb, rate));
        eqR.push_back(dsp::peaking(move.hz, 1.0, move.gainDb, rate));
    }
    dsp::Biquad hpL{}, hpR{};
    if (plan.highPassHz > 0.0) {
        hpL = dsp::highPass(plan.highPassHz, 0.707, rate);
        hpR = dsp::highPass(plan.highPassHz, 0.707, rate);
    }

    const double preGain = dsp::dbToLinear(preGainDb);
    const double threshold = dsp::dbToLinear(plan.compressorThresholdDb);
    const double ratio = plan.compressorRatio;
    const double attack = std::exp(-1.0 / (0.010 * rate));   // 10ms
    const double release = std::exp(-1.0 / (0.120 * rate));  // 120ms
    double envelope = 0.0;

    // Two passes over the material: the first works out what the chain does
    // and what its inter-sample peaks are, the second limits against them.
    // A single streaming pass cannot do this - the interpolator needs
    // samples either side of the one being judged, including ones the chain
    // has not produced yet.
    std::vector<float> preL(frames), preR(frames);
    {
        auto eqL2 = eqL, eqR2 = eqR;
        auto hpL2 = hpL, hpR2 = hpR;
        double env2 = 0.0;
        for (std::size_t i = 0; i < frames; ++i) {
            double l = inL[i], r = inR[i];
            if (plan.highPassHz > 0.0) {
                l = hpL2.process(static_cast<float>(l));
                r = hpR2.process(static_cast<float>(r));
            }
            for (std::size_t e = 0; e < eqL2.size(); ++e) {
                l = eqL2[e].process(static_cast<float>(l));
                r = eqR2[e].process(static_cast<float>(r));
            }
            if (plan.widthScale != 1.0) {
                const double mid = (l + r) * 0.5;
                const double side = (l - r) * 0.5 * plan.widthScale;
                l = mid + side;
                r = mid - side;
            }
            l *= preGain;
            r *= preGain;
            if (ratio > 1.0 && threshold > 0.0) {
                const double detect = (std::max)(std::fabs(l), std::fabs(r));
                env2 = detect > env2 ? detect + (env2 - detect) * attack
                                     : detect + (env2 - detect) * release;
                if (env2 > threshold) {
                    const double over = dsp::linearToDb(env2 / threshold);
                    const double gain = dsp::dbToLinear(-(over - over / ratio));
                    l *= gain;
                    r *= gain;
                }
            }
            preL[i] = static_cast<float>(l);
            preR[i] = static_cast<float>(r);
        }
    }
    const std::vector<double> truePeaks = interSamplePeaks(preL, preR);

    (void)envelope;
    const auto gains = limiterGains(truePeaks, dsp::dbToLinear(plan.limiterCeilingDb), rate,
                                    gainReductionDb);
    for (std::size_t i = 0; i < frames; ++i) {
        out->channels[0][i] = static_cast<float>(preL[i] * gains[i]);
        out->channels[1][i] = static_cast<float>(preR[i] * gains[i]);
    }
    return out;
}

}  // namespace

MasteringResult master(const AudioBufferPtr& source, const MasteringTarget& target) {
    MasteringResult result;
    if (!source || source->frames() == 0) return result;

    result.before = analyse(source);
    result.plan = designChain(result.before, target);

    double reduction = 0.0;
    auto rendered = runChain(source, result.plan, result.plan.preGainDb, reduction);

    // The limiter took some loudness back out, and how much depends on the
    // material. Measuring the first pass and correcting is what makes the
    // final number land on the target instead of near it.
    // Each correction moves the result most of the way, and the remainder
    // shrinks as the limiter takes over - so this converges quickly or not
    // at all. Four passes is where the improvement stops being measurable.
    double achieved = dsp::measureLoudness(rendered->channels, rendered->sampleRate).integratedLufs;
    for (int pass = 0; pass < 4; ++pass) {
        const double miss = target.targetLufs - achieved;
        if (std::fabs(miss) <= 0.1) break;
        const double before = achieved;
        result.plan.preGainDb += miss;
        auto candidate = runChain(source, result.plan, result.plan.preGainDb, reduction);
        achieved = dsp::measureLoudness(candidate->channels, candidate->sampleRate).integratedLufs;
        // Past a certain point more pre-gain buys nothing: the limiter eats
        // all of it and the only thing that changes is how much it is
        // working. Stop there and keep the better of the two.
        if (achieved <= before + 0.05 && miss > 0) {
            result.plan.preGainDb -= miss;
            achieved = before;
            break;
        }
        rendered = candidate;
    }
    result.plan.loudnessMissLu = achieved - target.targetLufs;

    result.plan.estimatedGainReductionDb = reduction;
    result.audio = rendered;
    result.after = analyse(rendered);
    return result;
}

}  // namespace cashout
