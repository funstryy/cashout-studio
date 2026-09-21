// A WAV reader, and the resampler that goes with it.
//
// Written out rather than pulled in: everything the studio produces lands as
// WAV (the tracks API converts on upload), and a dependency-free reader is
// about two hundred lines against a library that would have to be vendored,
// built and kept current for one format. If the library ever needs to read
// FLAC or MP3 too, that is the point to reach for one - decoding those by
// hand would not be a reasonable trade.
#include "wav.h"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace cashout {
namespace {

struct Reader {
    const std::uint8_t* data;
    std::size_t size;
    std::size_t pos{0};

    bool need(std::size_t n) const { return pos + n <= size; }

    std::uint32_t u32() {
        std::uint32_t v = 0;
        std::memcpy(&v, data + pos, 4);
        pos += 4;
        return v;
    }
    std::uint16_t u16() {
        std::uint16_t v = 0;
        std::memcpy(&v, data + pos, 2);
        pos += 2;
        return v;
    }
};

// 24-bit is stored as three bytes little-endian, two's complement. Sign
// extension has to be explicit; shifting into the top of a 32-bit int and
// back down is the cheap way to get the compiler to do it correctly.
inline float from24(const std::uint8_t* p) {
    const std::int32_t raw = (static_cast<std::int32_t>(p[0]) << 8) |
                             (static_cast<std::int32_t>(p[1]) << 16) |
                             (static_cast<std::int32_t>(p[2]) << 24);
    return static_cast<float>(raw >> 8) / 8388608.0f;
}

}  // namespace

AudioBufferPtr readWavFile(const std::string& path, std::string& error) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path.c_str(), "rb") != 0 || file == nullptr) {
        error = "could not open " + path;
        return nullptr;
    }
    std::fseek(file, 0, SEEK_END);
    const long length = std::ftell(file);
    std::fseek(file, 0, SEEK_SET);
    if (length <= 44) {
        std::fclose(file);
        error = "not a WAV file (too short): " + path;
        return nullptr;
    }
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));
    const std::size_t got = std::fread(bytes.data(), 1, bytes.size(), file);
    std::fclose(file);
    bytes.resize(got);

    Reader r{bytes.data(), bytes.size()};
    if (!r.need(12) || std::memcmp(r.data, "RIFF", 4) != 0 ||
        std::memcmp(r.data + 8, "WAVE", 4) != 0) {
        error = "not a RIFF/WAVE file: " + path;
        return nullptr;
    }
    r.pos = 12;

    std::uint16_t format = 1;
    std::uint16_t channels = 2;
    std::uint32_t sampleRate = 48000;
    std::uint16_t bits = 16;
    const std::uint8_t* audio = nullptr;
    std::size_t audioBytes = 0;

    // Walk the chunks rather than assuming the canonical 44-byte header.
    // Files that have been through ffmpeg routinely carry a LIST or fact
    // chunk in front of the data, and a reader that assumes a fixed offset
    // reads metadata as audio and produces a burst of noise.
    while (r.need(8)) {
        char id[5] = {0};
        std::memcpy(id, r.data + r.pos, 4);
        r.pos += 4;
        const std::uint32_t chunkSize = r.u32();
        const std::size_t chunkStart = r.pos;
        if (chunkStart + chunkSize > r.size) break;

        if (std::memcmp(id, "fmt ", 4) == 0 && chunkSize >= 16) {
            format = r.u16();
            channels = r.u16();
            sampleRate = r.u32();
            r.pos += 6;  // byte rate and block align, both derivable
            bits = r.u16();
            // WAVE_FORMAT_EXTENSIBLE hides the real format in a GUID whose
            // first two bytes are the format tag it stands in for.
            if (format == 0xFFFE && chunkSize >= 40) {
                format = *reinterpret_cast<const std::uint16_t*>(r.data + chunkStart + 24);
            }
        } else if (std::memcmp(id, "data", 4) == 0) {
            audio = r.data + chunkStart;
            audioBytes = chunkSize;
        }

        // Chunks are word aligned; an odd size carries a pad byte that is
        // not counted in the size field.
        r.pos = chunkStart + chunkSize + (chunkSize & 1u);
    }

    if (audio == nullptr || channels == 0) {
        error = "no audio data in " + path;
        return nullptr;
    }

    const std::size_t bytesPerSample = static_cast<std::size_t>(bits) / 8;
    if (bytesPerSample == 0) {
        error = "unsupported bit depth in " + path;
        return nullptr;
    }
    const std::size_t frames = audioBytes / (bytesPerSample * channels);

    auto out = std::make_shared<AudioBuffer>();
    out->sampleRate = sampleRate;
    out->channels.assign(channels, std::vector<float>(frames, 0.0f));

    for (std::size_t f = 0; f < frames; ++f) {
        for (std::uint16_t c = 0; c < channels; ++c) {
            const std::uint8_t* p =
                audio + (f * channels + c) * bytesPerSample;
            float value = 0.0f;
            if (format == 3 && bits == 32) {
                std::memcpy(&value, p, 4);
            } else if (format == 3 && bits == 64) {
                double d = 0.0;
                std::memcpy(&d, p, 8);
                value = static_cast<float>(d);
            } else if (bits == 16) {
                std::int16_t s = 0;
                std::memcpy(&s, p, 2);
                value = static_cast<float>(s) / 32768.0f;
            } else if (bits == 24) {
                value = from24(p);
            } else if (bits == 32) {
                std::int32_t s = 0;
                std::memcpy(&s, p, 4);
                value = static_cast<float>(s) / 2147483648.0f;
            } else if (bits == 8) {
                // 8-bit WAV is unsigned, unlike every other depth.
                value = (static_cast<float>(p[0]) - 128.0f) / 128.0f;
            }
            out->channels[c][f] = value;
        }
    }

    error.clear();
    return out;
}

AudioBufferPtr resampleTo(const AudioBufferPtr& source, std::uint32_t targetRate) {
    if (!source || source->sampleRate == targetRate || source->frames() == 0) return source;

    const double ratio = static_cast<double>(targetRate) / source->sampleRate;
    const std::size_t outFrames = static_cast<std::size_t>(source->frames() * ratio);

    auto out = std::make_shared<AudioBuffer>();
    out->sampleRate = targetRate;
    out->channels.assign(source->channels.size(), std::vector<float>(outFrames, 0.0f));

    for (std::size_t c = 0; c < source->channels.size(); ++c) {
        const auto& in = source->channels[c];
        auto& dst = out->channels[c];
        for (std::size_t i = 0; i < outFrames; ++i) {
            const double position = static_cast<double>(i) / ratio;
            const std::size_t index = static_cast<std::size_t>(position);
            const double frac = position - static_cast<double>(index);
            const float a = index < in.size() ? in[index] : 0.0f;
            const float b = index + 1 < in.size() ? in[index + 1] : a;
            dst[i] = static_cast<float>(a + (b - a) * frac);
        }
    }
    return out;
}

bool writeWavFile(const std::string& path, const float* interleaved, std::size_t frames,
                  int channels, std::uint32_t sampleRate, std::string& error) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path.c_str(), "wb") != 0 || file == nullptr) {
        error = "could not write " + path;
        return false;
    }
    // 32-bit float, because this writes captures straight off the master bus
    // and anything that has been through a plugin chain can legitimately sit
    // above 0dBFS. Truncating to 16-bit here would clip a take that the
    // mixer could still have rescued.
    const std::uint32_t dataBytes =
        static_cast<std::uint32_t>(frames * static_cast<std::size_t>(channels) * 4);
    const std::uint32_t byteRate = sampleRate * static_cast<std::uint32_t>(channels) * 4;
    const std::uint16_t blockAlign = static_cast<std::uint16_t>(channels * 4);

    auto put32 = [&](std::uint32_t v) { std::fwrite(&v, 4, 1, file); };
    auto put16 = [&](std::uint16_t v) { std::fwrite(&v, 2, 1, file); };

    std::fwrite("RIFF", 1, 4, file);
    put32(36 + dataBytes);
    std::fwrite("WAVEfmt ", 1, 8, file);
    put32(16);
    put16(3);  // IEEE float
    put16(static_cast<std::uint16_t>(channels));
    put32(sampleRate);
    put32(byteRate);
    put16(blockAlign);
    put16(32);
    std::fwrite("data", 1, 4, file);
    put32(dataBytes);
    std::fwrite(interleaved, 4, frames * static_cast<std::size_t>(channels), file);
    std::fclose(file);
    error.clear();
    return true;
}

}  // namespace cashout
