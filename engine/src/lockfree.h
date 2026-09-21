// Single-producer/single-consumer plumbing between the control thread and
// the audio thread.
//
// The rule this file exists to enforce: the audio callback never blocks. It
// takes no lock, allocates nothing, frees nothing, touches no file and calls
// nothing that might. A mutex held by the control thread for even a
// microsecond while the device wants its next buffer is a click everybody
// hears, and under WASAPI exclusive mode at 128 frames there are only 2.6ms
// between callbacks to play with.
//
// So: the control thread posts commands into a ring, the audio thread drains
// it, and anything the audio thread is finished with goes back out through a
// second ring to be destroyed on the control thread where destruction is
// allowed to take as long as it likes.
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace cashout {

// A bounded lock-free queue for one writer and one reader.
//
// Capacity is a power of two so the wrap is a mask rather than a modulo -
// not for the division, which is cheap enough, but because the mask keeps
// the index arithmetic branch-free and correct across the 64-bit wrap.
template <typename T, std::size_t CapacityPow2>
class SpscQueue {
    static_assert((CapacityPow2 & (CapacityPow2 - 1)) == 0, "capacity must be a power of two");
    static_assert(std::is_trivially_copyable<T>::value,
                  "queued items cross to the audio thread; they must not own memory");

public:
    bool push(const T& item) noexcept {
        const auto head = head_.load(std::memory_order_relaxed);
        const auto next = head + 1;
        // acquire: the slot must not be written before the reader's release
        // of that slot is visible, or a fast producer overwrites an item the
        // consumer has not taken yet.
        if (next - tail_.load(std::memory_order_acquire) > CapacityPow2) return false;
        slots_[head & kMask] = item;
        head_.store(next, std::memory_order_release);
        return true;
    }

    bool pop(T& out) noexcept {
        const auto tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) return false;
        out = slots_[tail & kMask];
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }

    bool empty() const noexcept {
        return head_.load(std::memory_order_acquire) == tail_.load(std::memory_order_acquire);
    }

private:
    static constexpr std::size_t kMask = CapacityPow2 - 1;

    T slots_[CapacityPow2]{};
    // Separate cache lines. Without the padding the producer's store to head_
    // invalidates the line the consumer is reading tail_ from on every single
    // push, which is a measurable stall in a queue this hot.
    alignas(64) std::atomic<std::uint64_t> head_{0};
    alignas(64) std::atomic<std::uint64_t> tail_{0};
};

// A fixed-size audio ring the audio thread writes and the control thread
// reads, for capture.
//
// Overwriting rather than blocking is deliberate: this backs an always-on
// recorder, and the audio thread must never be held up because nobody has
// drained the last few seconds. The reader finds out how much it missed and
// carries on.
class AudioRing {
public:
    void reset(std::size_t frames, int channels) {
        frames_ = frames;
        channels_ = channels;
        data_.assign(frames * static_cast<std::size_t>(channels), 0.0f);
        written_.store(0, std::memory_order_release);
    }

    // Audio thread. Interleaved input.
    void write(const float* interleaved, std::size_t frames) noexcept {
        if (frames_ == 0) return;
        auto written = written_.load(std::memory_order_relaxed);
        for (std::size_t i = 0; i < frames; ++i) {
            const std::size_t slot = (written + i) % frames_;
            for (int c = 0; c < channels_; ++c) {
                data_[slot * static_cast<std::size_t>(channels_) + static_cast<std::size_t>(c)] =
                    interleaved[i * static_cast<std::size_t>(channels_) + static_cast<std::size_t>(c)];
            }
        }
        written_.store(written + frames, std::memory_order_release);
    }

    std::uint64_t framesWritten() const noexcept { return written_.load(std::memory_order_acquire); }
    std::size_t capacityFrames() const noexcept { return frames_; }
    int channels() const noexcept { return channels_; }

    // Control thread. Copies the most recent `wanted` frames in order.
    void readLatest(float* out, std::size_t wanted) const {
        if (frames_ == 0 || wanted == 0) return;
        const auto written = written_.load(std::memory_order_acquire);
        const std::size_t have = static_cast<std::size_t>(
            written < wanted ? written : static_cast<std::uint64_t>(wanted));
        const std::uint64_t start = written - have;
        for (std::size_t i = 0; i < have; ++i) {
            const std::size_t slot = static_cast<std::size_t>((start + i) % frames_);
            for (int c = 0; c < channels_; ++c) {
                out[i * static_cast<std::size_t>(channels_) + static_cast<std::size_t>(c)] =
                    data_[slot * static_cast<std::size_t>(channels_) + static_cast<std::size_t>(c)];
            }
        }
    }

private:
    std::vector<float> data_;
    std::size_t frames_{0};
    int channels_{2};
    std::atomic<std::uint64_t> written_{0};
};

}  // namespace cashout
