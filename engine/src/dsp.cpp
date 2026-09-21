#include "dsp.h"

#include <algorithm>
#include <numeric>

namespace cashout {
namespace dsp {
namespace {

// RBJ cookbook forms. Normalised by a0 at construction so the process loop
// does not carry a division.
Biquad normalise(double b0, double b1, double b2, double a0, double a1, double a2) {
    Biquad q;
    q.b0 = b0 / a0;
    q.b1 = b1 / a0;
    q.b2 = b2 / a0;
    q.a1 = a1 / a0;
    q.a2 = a2 / a0;
    return q;
}

}  // namespace

double linearToDb(double linear) {
    return linear > 1e-12 ? 20.0 * std::log10(linear) : -240.0;
}

double dbToLinear(double db) { return std::pow(10.0, db / 20.0); }

double Biquad::magnitudeAt(double hz, double sampleRate) const {
    const double w = 2.0 * kPi * hz / sampleRate;
    const std::complex<double> z = std::polar(1.0, -w);
    const std::complex<double> num = b0 + b1 * z + b2 * z * z;
    const std::complex<double> den = 1.0 + a1 * z + a2 * z * z;
    return std::abs(num / den);
}

Biquad peaking(double hz, double q, double gainDb, double sampleRate) {
    const double A = std::pow(10.0, gainDb / 40.0);
    const double w = 2.0 * kPi * hz / sampleRate;
    const double alpha = std::sin(w) / (2.0 * q);
    return normalise(1.0 + alpha * A, -2.0 * std::cos(w), 1.0 - alpha * A,
                     1.0 + alpha / A, -2.0 * std::cos(w), 1.0 - alpha / A);
}

Biquad lowShelf(double hz, double q, double gainDb, double sampleRate) {
    const double A = std::pow(10.0, gainDb / 40.0);
    const double w = 2.0 * kPi * hz / sampleRate;
    const double cosw = std::cos(w);
    const double alpha = std::sin(w) / (2.0 * q);
    const double beta = 2.0 * std::sqrt(A) * alpha;
    return normalise(A * ((A + 1) - (A - 1) * cosw + beta),
                     2 * A * ((A - 1) - (A + 1) * cosw),
                     A * ((A + 1) - (A - 1) * cosw - beta),
                     (A + 1) + (A - 1) * cosw + beta,
                     -2 * ((A - 1) + (A + 1) * cosw),
                     (A + 1) + (A - 1) * cosw - beta);
}

Biquad highShelf(double hz, double q, double gainDb, double sampleRate) {
    const double A = std::pow(10.0, gainDb / 40.0);
    const double w = 2.0 * kPi * hz / sampleRate;
    const double cosw = std::cos(w);
    const double alpha = std::sin(w) / (2.0 * q);
    const double beta = 2.0 * std::sqrt(A) * alpha;
    return normalise(A * ((A + 1) + (A - 1) * cosw + beta),
                     -2 * A * ((A - 1) + (A + 1) * cosw),
                     A * ((A + 1) + (A - 1) * cosw - beta),
                     (A + 1) - (A - 1) * cosw + beta,
                     2 * ((A - 1) - (A + 1) * cosw),
                     (A + 1) - (A - 1) * cosw - beta);
}

Biquad highPass(double hz, double q, double sampleRate) {
    const double w = 2.0 * kPi * hz / sampleRate;
    const double cosw = std::cos(w);
    const double alpha = std::sin(w) / (2.0 * q);
    return normalise((1 + cosw) / 2, -(1 + cosw), (1 + cosw) / 2,
                     1 + alpha, -2 * cosw, 1 - alpha);
}

void fft(std::vector<std::complex<double>>& data) {
    const std::size_t n = data.size();
    if (n < 2) return;

    // Bit reversal, then the usual Cooley-Tukey butterflies.
    for (std::size_t i = 1, j = 0; i < n; ++i) {
        std::size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(data[i], data[j]);
    }
    for (std::size_t len = 2; len <= n; len <<= 1) {
        const double angle = -2.0 * kPi / static_cast<double>(len);
        const std::complex<double> step(std::cos(angle), std::sin(angle));
        for (std::size_t i = 0; i < n; i += len) {
            std::complex<double> w(1.0, 0.0);
            for (std::size_t k = 0; k < len / 2; ++k) {
                const auto u = data[i + k];
                const auto v = data[i + k + len / 2] * w;
                data[i + k] = u + v;
                data[i + k + len / 2] = u - v;
                w *= step;
            }
        }
    }
}

// ---------------------------------------------------------------- loudness

void KWeighting::prepare() {
    // BS.1770-4's published 48kHz coefficients, used verbatim. Re-deriving
    // them from the standard's analogue prototype gives the same numbers to
    // within rounding, and using the published ones means a disagreement
    // with another meter is a bug here rather than a difference of method.
    shelf = Biquad{1.53512485958697, -2.69169618940638, 1.19839281085285,
                   -1.69065929318241, 0.73248077421585, 0.0, 0.0};
    highpass = Biquad{1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621, 0.0, 0.0};
    reset();
}

void KWeighting::reset() {
    shelf.reset();
    highpass.reset();
}

namespace {

// BS.1770 channel weights. Stereo is both channels at unity; the surround
// weights only matter for material this studio never produces.
constexpr double kChannelWeight = 1.0;

double meanSquareToLufs(double meanSquare) {
    return meanSquare > 1e-12 ? -0.691 + 10.0 * std::log10(meanSquare) : -70.0;
}

}  // namespace

LoudnessResult measureLoudness(const std::vector<std::vector<float>>& channels,
                               std::uint32_t sampleRate) {
    LoudnessResult result;
    if (channels.empty() || channels[0].empty()) return result;

    const std::size_t frames = channels[0].size();
    const int channelCount = static_cast<int>(channels.size());

    // K-weight every channel once, into scratch.
    std::vector<std::vector<float>> weighted(channels.size());
    for (std::size_t c = 0; c < channels.size(); ++c) {
        KWeighting k;
        k.prepare();
        weighted[c].resize(frames);
        for (std::size_t i = 0; i < frames; ++i) weighted[c][i] = k.process(channels[c][i]);
    }

    // 400ms blocks overlapping by 75%, which is what the standard specifies
    // and what makes the gating stable on material with gaps in it.
    const std::size_t blockFrames = static_cast<std::size_t>(0.4 * sampleRate);
    const std::size_t hop = blockFrames / 4;
    if (frames < blockFrames) return result;

    std::vector<double> blockMeanSquares;
    blockMeanSquares.reserve((frames - blockFrames) / hop + 1);
    for (std::size_t start = 0; start + blockFrames <= frames; start += hop) {
        double sum = 0.0;
        for (int c = 0; c < channelCount; ++c) {
            const auto& plane = weighted[static_cast<std::size_t>(c)];
            double channelSum = 0.0;
            for (std::size_t i = 0; i < blockFrames; ++i) {
                const double s = plane[start + i];
                channelSum += s * s;
            }
            sum += kChannelWeight * channelSum / static_cast<double>(blockFrames);
        }
        blockMeanSquares.push_back(sum);
    }
    if (blockMeanSquares.empty()) return result;

    // Absolute gate first: anything below -70 LUFS is silence and must not
    // count toward the mean at all.
    std::vector<double> gated;
    gated.reserve(blockMeanSquares.size());
    for (const double ms : blockMeanSquares) {
        if (meanSquareToLufs(ms) > -70.0) gated.push_back(ms);
    }
    if (gated.empty()) return result;

    const double ungatedMean =
        std::accumulate(gated.begin(), gated.end(), 0.0) / static_cast<double>(gated.size());
    // Then the relative gate, 10 LU below that mean - the step that stops a
    // quiet intro or a long fade pulling the whole number down.
    const double relativeThreshold = meanSquareToLufs(ungatedMean) - 10.0;

    std::vector<double> finalBlocks;
    for (const double ms : gated) {
        if (meanSquareToLufs(ms) > relativeThreshold) finalBlocks.push_back(ms);
    }
    if (finalBlocks.empty()) finalBlocks = gated;

    const double mean = std::accumulate(finalBlocks.begin(), finalBlocks.end(), 0.0) /
                        static_cast<double>(finalBlocks.size());
    result.integratedLufs = meanSquareToLufs(mean);

    // Short-term is a 3-second window; the maximum of it is what tells you
    // whether a chorus is going to hit the limiter much harder than the
    // integrated figure suggests.
    const std::size_t shortFrames = static_cast<std::size_t>(3.0 * sampleRate);
    if (frames >= shortFrames) {
        double loudest = -70.0;
        for (std::size_t start = 0; start + shortFrames <= frames; start += sampleRate / 2) {
            double sum = 0.0;
            for (int c = 0; c < channelCount; ++c) {
                const auto& plane = weighted[static_cast<std::size_t>(c)];
                double channelSum = 0.0;
                for (std::size_t i = 0; i < shortFrames; ++i) {
                    const double s = plane[start + i];
                    channelSum += s * s;
                }
                sum += channelSum / static_cast<double>(shortFrames);
            }
            loudest = (std::max)(loudest, meanSquareToLufs(sum));
        }
        result.shortTermMaxLufs = loudest;
    } else {
        result.shortTermMaxLufs = result.integratedLufs;
    }

    // LRA: the spread between the 10th and 95th percentile of the gated
    // blocks. A number near 3 is a squashed master; near 12 is a dynamic one.
    std::vector<double> lufsBlocks;
    lufsBlocks.reserve(finalBlocks.size());
    for (const double ms : finalBlocks) lufsBlocks.push_back(meanSquareToLufs(ms));
    std::sort(lufsBlocks.begin(), lufsBlocks.end());
    if (lufsBlocks.size() > 4) {
        const auto pick = [&](double fraction) {
            const std::size_t index = static_cast<std::size_t>(
                fraction * static_cast<double>(lufsBlocks.size() - 1));
            return lufsBlocks[index];
        };
        result.loudnessRange = pick(0.95) - pick(0.10);
    }

    double peak = 0.0;
    for (const auto& plane : channels) {
        for (const float s : plane) peak = (std::max)(peak, static_cast<double>(std::fabs(s)));
    }
    result.samplePeakDb = linearToDb(peak);
    result.truePeakDb = truePeakDb(channels);
    result.crestDb = result.samplePeakDb - result.integratedLufs;
    return result;
}

double truePeakDb(const std::vector<std::vector<float>>& channels) {
    if (channels.empty() || channels[0].empty()) return -120.0;

    // A 4x windowed-sinc interpolator. BS.1770 asks for at least 4x for
    // material at 48kHz, and the difference this finds against the sample
    // peak is routinely 0.3-1.5dB on a limited master - enough to fail a
    // -1 dBTP delivery spec while the sample peak reads exactly 0.0.
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
                const double sinc = std::fabs(x) < 1e-9 ? 1.0 : std::sin(kPi * x) / (kPi * x);
                // Blackman window: the stopband matters more than the
                // transition width when the only question is a peak.
                const double w = 0.42 - 0.5 * std::cos(2.0 * kPi * t / (kTaps - 1)) +
                                 0.08 * std::cos(4.0 * kPi * t / (kTaps - 1));
                phases[phase][t] = sinc * w;
                sum += sinc * w;
            }
            for (double& tap : phases[phase]) tap /= sum;
        }
    }

    double peak = 0.0;
    for (const auto& plane : channels) {
        const std::size_t n = plane.size();
        for (std::size_t i = 0; i < n; ++i) {
            for (int phase = 0; phase < kOversample; ++phase) {
                double acc = 0.0;
                for (int t = 0; t < kTaps; ++t) {
                    const std::ptrdiff_t index =
                        static_cast<std::ptrdiff_t>(i) + t - kTaps / 2;
                    if (index < 0 || static_cast<std::size_t>(index) >= n) continue;
                    acc += plane[static_cast<std::size_t>(index)] * phases[phase][t];
                }
                peak = (std::max)(peak, std::fabs(acc));
            }
        }
    }
    return linearToDb(peak);
}

// ---------------------------------------------------------------- spectrum

SpectralProfile measureSpectrum(const std::vector<std::vector<float>>& channels,
                                std::uint32_t sampleRate) {
    SpectralProfile profile;
    // Log-spaced from 40Hz to 16kHz. Below 40 is mostly rumble and above 16k
    // is mostly nothing on material that has been through a codec, and
    // neither is a band anybody makes a mastering decision on.
    const double lowest = 40.0, highest = 16000.0;
    for (int b = 0; b < SpectralProfile::kBands; ++b) {
        const double t = static_cast<double>(b) / (SpectralProfile::kBands - 1);
        profile.centres[b] = lowest * std::pow(highest / lowest, t);
    }
    if (channels.empty() || channels[0].empty()) return profile;

    constexpr std::size_t kFft = 8192;
    const std::size_t frames = channels[0].size();
    if (frames < kFft) return profile;

    std::vector<double> window(kFft);
    for (std::size_t i = 0; i < kFft; ++i) {
        window[i] = 0.5 - 0.5 * std::cos(2.0 * kPi * i / (kFft - 1));
    }

    std::vector<double> energy(SpectralProfile::kBands, 0.0);
    std::vector<std::complex<double>> buffer(kFft);
    std::size_t windows = 0;

    // Hop by half the window, and stop after a bounded number of them: a
    // six-minute track does not need ten thousand FFTs to establish its
    // tonal balance, and capping this keeps the analysis under a second.
    const std::size_t hop = kFft / 2;
    const std::size_t maxWindows = 240;
    const std::size_t available = (frames - kFft) / hop + 1;
    const std::size_t stride = available > maxWindows ? available / maxWindows : 1;

    for (std::size_t w = 0; w * stride * hop + kFft <= frames && windows < maxWindows; ++w) {
        const std::size_t start = w * stride * hop;
        // Each channel on its own, with the magnitudes averaged afterwards.
        //
        // Summing to mono first and transforming that is the obvious thing
        // and it is wrong: anything out of phase between the channels
        // cancels before it is ever measured. A track with its bass wide
        // enough to partly cancel reported 37dB *less* low end than it
        // actually had, and both the co-producer and the mastering EQ then
        // acted on that - boosting a bottom end that was already too big.
        // Magnitudes cannot cancel, so this describes what is in the mix.
        // Whether it survives a mono fold-down is a separate question, and
        // the correlation figure below is the one that answers it.
        for (std::size_t c = 0; c < channels.size(); ++c) {
            for (std::size_t i = 0; i < kFft; ++i) {
                buffer[i] = std::complex<double>(channels[c][start + i] * window[i], 0.0);
            }
            fft(buffer);
            for (std::size_t bin = 1; bin < kFft / 2; ++bin) {
                const double hz = static_cast<double>(bin) * sampleRate / kFft;
                if (hz < lowest * 0.7 || hz > highest * 1.4) continue;
                int best = 0;
                double bestDistance = 1e30;
                for (int b = 0; b < SpectralProfile::kBands; ++b) {
                    const double d = std::fabs(std::log(hz / profile.centres[b]));
                    if (d < bestDistance) {
                        bestDistance = d;
                        best = b;
                    }
                }
                const double magnitude = std::abs(buffer[bin]);
                energy[static_cast<std::size_t>(best)] +=
                    magnitude * magnitude / static_cast<double>(channels.size());
            }
        }

        ++windows;
    }

    if (windows == 0) return profile;
    for (int b = 0; b < SpectralProfile::kBands; ++b) {
        profile.bandDb[b] = 10.0 * std::log10(
            (energy[static_cast<std::size_t>(b)] / windows) + 1e-20);
    }

    // Stereo: correlation between the channels, and how much energy is in
    // the side signal relative to the mid.
    if (channels.size() >= 2) {
        double sumLR = 0.0, sumLL = 0.0, sumRR = 0.0, mid = 0.0, side = 0.0;
        const std::size_t n = (std::min)(channels[0].size(), channels[1].size());
        for (std::size_t i = 0; i < n; ++i) {
            const double l = channels[0][i], r = channels[1][i];
            sumLR += l * r;
            sumLL += l * l;
            sumRR += r * r;
            const double m = (l + r) * 0.5, s = (l - r) * 0.5;
            mid += m * m;
            side += s * s;
        }
        const double denom = std::sqrt(sumLL * sumRR);
        profile.correlation = denom > 1e-12 ? sumLR / denom : 1.0;
        profile.widthRatio = mid > 1e-12 ? std::sqrt(side / mid) : 0.0;
    }
    return profile;
}


// ---------------------------------------------------------------- harmony

void Chroma::normalise() {
    double total = 0.0;
    for (const double v : bins) total += v;
    if (total <= 1e-12) return;
    for (double& v : bins) v /= total;
}

const char* noteName(int pitchClass) {
    static const char* kNames[12] = {"C", "C#", "D", "D#", "E", "F",
                                     "F#", "G", "G#", "A", "A#", "B"};
    return (pitchClass >= 0 && pitchClass < 12) ? kNames[pitchClass] : "?";
}

namespace {

// Krumhansl-Schmuckler key profiles: how strongly each scale degree tends
// to be present in music in a given key, derived from listener ratings
// rather than from theory. Correlating a piece's average chroma against all
// 24 rotations of these is still the standard way to guess a key, and it is
// right far more often than any single heuristic about the bass note.
const double kMajorProfile[12] = {6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                                  2.52, 5.19, 2.39, 3.66, 2.29, 2.88};
const double kMinorProfile[12] = {6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                                  2.54, 4.75, 3.98, 2.69, 3.34, 3.17};

double correlate(const double* a, const double* b) {
    double meanA = 0.0, meanB = 0.0;
    for (int i = 0; i < 12; ++i) {
        meanA += a[i];
        meanB += b[i];
    }
    meanA /= 12.0;
    meanB /= 12.0;
    double num = 0.0, da = 0.0, dbv = 0.0;
    for (int i = 0; i < 12; ++i) {
        const double x = a[i] - meanA, y = b[i] - meanB;
        num += x * y;
        da += x * x;
        dbv += y * y;
    }
    const double den = std::sqrt(da * dbv);
    return den > 1e-12 ? num / den : 0.0;
}

// Cosine similarity against a triad template. Weighted rather than binary:
// the root and fifth carry a chord's identity, the third decides its
// quality, and treating all three equally makes majors and minors easy to
// confuse when the third is buried in the mix.
double matchTriad(const Chroma& chroma, int root, bool minorQuality) {
    double templ[12] = {};
    templ[root % 12] = 1.0;
    templ[(root + (minorQuality ? 3 : 4)) % 12] = 0.9;
    templ[(root + 7) % 12] = 0.8;

    double dot = 0.0, na = 0.0, nb = 0.0;
    for (int i = 0; i < 12; ++i) {
        dot += chroma.bins[i] * templ[i];
        na += chroma.bins[i] * chroma.bins[i];
        nb += templ[i] * templ[i];
    }
    const double den = std::sqrt(na * nb);
    return den > 1e-12 ? dot / den : 0.0;
}

}  // namespace

HarmonyResult analyseHarmony(const std::vector<std::vector<float>>& channels,
                             std::uint32_t sampleRate) {
    HarmonyResult result;
    if (channels.empty() || channels[0].empty()) return result;

    constexpr std::size_t kFft = 8192;
    const std::size_t frames = channels[0].size();
    if (frames < kFft) return result;

    std::vector<double> window(kFft);
    for (std::size_t i = 0; i < kFft; ++i) {
        window[i] = 0.5 - 0.5 * std::cos(2.0 * kPi * i / (kFft - 1));
    }

    // A frame every ~85ms. Chords do not change faster than that, and the
    // 8192-point window gives enough resolution to tell a G from a G# down
    // where basses live.
    const std::size_t hop = kFft / 2;
    std::vector<Chroma> frameChroma;
    std::vector<std::complex<double>> buffer(kFft);

    for (std::size_t start = 0; start + kFft <= frames; start += hop) {
        Chroma chroma;
        for (std::size_t c = 0; c < channels.size(); ++c) {
            for (std::size_t i = 0; i < kFft; ++i) {
                buffer[i] = std::complex<double>(channels[c][start + i] * window[i], 0.0);
            }
            fft(buffer);
            for (std::size_t bin = 1; bin < kFft / 2; ++bin) {
                const double hz = static_cast<double>(bin) * sampleRate / kFft;
                // Two octaves below middle C up to the top of where pitch is
                // still heard as pitch. Above ~5kHz everything is overtones,
                // and folding those in only smears the chroma.
                if (hz < 55.0 || hz > 5000.0) continue;
                const double midi = 69.0 + 12.0 * std::log2(hz / 440.0);
                int pitchClass = static_cast<int>(std::lround(midi)) % 12;
                if (pitchClass < 0) pitchClass += 12;
                const double magnitude = std::abs(buffer[bin]);
                chroma.bins[pitchClass] += magnitude * magnitude;
            }
        }
        chroma.normalise();
        frameChroma.push_back(chroma);
    }
    if (frameChroma.empty()) return result;

    for (const auto& chroma : frameChroma) {
        for (int i = 0; i < 12; ++i) result.average.bins[i] += chroma.bins[i];
    }
    result.average.normalise();

    // Key: the best of the 24 rotations.
    double best = -2.0;
    for (int root = 0; root < 12; ++root) {
        double rotatedMajor[12], rotatedMinor[12];
        for (int i = 0; i < 12; ++i) {
            rotatedMajor[i] = kMajorProfile[(i - root + 12) % 12];
            rotatedMinor[i] = kMinorProfile[(i - root + 12) % 12];
        }
        const double majorScore = correlate(result.average.bins, rotatedMajor);
        const double minorScore = correlate(result.average.bins, rotatedMinor);
        if (majorScore > best) {
            best = majorScore;
            result.keyRoot = root;
            result.keyMinor = false;
        }
        if (minorScore > best) {
            best = minorScore;
            result.keyRoot = root;
            result.keyMinor = true;
        }
    }
    result.keyConfidence = best;

    // Chords, on a slower grid. Averaging roughly half a second of chroma
    // before matching stops a passing melody note being read as a chord
    // change every time it lands.
    const double frameSeconds = static_cast<double>(hop) / sampleRate;
    std::size_t group = static_cast<std::size_t>(0.5 / frameSeconds);
    if (group < 1) group = 1;

    for (std::size_t i = 0; i + group <= frameChroma.size(); i += group) {
        Chroma merged{};
        for (std::size_t j = 0; j < group; ++j) {
            for (int b = 0; b < 12; ++b) merged.bins[b] += frameChroma[i + j].bins[b];
        }
        merged.normalise();

        int bestRoot = -1;
        bool bestMinor = false;
        double bestScore = 0.0;
        for (int root = 0; root < 12; ++root) {
            for (int quality = 0; quality < 2; ++quality) {
                const double score = matchTriad(merged, root, quality == 1);
                if (score > bestScore) {
                    bestScore = score;
                    bestRoot = root;
                    bestMinor = quality == 1;
                }
            }
        }

        // Below this the chroma is not describing a triad - percussion, or
        // one sustained note. Reporting nothing beats reporting a chord
        // nobody played.
        if (bestScore < 0.55) bestRoot = -1;

        const double startSeconds = static_cast<double>(i) * frameSeconds;
        const double spanSeconds = static_cast<double>(group) * frameSeconds;

        // Merge with the previous span when it is the same chord, so the
        // output is a progression rather than a list of frames.
        if (!result.chords.empty() && result.chords.back().root == bestRoot &&
            result.chords.back().minorQuality == bestMinor) {
            result.chords.back().seconds += spanSeconds;
            if (bestScore > result.chords.back().confidence) {
                result.chords.back().confidence = bestScore;
            }
        } else {
            ChordSpan span;
            span.root = bestRoot;
            span.minorQuality = bestMinor;
            span.startSeconds = startSeconds;
            span.seconds = spanSeconds;
            span.confidence = bestScore;
            result.chords.push_back(span);
        }
    }

    std::size_t named = 0;
    for (const auto& span : result.chords) {
        if (span.root >= 0) ++named;
    }
    result.harmonicMovement =
        result.chords.empty()
            ? 0.0
            : static_cast<double>(named) / static_cast<double>(result.chords.size());
    return result;
}

}  // namespace dsp
}  // namespace cashout
