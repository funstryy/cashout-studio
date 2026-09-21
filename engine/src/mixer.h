// The mixer graph, and everything that runs on the audio thread.
//
// Shape: clips read into per-track planar scratch, each track's insert chain
// processes in place, the track is panned and summed into the master, the
// master's own chain runs, and the result is interleaved for the device. One
// pass, no allocation, no locks.
//
// Every change from outside arrives as a command in a ring. The audio thread
// drains the ring at the top of each block, which means a change always lands
// on a block boundary - a gain move can never be half-applied across a
// buffer, and two commands can never interleave.
#pragma once

#include <atomic>
#include <cstdint>
#include <vector>

#include "audio_buffer.h"
#include "lockfree.h"

namespace cashout {

// Anything that sits in a track's signal path. Implemented by the VST3
// bridge, and by the built-in processors, so the mixer does not care which
// it is holding.
class InsertProcessor {
public:
    virtual ~InsertProcessor() = default;
    // Planar, in place. Must be real-time safe: no allocation, no locks, no
    // file access. A plugin that breaks that rule is a plugin that clicks.
    virtual void process(float* const* channels, int channelCount, std::uint32_t frames) = 0;
    virtual void reset(double sampleRate, std::uint32_t maxFrames) = 0;
    virtual const char* name() const = 0;
};

constexpr int kMaxTracks = 128;
constexpr int kMaxClips = 8192;
constexpr int kMaxInsertsPerTrack = 8;

enum class CommandType : std::uint8_t {
    None,
    SetTrack,        // gain, pan, mute, solo, armed
    AddClip,
    RemoveClip,
    ClearClips,
    SetInsert,       // slot on a track gets a processor (or null to clear)
    Transport,       // play/stop
    Seek,
    SetLoop,
    SetMaster,
    SetTempo,
};

// Deliberately trivially copyable and pointer-only: this crosses to the
// audio thread, so it may not own anything that would need destroying there.
struct Command {
    CommandType type{CommandType::None};
    int track{0};
    int slot{0};
    std::int64_t clipId{0};
    const AudioBuffer* buffer{nullptr};
    InsertProcessor* insert{nullptr};
    std::int64_t startFrame{0};
    std::int64_t trimStartFrame{0};
    std::int64_t lengthFrames{0};
    float gain{1.0f};
    float pan{0.0f};
    bool flagA{false};   // mute / playing / loop enabled
    bool flagB{false};   // solo
    bool flagC{false};   // armed
    double value{0.0};
};

struct TrackState {
    float gain{1.0f};
    float pan{0.0f};
    bool mute{false};
    bool solo{false};
    bool armed{false};
    InsertProcessor* inserts[kMaxInsertsPerTrack]{};
    // Smoothed, because a gain jump inside a block is a click. One-pole
    // toward the target over a few milliseconds is inaudible and costs a
    // multiply-add per block.
    float currentGain{1.0f};
    float currentPan{0.0f};
    // Post-fader peak for the meters, decayed on the audio thread so the UI
    // can poll whenever it likes without missing a transient.
    std::atomic<float> peakL{0.0f};
    std::atomic<float> peakR{0.0f};
};

struct ClipState {
    std::int64_t id{0};
    int track{-1};
    const AudioBuffer* buffer{nullptr};
    std::int64_t startFrame{0};
    std::int64_t trimStartFrame{0};
    std::int64_t lengthFrames{0};
    float gain{1.0f};
    bool active{false};
};

struct MixerSnapshot {
    bool playing{false};
    double positionSeconds{0.0};
    double tempo{120.0};
    bool loopEnabled{false};
    double loopStartSeconds{0.0};
    double loopEndSeconds{0.0};
    int activeClips{0};
    float masterPeakL{0.0f};
    float masterPeakR{0.0f};
};

class Mixer {
public:
    Mixer();

    // Control thread. Returns false when the ring is full, which only
    // happens if the audio thread has stopped draining it - i.e. the device
    // is closed. The caller reports that rather than spinning.
    bool post(const Command& command);

    // Audio thread. Interleaved output, `channels` wide.
    void render(float* out, std::uint32_t frames, int channels);

    void prepare(double sampleRate, std::uint32_t maxFrames);

    MixerSnapshot snapshot() const;
    void trackPeaks(int track, float& left, float& right) const;

    // Always-on capture of the master bus. See retrospective().
    AudioRing& capture() { return capture_; }
    const AudioRing& capture() const { return capture_; }

private:
    void drainCommands();
    void renderClips(std::uint32_t frames, std::int64_t position);

    SpscQueue<Command, 4096> commands_;

    TrackState tracks_[kMaxTracks];
    ClipState clips_[kMaxClips];
    InsertProcessor* masterInserts_[kMaxInsertsPerTrack]{};

    // Planar scratch, one pair per track plus the master. Allocated in
    // prepare() and never resized while the device is open.
    std::vector<std::vector<float>> trackScratch_;
    std::vector<float*> scratchPointers_;
    std::vector<float> masterL_;
    std::vector<float> masterR_;

    double sampleRate_{48000.0};
    std::uint32_t maxFrames_{1024};
    float gainSmoothing_{0.0f};

    std::atomic<bool> playing_{false};
    std::atomic<std::int64_t> position_{0};
    std::atomic<double> tempo_{120.0};
    std::atomic<bool> loopEnabled_{false};
    std::atomic<std::int64_t> loopStart_{0};
    std::atomic<std::int64_t> loopEnd_{0};
    std::atomic<int> activeClips_{0};
    std::atomic<float> masterGain_{1.0f};
    std::atomic<float> masterPeakL_{0.0f};
    std::atomic<float> masterPeakR_{0.0f};

    AudioRing capture_;
};

}  // namespace cashout
