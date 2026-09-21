#include "mixer.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace cashout {
namespace {

// Constant-power panning. Linear panning dips by 3dB in the middle, which
// is audible as a hole when a part sweeps across the image; sin/cos keeps
// the perceived level flat all the way across.
inline void panGains(float pan, float& left, float& right) {
    const float clamped = (std::max)(-1.0f, (std::min)(1.0f, pan));
    const float angle = (clamped + 1.0f) * 0.25f * 3.14159265358979f;
    left = std::cos(angle);
    right = std::sin(angle);
}

inline float decayPeak(std::atomic<float>& slot, float blockPeak) {
    // Meters fall at roughly 20dB/second, which is slow enough to read and
    // fast enough not to lie about what is happening now.
    float previous = slot.load(std::memory_order_relaxed) * 0.85f;
    if (blockPeak > previous) previous = blockPeak;
    slot.store(previous, std::memory_order_relaxed);
    return previous;
}

}  // namespace

Mixer::Mixer() {
    for (int i = 0; i < kMaxClips; ++i) clips_[i].active = false;
}

void Mixer::prepare(double sampleRate, std::uint32_t maxFrames) {
    sampleRate_ = sampleRate > 0 ? sampleRate : 48000.0;
    maxFrames_ = (std::max)(maxFrames, 64u);

    // A 5ms one-pole on gain and pan. Long enough to kill the click of a
    // fader jump, short enough that it still feels instant under the hand.
    const double smoothingSeconds = 0.005;
    gainSmoothing_ = static_cast<float>(
        std::exp(-1.0 / (smoothingSeconds * sampleRate_ / maxFrames_)));

    trackScratch_.assign(static_cast<std::size_t>(kMaxTracks) * 2,
                         std::vector<float>(maxFrames_, 0.0f));
    scratchPointers_.assign(static_cast<std::size_t>(kMaxTracks) * 2, nullptr);
    for (std::size_t i = 0; i < trackScratch_.size(); ++i) {
        scratchPointers_[i] = trackScratch_[i].data();
    }
    masterL_.assign(maxFrames_, 0.0f);
    masterR_.assign(maxFrames_, 0.0f);

    for (auto& track : tracks_) {
        for (auto* insert : track.inserts) {
            if (insert != nullptr) insert->reset(sampleRate_, maxFrames_);
        }
    }
    for (auto* insert : masterInserts_) {
        if (insert != nullptr) insert->reset(sampleRate_, maxFrames_);
    }

    // Thirty seconds of the master bus, always. See the retrospective
    // capture note in main.cpp - this is what makes "I wasn't recording"
    // recoverable instead of gone.
    capture_.reset(static_cast<std::size_t>(sampleRate_ * 30.0), 2);
}

bool Mixer::post(const Command& command) { return commands_.push(command); }

void Mixer::drainCommands() {
    Command command;
    while (commands_.pop(command)) {
        switch (command.type) {
            case CommandType::SetTrack: {
                if (command.track < 0 || command.track >= kMaxTracks) break;
                auto& track = tracks_[command.track];
                track.gain = command.gain;
                track.pan = command.pan;
                track.mute = command.flagA;
                track.solo = command.flagB;
                track.armed = command.flagC;
                break;
            }
            case CommandType::AddClip: {
                for (auto& clip : clips_) {
                    if (clip.active) continue;
                    clip.id = command.clipId;
                    clip.track = command.track;
                    clip.buffer = command.buffer;
                    clip.startFrame = command.startFrame;
                    clip.trimStartFrame = command.trimStartFrame;
                    clip.lengthFrames = command.lengthFrames;
                    clip.gain = command.gain;
                    clip.active = true;
                    activeClips_.fetch_add(1, std::memory_order_relaxed);
                    break;
                }
                break;
            }
            case CommandType::RemoveClip: {
                for (auto& clip : clips_) {
                    if (!clip.active || clip.id != command.clipId) continue;
                    clip.active = false;
                    clip.buffer = nullptr;
                    activeClips_.fetch_sub(1, std::memory_order_relaxed);
                }
                break;
            }
            case CommandType::ClearClips: {
                for (auto& clip : clips_) {
                    clip.active = false;
                    clip.buffer = nullptr;
                }
                activeClips_.store(0, std::memory_order_relaxed);
                break;
            }
            case CommandType::SetInsert: {
                if (command.slot < 0 || command.slot >= kMaxInsertsPerTrack) break;
                if (command.track < 0) {
                    masterInserts_[command.slot] = command.insert;
                } else if (command.track < kMaxTracks) {
                    tracks_[command.track].inserts[command.slot] = command.insert;
                }
                break;
            }
            case CommandType::Transport: {
                playing_.store(command.flagA, std::memory_order_release);
                break;
            }
            case CommandType::Seek: {
                position_.store(command.startFrame, std::memory_order_release);
                break;
            }
            case CommandType::SetLoop: {
                loopEnabled_.store(command.flagA, std::memory_order_release);
                loopStart_.store(command.startFrame, std::memory_order_release);
                loopEnd_.store(command.lengthFrames, std::memory_order_release);
                break;
            }
            case CommandType::SetMaster: {
                masterGain_.store(command.gain, std::memory_order_release);
                break;
            }
            case CommandType::SetTempo: {
                tempo_.store(command.value, std::memory_order_release);
                break;
            }
            case CommandType::None:
            default:
                break;
        }
    }
}

void Mixer::renderClips(std::uint32_t frames, std::int64_t position) {
    const std::int64_t blockEnd = position + frames;

    for (const auto& clip : clips_) {
        if (!clip.active || clip.buffer == nullptr) continue;
        if (clip.track < 0 || clip.track >= kMaxTracks) continue;

        const std::int64_t clipEnd = clip.startFrame + clip.lengthFrames;
        if (clipEnd <= position || clip.startFrame >= blockEnd) continue;

        // The overlap between this block and this clip, in timeline frames.
        const std::int64_t from = (std::max)(position, clip.startFrame);
        const std::int64_t to = (std::min)(blockEnd, clipEnd);
        if (to <= from) continue;

        const std::size_t outOffset = static_cast<std::size_t>(from - position);
        const std::size_t count = static_cast<std::size_t>(to - from);
        const std::int64_t sourceStart = clip.trimStartFrame + (from - clip.startFrame);

        float* left = scratchPointers_[static_cast<std::size_t>(clip.track) * 2];
        float* right = scratchPointers_[static_cast<std::size_t>(clip.track) * 2 + 1];

        const auto& buffer = *clip.buffer;
        const int sourceChannels = buffer.channelCount();
        if (sourceChannels <= 0) continue;
        const auto& planeL = buffer.channels[0];
        const auto& planeR = buffer.channels[sourceChannels > 1 ? 1 : 0];
        const std::size_t sourceFrames = planeL.size();

        for (std::size_t i = 0; i < count; ++i) {
            const std::int64_t index = sourceStart + static_cast<std::int64_t>(i);
            if (index < 0 || static_cast<std::size_t>(index) >= sourceFrames) continue;
            const std::size_t s = static_cast<std::size_t>(index);
            left[outOffset + i] += planeL[s] * clip.gain;
            right[outOffset + i] += planeR[s] * clip.gain;
        }
    }
}

void Mixer::render(float* out, std::uint32_t frames, int channels) {
    drainCommands();

    if (frames > maxFrames_) frames = maxFrames_;
    const bool playing = playing_.load(std::memory_order_acquire);
    std::int64_t position = position_.load(std::memory_order_acquire);

    // Clear only the tracks that could be written this block. Clearing all
    // 128 every time costs more than the clips do at small buffer sizes.
    bool trackTouched[kMaxTracks] = {};
    for (const auto& clip : clips_) {
        if (clip.active && clip.track >= 0 && clip.track < kMaxTracks) {
            trackTouched[clip.track] = true;
        }
    }
    for (int t = 0; t < kMaxTracks; ++t) {
        if (!trackTouched[t] && tracks_[t].inserts[0] == nullptr) continue;
        std::memset(scratchPointers_[static_cast<std::size_t>(t) * 2], 0, sizeof(float) * frames);
        std::memset(scratchPointers_[static_cast<std::size_t>(t) * 2 + 1], 0, sizeof(float) * frames);
        trackTouched[t] = true;
    }

    if (playing) renderClips(frames, position);

    std::memset(masterL_.data(), 0, sizeof(float) * frames);
    std::memset(masterR_.data(), 0, sizeof(float) * frames);

    // Solo is a property of the whole desk, not of one track, so it has to
    // be resolved before any track is summed.
    bool anySolo = false;
    for (const auto& track : tracks_) {
        if (track.solo) {
            anySolo = true;
            break;
        }
    }

    for (int t = 0; t < kMaxTracks; ++t) {
        if (!trackTouched[t]) continue;
        auto& track = tracks_[t];

        float* channelsPtr[2] = {scratchPointers_[static_cast<std::size_t>(t) * 2],
                                 scratchPointers_[static_cast<std::size_t>(t) * 2 + 1]};

        // Inserts run whether or not the track is audible. A compressor that
        // only sees signal when it is unmuted comes back with its envelope
        // in the wrong place, and a reverb tail should not vanish the
        // instant somebody hits mute.
        for (auto* insert : track.inserts) {
            if (insert != nullptr) insert->process(channelsPtr, 2, frames);
        }

        const bool audible = !track.mute && (!anySolo || track.solo);
        if (!audible) continue;

        track.currentGain = track.gain + (track.currentGain - track.gain) * gainSmoothing_;
        track.currentPan = track.pan + (track.currentPan - track.pan) * gainSmoothing_;

        float gl = 0.0f, gr = 0.0f;
        panGains(track.currentPan, gl, gr);
        gl *= track.currentGain;
        gr *= track.currentGain;

        float peakL = 0.0f, peakR = 0.0f;
        for (std::uint32_t i = 0; i < frames; ++i) {
            const float l = channelsPtr[0][i] * gl;
            const float r = channelsPtr[1][i] * gr;
            masterL_[i] += l;
            masterR_[i] += r;
            peakL = (std::max)(peakL, std::fabs(l));
            peakR = (std::max)(peakR, std::fabs(r));
        }
        decayPeak(track.peakL, peakL);
        decayPeak(track.peakR, peakR);
    }

    float* masterChannels[2] = {masterL_.data(), masterR_.data()};
    for (auto* insert : masterInserts_) {
        if (insert != nullptr) insert->process(masterChannels, 2, frames);
    }

    const float masterGain = masterGain_.load(std::memory_order_relaxed);
    float peakL = 0.0f, peakR = 0.0f;
    for (std::uint32_t i = 0; i < frames; ++i) {
        masterL_[i] *= masterGain;
        masterR_[i] *= masterGain;
        peakL = (std::max)(peakL, std::fabs(masterL_[i]));
        peakR = (std::max)(peakR, std::fabs(masterR_[i]));
    }
    decayPeak(masterPeakL_, peakL);
    decayPeak(masterPeakR_, peakR);

    // Interleave for the device. Anything past stereo gets silence rather
    // than a copy of the front pair: quietly duplicating the mix into a
    // surround array is worse than an obviously empty channel.
    for (std::uint32_t i = 0; i < frames; ++i) {
        float* frame = out + static_cast<std::size_t>(i) * channels;
        frame[0] = masterL_[i];
        if (channels > 1) frame[1] = masterR_[i];
        for (int c = 2; c < channels; ++c) frame[c] = 0.0f;
    }

    // The capture ring wants the stereo bus, not the device's channel count.
    if (channels == 2) {
        capture_.write(out, frames);
    }

    if (playing) {
        position += frames;
        const bool looping = loopEnabled_.load(std::memory_order_acquire);
        const std::int64_t loopEnd = loopEnd_.load(std::memory_order_acquire);
        const std::int64_t loopStart = loopStart_.load(std::memory_order_acquire);
        if (looping && loopEnd > loopStart && position >= loopEnd) {
            // Jumping straight back is what a DAW does; the seam is at a
            // block boundary, which is where the user put the loop point.
            position = loopStart + ((position - loopStart) % (loopEnd - loopStart));
        }
        position_.store(position, std::memory_order_release);
    }
}

MixerSnapshot Mixer::snapshot() const {
    MixerSnapshot s;
    s.playing = playing_.load(std::memory_order_acquire);
    s.positionSeconds = static_cast<double>(position_.load(std::memory_order_acquire)) / sampleRate_;
    s.tempo = tempo_.load(std::memory_order_acquire);
    s.loopEnabled = loopEnabled_.load(std::memory_order_acquire);
    s.loopStartSeconds = static_cast<double>(loopStart_.load(std::memory_order_acquire)) / sampleRate_;
    s.loopEndSeconds = static_cast<double>(loopEnd_.load(std::memory_order_acquire)) / sampleRate_;
    s.activeClips = activeClips_.load(std::memory_order_relaxed);
    s.masterPeakL = masterPeakL_.load(std::memory_order_relaxed);
    s.masterPeakR = masterPeakR_.load(std::memory_order_relaxed);
    return s;
}

void Mixer::trackPeaks(int track, float& left, float& right) const {
    if (track < 0 || track >= kMaxTracks) {
        left = right = 0.0f;
        return;
    }
    left = tracks_[track].peakL.load(std::memory_order_relaxed);
    right = tracks_[track].peakR.load(std::memory_order_relaxed);
}

}  // namespace cashout
