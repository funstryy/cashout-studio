// Cashout Studio audio engine.
//
// A separate process from the UI, on purpose. The studio's front end is a
// web view, and a web view is a thing that stops the world to do layout; an
// audio thread living in the same process as that is an audio thread that
// glitches whenever somebody opens a panel. Out here the render loop is on
// an MMCSS Pro Audio thread with nothing else in the address space that can
// block it, and the UI drives it over a loopback socket - a few dozen bytes
// per gesture, nowhere near the audio path.
//
// The protocol is newline-delimited JSON, one object per line, one reply per
// command. Loopback only, and deliberately not part of the collaboration
// surface: this speaks to the studio's own backend on the same machine and
// nothing else should ever reach it.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <objbase.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "device.h"
#include "json.h"
#include "dsp.h"
#include "mastering.h"
#include "mixer.h"
#include "wav.h"

#if CASHOUT_ENGINE_VST3
#include "vst_insert.h"
#endif

#pragma comment(lib, "ws2_32.lib")

namespace cashout {
namespace {

constexpr int kDefaultPort = 9310;

class Engine {
public:
    std::string handle(const std::string& line);

private:
    std::string reply(bool ok, const std::string& message = {}) {
        json::Writer w;
        w.openObject().field("ok", ok);
        if (!message.empty()) w.field("error", message);
        w.closeObject();
        return w.str();
    }

    std::string describeStatus();
    std::string commitCapture(const json::Object& request);

    // Everything the audio thread is allowed to read lives here, owned by
    // this thread and never mutated once published.
    std::map<std::string, AudioBufferPtr> originals_;
    std::map<std::string, AudioBufferPtr> prepared_;
#if CASHOUT_ENGINE_VST3
    std::vector<std::unique_ptr<VstInsert>> inserts_;
#endif

    AudioDevice device_;
    Mixer mixer_;
    std::int64_t nextClipId_{1};
};

void writeAnalysis(json::Writer& w, const char* key, const Analysis& analysis) {
    w.key(key).openObject()
        .field("lufs", analysis.loudness.integratedLufs)
        .field("shortTermMaxLufs", analysis.loudness.shortTermMaxLufs)
        .field("lra", analysis.loudness.loudnessRange)
        .field("truePeakDb", analysis.loudness.truePeakDb)
        .field("samplePeakDb", analysis.loudness.samplePeakDb)
        .field("crestDb", analysis.loudness.crestDb)
        .field("correlation", analysis.spectrum.correlation)
        .field("width", analysis.spectrum.widthRatio)
        .field("seconds", analysis.durationSeconds)
        .openArray("bands");
    for (int b = 0; b < dsp::SpectralProfile::kBands; ++b) {
        w.openObject().field("hz", analysis.spectrum.centres[b])
            .field("db", analysis.spectrum.bandDb[b]).closeObject();
    }
    w.closeArray().closeObject();
}

void writePlan(json::Writer& w, const MasteringPlan& plan) {
    w.key("plan").openObject()
        .field("highPassHz", plan.highPassHz)
        .field("compressorRatio", plan.compressorRatio)
        .field("compressorThresholdDb", plan.compressorThresholdDb)
        .field("widthScale", plan.widthScale)
        .field("preGainDb", plan.preGainDb)
        .field("ceilingDb", plan.limiterCeilingDb)
        .field("gainReductionDb", plan.estimatedGainReductionDb)
        .field("loudnessMissLu", plan.loudnessMissLu)
        .openArray("eq");
    for (const auto& move : plan.eq) {
        w.openObject().field("hz", move.hz).field("db", move.gainDb).closeObject();
    }
    w.closeArray().closeObject();
}

void writeHarmony(json::Writer& w, const dsp::HarmonyResult& harmony) {
    w.key("harmony").openObject()
        .field("key", harmony.keyRoot >= 0
                          ? std::string(dsp::noteName(harmony.keyRoot)) +
                                (harmony.keyMinor ? " minor" : " major")
                          : std::string("unclear"))
        .field("keyRoot", harmony.keyRoot)
        .field("minor", harmony.keyMinor)
        .field("confidence", harmony.keyConfidence)
        .field("movement", harmony.harmonicMovement)
        .openArray("chords");
    for (const auto& span : harmony.chords) {
        w.openObject()
            .field("name", span.root >= 0
                               ? std::string(dsp::noteName(span.root)) +
                                     (span.minorQuality ? "m" : "")
                               : std::string("-"))
            .field("root", span.root)
            .field("minor", span.minorQuality)
            .field("start", span.startSeconds)
            .field("seconds", span.seconds)
            .field("confidence", span.confidence)
            .closeObject();
    }
    w.closeArray().closeObject();
}

std::string Engine::describeStatus() {
    const auto status = device_.status();
    const auto snap = mixer_.snapshot();

    json::Writer w;
    w.openObject()
        .field("ok", true)
        .field("open", status.open)
        .field("device", status.deviceName)
        .field("exclusive", status.exclusive)
        .field("sampleRate", static_cast<long long>(status.sampleRate))
        .field("bufferFrames", static_cast<long long>(status.bufferFrames))
        .field("channels", status.channels)
        .field("latencyMs", status.outputLatencyMs)
        .field("framesRendered", static_cast<long long>(status.framesRendered))
        .field("xruns", static_cast<long long>(status.xruns))
        .field("loadPeak", status.loadPeak)
        .field("loadRecent", status.loadRecent)
        .field("playing", snap.playing)
        .field("position", snap.positionSeconds)
        .field("tempo", snap.tempo)
        .field("clips", snap.activeClips)
        .field("peakL", static_cast<double>(snap.masterPeakL))
        .field("peakR", static_cast<double>(snap.masterPeakR))
        .field("captureSeconds",
               status.sampleRate > 0
                   ? static_cast<double>(mixer_.capture().capacityFrames()) / status.sampleRate
                   : 0.0)
        .closeObject();
    return w.str();
}

// Retrospective capture.
//
// The engine has been holding the last thirty seconds of the master bus in
// a ring since the device opened, because it costs one memcpy per block to
// do so and nothing to leave running. This is what turns "that take was
// perfect and I wasn't recording" into a file - the take already exists, it
// just has not been written down yet. No DAW I know of offers this for
// audio; a few do it for MIDI, which is the easy half.
std::string Engine::commitCapture(const json::Object& request) {
    const auto status = device_.status();
    if (!status.open) return reply(false, "the device is not open");

    auto pathIt = request.find("path");
    if (pathIt == request.end()) return reply(false, "capture needs a path");

    double seconds = 30.0;
    auto secondsIt = request.find("seconds");
    if (secondsIt != request.end()) seconds = secondsIt->second.asNumber();

    auto& ring = mixer_.capture();
    const std::size_t wanted = (std::min)(ring.capacityFrames(),
                                          static_cast<std::size_t>(seconds * status.sampleRate));
    if (wanted == 0) return reply(false, "nothing captured yet");

    std::vector<float> audio(wanted * 2, 0.0f);
    ring.readLatest(audio.data(), wanted);

    std::string error;
    if (!writeWavFile(pathIt->second.asString(), audio.data(), wanted, 2, status.sampleRate,
                      error)) {
        return reply(false, error);
    }

    json::Writer w;
    w.openObject()
        .field("ok", true)
        .field("path", pathIt->second.asString())
        .field("seconds", static_cast<double>(wanted) / status.sampleRate)
        .closeObject();
    return w.str();
}

std::string Engine::handle(const std::string& line) {
    json::Object request;
    if (!json::parseObject(line, request)) return reply(false, "not a JSON object");

    auto commandIt = request.find("cmd");
    if (commandIt == request.end()) return reply(false, "no cmd");
    const std::string cmd = commandIt->second.asString();

    auto number = [&](const char* key, double fallback) {
        auto it = request.find(key);
        return it == request.end() ? fallback : it->second.asNumber();
    };
    auto integer = [&](const char* key, long long fallback) {
        auto it = request.find(key);
        return it == request.end() ? fallback : it->second.asInt();
    };
    auto flag = [&](const char* key, bool fallback) {
        auto it = request.find(key);
        return it == request.end() ? fallback : it->second.asBool();
    };
    auto text = [&](const char* key) {
        auto it = request.find(key);
        return it == request.end() ? std::string() : it->second.asString();
    };
    auto frames = [&](double seconds) {
        const auto rate = device_.status().sampleRate;
        return static_cast<std::int64_t>(seconds * (rate > 0 ? rate : 48000));
    };

    if (cmd == "ping") return reply(true);

    if (cmd == "devices") {
        json::Writer w;
        w.openObject().field("ok", true).openArray("devices");
        for (const auto& info : enumerateOutputDevices()) {
            w.openObject()
                .field("id", info.id)
                .field("name", info.name)
                .field("default", info.isDefault)
                .field("mixRate", static_cast<long long>(info.mixRate))
                .field("channels", info.mixChannels)
                .closeObject();
        }
        w.closeArray().closeObject();
        return w.str();
    }

    if (cmd == "open") {
        DeviceConfig config;
        config.deviceId = text("device");
        config.sampleRate = static_cast<std::uint32_t>(integer("sampleRate", 48000));
        config.bufferFrames = static_cast<std::uint32_t>(integer("bufferFrames", 256));
        config.exclusive = flag("exclusive", false);

        std::string error;
        if (!device_.open(config, [this](float* out, std::uint32_t f, int c) {
                mixer_.render(out, f, c);
            }, error)) {
            return reply(false, error);
        }

        const auto status = device_.status();
        mixer_.prepare(status.sampleRate, status.bufferFrames);

        // Everything already loaded has to be brought to the new rate, or a
        // project opened before the device was chosen plays back at the
        // wrong pitch.
        prepared_.clear();
        for (const auto& entry : originals_) {
            prepared_[entry.first] = resampleTo(entry.second, status.sampleRate);
        }
        return describeStatus();
    }

    if (cmd == "close") {
        device_.close();
        return reply(true);
    }

    if (cmd == "status") return describeStatus();

    if (cmd == "load") {
        const std::string id = text("id");
        const std::string path = text("path");
        if (id.empty() || path.empty()) return reply(false, "load needs id and path");

        std::string error;
        auto buffer = readWavFile(path, error);
        if (!buffer) return reply(false, error);

        originals_[id] = buffer;
        const auto rate = device_.status().sampleRate;
        prepared_[id] = rate > 0 ? resampleTo(buffer, rate) : buffer;

        json::Writer w;
        w.openObject()
            .field("ok", true)
            .field("id", id)
            .field("seconds", prepared_[id]->seconds())
            .field("channels", prepared_[id]->channelCount())
            .field("sourceRate", static_cast<long long>(buffer->sampleRate))
            .closeObject();
        return w.str();
    }

    if (cmd == "unload") {
        // Only safe with the clips gone first, which the caller is expected
        // to have done - the mixer holds raw pointers into these.
        const std::string id = text("id");
        originals_.erase(id);
        prepared_.erase(id);
        return reply(true);
    }

    if (cmd == "track") {
        Command c;
        c.type = CommandType::SetTrack;
        c.track = static_cast<int>(integer("index", 0));
        c.gain = static_cast<float>(number("gain", 1.0));
        c.pan = static_cast<float>(number("pan", 0.0));
        c.flagA = flag("mute", false);
        c.flagB = flag("solo", false);
        c.flagC = flag("armed", false);
        return reply(mixer_.post(c), "the engine is not accepting commands");
    }

    if (cmd == "addClip") {
        const std::string bufferId = text("buffer");
        auto it = prepared_.find(bufferId);
        if (it == prepared_.end()) return reply(false, "no such buffer: " + bufferId);

        const double trimStart = number("trimStart", 0.0);
        const double trimEnd = number("trimEnd", it->second->seconds());

        Command c;
        c.type = CommandType::AddClip;
        c.clipId = nextClipId_++;
        c.track = static_cast<int>(integer("track", 0));
        c.buffer = it->second.get();
        c.startFrame = frames(number("start", 0.0));
        c.trimStartFrame = frames(trimStart);
        c.lengthFrames = frames((std::max)(0.0, trimEnd - trimStart));
        c.gain = static_cast<float>(number("gain", 1.0));
        if (!mixer_.post(c)) return reply(false, "the engine is not accepting commands");

        json::Writer w;
        w.openObject().field("ok", true).field("clipId", static_cast<long long>(c.clipId)).closeObject();
        return w.str();
    }

    if (cmd == "removeClip") {
        Command c;
        c.type = CommandType::RemoveClip;
        c.clipId = integer("clipId", 0);
        return reply(mixer_.post(c));
    }

    if (cmd == "clearClips") {
        Command c;
        c.type = CommandType::ClearClips;
        return reply(mixer_.post(c));
    }

    if (cmd == "play" || cmd == "stop") {
        Command c;
        c.type = CommandType::Transport;
        c.flagA = cmd == "play";
        return reply(mixer_.post(c));
    }

    if (cmd == "seek") {
        Command c;
        c.type = CommandType::Seek;
        c.startFrame = frames(number("seconds", 0.0));
        return reply(mixer_.post(c));
    }

    if (cmd == "loop") {
        Command c;
        c.type = CommandType::SetLoop;
        c.flagA = flag("enabled", false);
        c.startFrame = frames(number("start", 0.0));
        c.lengthFrames = frames(number("end", 0.0));
        return reply(mixer_.post(c));
    }

    if (cmd == "master") {
        Command c;
        c.type = CommandType::SetMaster;
        c.gain = static_cast<float>(number("gain", 1.0));
        return reply(mixer_.post(c));
    }

    if (cmd == "tempo") {
        Command c;
        c.type = CommandType::SetTempo;
        c.value = number("bpm", 120.0);
        return reply(mixer_.post(c));
    }

    if (cmd == "meters") {
        json::Writer w;
        w.openObject().field("ok", true).openArray("tracks");
        const int count = static_cast<int>(integer("count", 16));
        for (int t = 0; t < count && t < kMaxTracks; ++t) {
            float l = 0.0f, r = 0.0f;
            mixer_.trackPeaks(t, l, r);
            w.openObject().field("l", static_cast<double>(l)).field("r", static_cast<double>(r)).closeObject();
        }
        w.closeArray();
        const auto snap = mixer_.snapshot();
        w.field("masterL", static_cast<double>(snap.masterPeakL))
            .field("masterR", static_cast<double>(snap.masterPeakR))
            .field("position", snap.positionSeconds)
            .closeObject();
        return w.str();
    }

    if (cmd == "analyze" || cmd == "masterFile") {
        const std::string path = text("path");
        if (path.empty()) return reply(false, "give it a file to work on");

        std::string error;
        auto audio = readWavFile(path, error);
        if (!audio) return reply(false, error);
        // Measurement happens at 48kHz because that is the rate BS.1770's
        // published K-weighting coefficients are for. Resampling once here
        // is exact; re-deriving the filters per rate is not worth it.
        audio = resampleTo(audio, 48000);

        if (cmd == "analyze") {
            const auto analysis = analyse(audio);
            json::Writer w;
            w.openObject().field("ok", true);
            writeAnalysis(w, "analysis", analysis);
            writeHarmony(w, dsp::analyseHarmony(audio->channels, audio->sampleRate));
            w.closeObject();
            return w.str();
        }

        MasteringTarget target;
        const std::string referencePath = text("reference");
        if (!referencePath.empty()) {
            auto reference = readWavFile(referencePath, error);
            if (!reference) return reply(false, "reference: " + error);
            reference = resampleTo(reference, 48000);
            target = targetFromReference(analyse(reference), "reference");
        } else {
            target = targetByName(text("target").empty() ? "streaming" : text("target"));
        }
        // An explicit loudness always wins over the preset's, so a rap
        // curve can be delivered at -14 for streaming without giving up
        // the tonal balance that makes it a rap master.
        auto lufsIt = request.find("lufs");
        if (lufsIt != request.end()) target.targetLufs = lufsIt->second.asNumber();
        auto ceilingIt = request.find("ceiling");
        if (ceilingIt != request.end()) target.ceilingDbTp = ceilingIt->second.asNumber();

        const auto result = master(audio, target);
        if (!result.audio) return reply(false, "nothing to master");

        const std::string outPath = text("out");
        if (outPath.empty()) return reply(false, "masterFile needs an out path");
        std::vector<float> interleaved(result.audio->frames() * 2);
        for (std::size_t i = 0; i < result.audio->frames(); ++i) {
            interleaved[i * 2] = result.audio->channels[0][i];
            interleaved[i * 2 + 1] = result.audio->channels[1][i];
        }
        if (!writeWavFile(outPath, interleaved.data(), result.audio->frames(), 2,
                          result.audio->sampleRate, error)) {
            return reply(false, error);
        }

        json::Writer w;
        w.openObject().field("ok", true).field("out", outPath).field("target", target.name)
            .field("targetLufs", target.targetLufs);
        writeAnalysis(w, "before", result.before);
        writeAnalysis(w, "after", result.after);
        writePlan(w, result.plan);
        w.closeObject();
        return w.str();
    }

    if (cmd == "targets") {
        json::Writer w;
        w.openObject().field("ok", true).openArray("targets");
        for (const auto& name : targetNames()) {
            const auto t = targetByName(name);
            w.openObject().field("name", name).field("lufs", t.targetLufs)
                .field("ceiling", t.ceilingDbTp).closeObject();
        }
        w.closeArray().closeObject();
        return w.str();
    }

    if (cmd == "listen") {
        // The co-producer's ear. Same ring the retrospective capture reads,
        // but measured in memory rather than written out - this gets polled
        // every couple of seconds while someone works, and a WAV per poll
        // would be a gigabyte an hour of disk churn for numbers nobody
        // keeps.
        const auto status = device_.status();
        if (!status.open) return reply(false, "the engine is not playing anything");

        double seconds = 8.0;
        auto secondsIt = request.find("seconds");
        if (secondsIt != request.end()) seconds = secondsIt->second.asNumber();

        auto& ring = mixer_.capture();
        const std::size_t wanted =
            (std::min)(ring.capacityFrames(),
                       static_cast<std::size_t>(seconds * status.sampleRate));
        if (wanted < status.sampleRate) return reply(false, "not enough audio yet");

        std::vector<float> interleaved(wanted * 2, 0.0f);
        ring.readLatest(interleaved.data(), wanted);

        auto window = std::make_shared<AudioBuffer>();
        window->sampleRate = status.sampleRate;
        window->channels.assign(2, std::vector<float>(wanted, 0.0f));
        for (std::size_t i = 0; i < wanted; ++i) {
            window->channels[0][i] = interleaved[i * 2];
            window->channels[1][i] = interleaved[i * 2 + 1];
        }

        // Silence is not a mix problem, and measuring it produces a page of
        // alarming numbers about nothing.
        double energy = 0.0;
        for (std::size_t i = 0; i < wanted * 2; ++i) energy += std::fabs(interleaved[i]);
        if (energy / (wanted * 2) < 1e-5) {
            json::Writer w;
            w.openObject().field("ok", true).field("silent", true).closeObject();
            return w.str();
        }

        const auto analysis = analyse(window);
        json::Writer w;
        w.openObject().field("ok", true).field("silent", false)
            .field("position", mixer_.snapshot().positionSeconds);
        writeAnalysis(w, "analysis", analysis);
        writeHarmony(w, dsp::analyseHarmony(window->channels, window->sampleRate));
        w.openArray("tracks");
        const int count = static_cast<int>(
            request.count("tracks") ? request.at("tracks").asInt() : 16);
        for (int t = 0; t < count && t < kMaxTracks; ++t) {
            float l = 0.0f, r = 0.0f;
            mixer_.trackPeaks(t, l, r);
            w.openObject().field("l", static_cast<double>(l))
                .field("r", static_cast<double>(r)).closeObject();
        }
        w.closeArray().closeObject();
        return w.str();
    }

    if (cmd == "capture") return commitCapture(request);

#if CASHOUT_ENGINE_VST3
    if (cmd == "insert") {
        const std::string path = text("path");
        const int track = static_cast<int>(integer("track", 0));
        const int slot = static_cast<int>(integer("slot", 0));
        const auto status = device_.status();

        if (path.empty()) {
            Command c;
            c.type = CommandType::SetInsert;
            c.track = track;
            c.slot = slot;
            c.insert = nullptr;
            return reply(mixer_.post(c));
        }

        std::string error;
        auto insert = VstInsert::load(path, status.sampleRate > 0 ? status.sampleRate : 48000,
                                      status.bufferFrames > 0 ? status.bufferFrames : 256, error);
        if (!insert) return reply(false, error);

        Command c;
        c.type = CommandType::SetInsert;
        c.track = track;
        c.slot = slot;
        c.insert = insert.get();
        if (!mixer_.post(c)) return reply(false, "the engine is not accepting commands");
        // Kept alive for the life of the process. Unloading a plugin the
        // audio thread might still be inside needs a retire handshake, and
        // holding a few megabytes is the cheaper correctness.
        inserts_.push_back(std::move(insert));

        json::Writer w;
        w.openObject().field("ok", true).field("name", inserts_.back()->name()).closeObject();
        return w.str();
    }
#endif

    return reply(false, "unknown command: " + cmd);
}

int serve(int port) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        std::fprintf(stderr, "winsock failed to start\n");
        return 1;
    }

    SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) {
        std::fprintf(stderr, "could not create the control socket\n");
        return 1;
    }

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<u_short>(port));
    // Loopback, hard-coded. The engine has no authentication and no reason
    // to acquire any: it is an implementation detail of one machine's
    // studio, and binding it anywhere else would hand the whole mixer to
    // the network.
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);

    if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
        std::fprintf(stderr, "port %d is already in use\n", port);
        closesocket(listener);
        return 1;
    }
    listen(listener, 4);

    // Printed so a parent process can wait for readiness on stdout rather
    // than polling the port.
    std::printf("cashout-engine listening on 127.0.0.1:%d\n", port);
    std::fflush(stdout);

    Engine engine;

    for (;;) {
        SOCKET client = accept(listener, nullptr, nullptr);
        if (client == INVALID_SOCKET) break;

        // Nagle off: these are tiny request/response pairs, and a 40ms delay
        // waiting for a full segment is an eternity on a fader move.
        BOOL noDelay = TRUE;
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&noDelay),
                   sizeof(noDelay));

        std::string pending;
        char chunk[4096];
        for (;;) {
            const int got = recv(client, chunk, sizeof(chunk), 0);
            if (got <= 0) break;
            pending.append(chunk, static_cast<std::size_t>(got));

            std::size_t newline;
            while ((newline = pending.find('\n')) != std::string::npos) {
                const std::string line = pending.substr(0, newline);
                pending.erase(0, newline + 1);
                if (line.empty()) continue;

                std::string response = engine.handle(line);
                response += '\n';
                int sent = 0;
                while (sent < static_cast<int>(response.size())) {
                    const int wrote = send(client, response.data() + sent,
                                           static_cast<int>(response.size()) - sent, 0);
                    if (wrote <= 0) break;
                    sent += wrote;
                }
            }
        }
        closesocket(client);
    }

    closesocket(listener);
    WSACleanup();
    return 0;
}

}  // namespace
}  // namespace cashout

int main(int argc, char** argv) {
    // Before anything else touches COM. Plugin DLLs are loaded on this
    // thread and expect an apartment that already exists; the standalone
    // host learned that the hard way when Serum faulted on a thread that
    // had been made MTA by the device enumerator first.
    OleInitialize(nullptr);

    int port = 9310;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--port" && i + 1 < argc) port = std::atoi(argv[++i]);
    }
    return cashout::serve(port);
}
