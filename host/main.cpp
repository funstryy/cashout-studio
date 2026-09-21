// Cashout Studio's out-of-process VST3 host.
//
// Deliberately a separate executable, for the same reason every serious DAW
// does it: a plugin is third-party native code, and one that faults should
// take down a throwaway process rather than the studio. It is also the only
// way to reach native plugins at all from an app whose UI is a WebView - the
// browser sandbox cannot load a DLL.
//
// Audio crosses the boundary as raw interleaved float32, not WAV. The Python
// side already decodes every format through ffmpeg, so handing over bare
// samples keeps container parsing - and a whole class of bugs - out of C++.
//
// Modes:
//   scan    --plugin <path>                     describe the plugin as JSON
//   edit    --plugin <path> --state <file>      open the plugin's own window
//   process --plugin <path> --in <raw> --out <raw> [--rate N] [--channels N]
//           [--block N] [--state <file>] [--param <index>=<0..1>]...
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "pluginterfaces/base/ibstream.h"

#include "editor_window.h"

#include <cstdint>

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace {

std::string toUtf8(const Steinberg::Vst::String128 text) {
    std::string out;
    for (int i = 0; i < 128 && text[i] != 0; ++i) {
        out.push_back(text[i] < 128 ? static_cast<char>(text[i]) : '?');
    }
    return out;
}

// Just enough escaping for names and vendor strings.
std::string jsonEscape(const std::string& value) {
    std::string out;
    for (char c : value) {
        if (c == '"' || c == '\\') { out.push_back('\\'); out.push_back(c); }
        else if (c == '\n') { out += "\\n"; }
        else if (static_cast<unsigned char>(c) < 0x20) { out += ' '; }
        else { out.push_back(c); }
    }
    return out;
}

void fail(const std::string& message) {
    std::cout << "{\"ok\":false,\"error\":\"" << jsonEscape(message) << "\"}" << std::endl;
}

struct Args {
    std::string mode, pluginPath, inPath, outPath, statePath;
    double sampleRate = 44100.0;
    int channels = 2;
    int blockSize = 512;
    std::map<int, double> params;   // parameter index -> normalised value
};

bool parseArgs(int argc, char** argv, Args& args) {
    if (argc < 2) return false;
    args.mode = argv[1];
    for (int i = 2; i < argc; ++i) {
        std::string key = argv[i];
        auto next = [&]() -> std::string { return (i + 1 < argc) ? argv[++i] : std::string(); };
        if (key == "--plugin") args.pluginPath = next();
        else if (key == "--in") args.inPath = next();
        else if (key == "--out") args.outPath = next();
        else if (key == "--state") args.statePath = next();
        else if (key == "--rate") args.sampleRate = std::stod(next());
        else if (key == "--channels") args.channels = std::stoi(next());
        else if (key == "--block") args.blockSize = std::stoi(next());
        else if (key == "--param") {
            std::string pair = next();
            auto eq = pair.find('=');
            if (eq != std::string::npos) {
                args.params[std::stoi(pair.substr(0, eq))] = std::stod(pair.substr(eq + 1));
            }
        }
    }
    return !args.mode.empty() && !args.pluginPath.empty();
}

// A plugin's settings, in the plugin's own terms.
//
// Three parts, because no one of them is enough alone. The component blob is
// everything the processor needs - a synth's wavetables, a corrector's scale
// and key. The controller blob is what only the interface knows, such as
// which page was open. The parameter list carries edits made in the editor
// that never reached the processor: a host delivers those through process(),
// and an editing session processes nothing.
struct PluginState {
    std::vector<char> component;
    std::vector<char> controller;
    std::map<ParamID, double> params;
};

const char kStateMagic[4] = {'C', 'S', 'V', 'S'};

bool readState(const std::string& path, PluginState& state) {
    std::ifstream file(path, std::ios::binary);
    if (!file) return false;
    char magic[4] = {};
    file.read(magic, 4);
    if (file.gcount() != 4 || std::memcmp(magic, kStateMagic, 4) != 0) return false;

    auto readU32 = [&]() -> uint32_t {
        uint32_t value = 0;
        file.read(reinterpret_cast<char*>(&value), sizeof(value));
        return value;
    };
    if (readU32() != 1u) return false;   // version
    const uint32_t componentSize = readU32();
    const uint32_t controllerSize = readU32();
    const uint32_t paramCount = readU32();

    state.component.resize(componentSize);
    if (componentSize) file.read(state.component.data(), componentSize);
    state.controller.resize(controllerSize);
    if (controllerSize) file.read(state.controller.data(), controllerSize);
    for (uint32_t i = 0; i < paramCount && file.good(); ++i) {
        const uint32_t id = readU32();
        double value = 0;
        file.read(reinterpret_cast<char*>(&value), sizeof(value));
        state.params[id] = value;
    }
    return true;
}

bool writeState(const std::string& path, const PluginState& state) {
    std::ofstream file(path, std::ios::binary);
    if (!file) return false;
    auto writeU32 = [&](uint32_t value) { file.write(reinterpret_cast<const char*>(&value), sizeof(value)); };
    file.write(kStateMagic, 4);
    writeU32(1u);
    writeU32(static_cast<uint32_t>(state.component.size()));
    writeU32(static_cast<uint32_t>(state.controller.size()));
    writeU32(static_cast<uint32_t>(state.params.size()));
    if (!state.component.empty()) file.write(state.component.data(), state.component.size());
    if (!state.controller.empty()) file.write(state.controller.data(), state.controller.size());
    for (const auto& entry : state.params) {
        writeU32(entry.first);
        file.write(reinterpret_cast<const char*>(&entry.second), sizeof(double));
    }
    return static_cast<bool>(file);
}

// Plugins ask the host for permission to change a parameter and tell it when
// they have. Most refuse to react to a knob at all when no handler is set, so
// an editor without one looks alive and does nothing.
//
// The second interface is not optional in practice. A plugin whose interface
// moves several parameters at once - a macro knob, a preset load - wraps that
// in a group edit, and asks the handler for IComponentHandler2 to do it.
class EditHandler : public IComponentHandler, public IComponentHandler2 {
public:
    tresult PLUGIN_API beginEdit(ParamID) SMTG_OVERRIDE { return kResultOk; }
    tresult PLUGIN_API performEdit(ParamID id, ParamValue value) SMTG_OVERRIDE {
        edits[id] = value;
        return kResultOk;
    }
    tresult PLUGIN_API endEdit(ParamID) SMTG_OVERRIDE { return kResultOk; }
    // A plugin asks for a restart when its parameter list itself changed -
    // loading a preset, switching a mode. Nothing to rebuild here: the current
    // values are read back off the controller once the window closes.
    tresult PLUGIN_API restartComponent(int32) SMTG_OVERRIDE { return kResultOk; }

    // There is no undo history to group into and no project to mark dirty:
    // this process exists for one editing session and saves on the way out.
    tresult PLUGIN_API setDirty(TBool) SMTG_OVERRIDE { return kResultOk; }
    tresult PLUGIN_API requestOpenEditor(FIDString) SMTG_OVERRIDE { return kResultFalse; }
    tresult PLUGIN_API startGroupEdit() SMTG_OVERRIDE { return kResultOk; }
    tresult PLUGIN_API finishGroupEdit() SMTG_OVERRIDE { return kResultOk; }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) SMTG_OVERRIDE {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IComponentHandler)
        QUERY_INTERFACE(iid, obj, IComponentHandler::iid, IComponentHandler)
        QUERY_INTERFACE(iid, obj, IComponentHandler2::iid, IComponentHandler2)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() SMTG_OVERRIDE { return 1; }
    uint32 PLUGIN_API release() SMTG_OVERRIDE { return 1; }

    std::map<ParamID, ParamValue> edits;
};

// Loads the module and the first audio-effect class it declares.
//
// The setup is handed to the SDK's own PlugProvider rather than written by
// hand. Initialising a component and its controller is not just two calls:
// they have to be connected through IConnectionPoint, and the component's
// state passed to the controller, before a plugin will report its parameters
// at all. Doing that by hand returned an empty parameter list for every
// plugin tested; PlugProvider gets it right.
struct Loaded {
    VST3::Hosting::Module::Ptr module;
    IPtr<PlugProvider> provider;
    IPtr<IComponent> component;
    IPtr<IAudioProcessor> processor;
    IPtr<IEditController> controller;
    std::string name, vendor, category;
};

bool load(const std::string& path, HostApplication& host, Loaded& out, std::string& error) {
    out.module = VST3::Hosting::Module::create(path, error);
    if (!out.module) return false;

    auto factory = out.module->getFactory();
    for (auto& classInfo : factory.classInfos()) {
        if (classInfo.category() != kVstAudioEffectClass) continue;
        out.name = classInfo.name();
        out.vendor = classInfo.vendor();
        out.category = classInfo.subCategoriesString();

        out.provider = owned(new PlugProvider(factory, classInfo, true));
        if (!out.provider->initialize()) { error = "plugin initialisation failed"; return false; }

        out.component = out.provider->getComponentPtr();
        out.controller = out.provider->getControllerPtr();
        if (!out.component) { error = "plugin exposes no component"; return false; }

        out.processor = FUnknownPtr<IAudioProcessor>(out.component);
        if (!out.processor) { error = "plugin exposes no audio processor"; return false; }
        return true;
    }
    error = "no audio effect class in module";
    return false;
}

void applyState(Loaded& plugin, const PluginState& state) {
    if (!state.component.empty()) {
        MemoryStream stream(const_cast<char*>(state.component.data()),
                            static_cast<TSize>(state.component.size()));
        plugin.component->setState(&stream);
        // The controller is handed the component's blob, not its own: that is
        // how it learns what the processor is actually set to.
        stream.seek(0, IBStream::kIBSeekSet, nullptr);
        if (plugin.controller) plugin.controller->setComponentState(&stream);
    }
    if (plugin.controller && !state.controller.empty()) {
        MemoryStream stream(const_cast<char*>(state.controller.data()),
                            static_cast<TSize>(state.controller.size()));
        plugin.controller->setState(&stream);
    }
    if (plugin.controller) {
        for (const auto& entry : state.params)
            plugin.controller->setParamNormalized(entry.first, entry.second);
    }
}

void captureState(Loaded& plugin, PluginState& state) {
    MemoryStream componentStream;
    if (plugin.component->getState(&componentStream) == kResultOk) {
        state.component.assign(componentStream.getData(),
                               componentStream.getData() + componentStream.getSize());
    }
    if (!plugin.controller) return;

    MemoryStream controllerStream;
    if (plugin.controller->getState(&controllerStream) == kResultOk) {
        state.controller.assign(controllerStream.getData(),
                                controllerStream.getData() + controllerStream.getSize());
    }
    // Every parameter is read back, not only the ones the plugin reported
    // editing: loading a preset moves dozens of values at once without a
    // performEdit for each.
    const int32 count = plugin.controller->getParameterCount();
    for (int32 i = 0; i < count; ++i) {
        ParameterInfo info{};
        if (plugin.controller->getParameterInfo(i, info) != kResultOk) continue;
        if (info.flags & ParameterInfo::kIsReadOnly) continue;
        state.params[info.id] = plugin.controller->getParamNormalized(info.id);
    }
}

// Show the plugin's own interface and keep whatever the user did with it.
//
// This blocks until the window is closed, which is the point: the process
// exists for the length of one editing session and the studio polls it.
int doEdit(const Args& args, HostApplication& host) {
    Loaded plugin;
    std::string error;
    if (!load(args.pluginPath, host, plugin, error)) { fail(error); return 1; }

    PluginState previous;
    if (!args.statePath.empty() && readState(args.statePath, previous)) applyState(plugin, previous);

    // Bring the plugin up as a DAW would before showing its interface. An
    // editor is not a separate, passive thing: a synth's window draws what its
    // processor holds, so opening one on a component that was never given a
    // sample rate or activated is reading from a plugin that has not finished
    // setting itself up. Serum takes the process down doing exactly that.
    ProcessSetup setup{};
    setup.processMode = kRealtime;
    setup.symbolicSampleSize = kSample32;
    setup.maxSamplesPerBlock = args.blockSize;
    setup.sampleRate = args.sampleRate;
    plugin.processor->setupProcessing(setup);
    for (int32 bus = 0; bus < plugin.component->getBusCount(kAudio, kInput); ++bus)
        plugin.component->activateBus(kAudio, kInput, bus, bus == 0);
    for (int32 bus = 0; bus < plugin.component->getBusCount(kAudio, kOutput); ++bus)
        plugin.component->activateBus(kAudio, kOutput, bus, bus == 0);
    plugin.component->setActive(true);

    EditHandler handler;
    if (plugin.controller) plugin.controller->setComponentHandler(&handler);

    if (!runPluginEditor(plugin.controller, plugin.name, error)) { fail(error); return 1; }

    plugin.component->setActive(false);

    PluginState saved;
    captureState(plugin, saved);
    for (const auto& entry : handler.edits) saved.params[entry.first] = entry.second;
    if (plugin.controller) plugin.controller->setComponentHandler(nullptr);

    if (!args.statePath.empty() && !writeState(args.statePath, saved)) {
        fail("could not write plugin state to " + args.statePath);
        return 1;
    }
    std::cout << "{\"ok\":true,\"parameters\":" << saved.params.size()
              << ",\"state_bytes\":" << saved.component.size() << "}" << std::endl;
    return 0;
}

int doScan(const Args& args, HostApplication& host) {
    Loaded plugin;
    std::string error;
    if (!load(args.pluginPath, host, plugin, error)) { fail(error); return 1; }

    const int32 inputs = plugin.component->getBusCount(kAudio, kInput);
    const int32 outputs = plugin.component->getBusCount(kAudio, kOutput);

    std::cout << "{\"ok\":true,\"name\":\"" << jsonEscape(plugin.name)
              << "\",\"vendor\":\"" << jsonEscape(plugin.vendor)
              << "\",\"category\":\"" << jsonEscape(plugin.category)
              << "\",\"audio_inputs\":" << inputs
              << ",\"audio_outputs\":" << outputs
              << ",\"parameters\":[";

    if (plugin.controller) {
        const int32 count = plugin.controller->getParameterCount();
        for (int32 i = 0; i < count; ++i) {
            ParameterInfo info{};
            if (plugin.controller->getParameterInfo(i, info) != kResultOk) continue;
            if (i) std::cout << ",";
            std::cout << "{\"index\":" << i
                      << ",\"id\":" << info.id
                      << ",\"title\":\"" << jsonEscape(toUtf8(info.title))
                      << "\",\"units\":\"" << jsonEscape(toUtf8(info.units))
                      << "\",\"default\":" << info.defaultNormalizedValue
                      << ",\"steps\":" << info.stepCount << "}";
        }
    }
    std::cout << "]}" << std::endl;

    // PlugProvider owns teardown.
    return 0;
}

int doProcess(const Args& args, HostApplication& host) {
    Loaded plugin;
    std::string error;
    if (!load(args.pluginPath, host, plugin, error)) { fail(error); return 1; }

    // Whatever the user set up in the plugin's own window, restored before
    // anything else is configured: a component state can change which bus
    // layout the plugin wants, so it has to land before setBusArrangements.
    PluginState state;
    if (!args.statePath.empty() && readState(args.statePath, state)) applyState(plugin, state);

    std::ifstream input(args.inPath, std::ios::binary);
    if (!input) { fail("cannot open input: " + args.inPath); return 1; }
    // Read as bytes, then reinterpret. Filling a vector<float> from a byte
    // iterator widens each byte into its own float - values 0..255, which
    // clip to full scale and sound like noise rather than the take.
    std::vector<char> bytes((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    input.close();
    const size_t totalFloats = bytes.size() / sizeof(float);
    std::vector<float> samples(totalFloats);
    std::memcpy(samples.data(), bytes.data(), totalFloats * sizeof(float));

    const int channels = args.channels;
    const size_t frames = totalFloats / channels;

    // Tell the plugin the channel layout before anything else. Skipping this
    // is why a plugin can accept every call and still emit silence: it never
    // agreed to a stereo arrangement, so it has nothing to write into.
    SpeakerArrangement stereo = SpeakerArr::kStereo;
    const int32 inputBuses = plugin.component->getBusCount(kAudio, kInput);
    const int32 outputBuses = plugin.component->getBusCount(kAudio, kOutput);
    if (inputBuses > 0 && outputBuses > 0) {
        plugin.processor->setBusArrangements(&stereo, 1, &stereo, 1);
    }

    ProcessSetup setup{};
    setup.processMode = kOffline;          // rendering, not live monitoring
    setup.symbolicSampleSize = kSample32;
    setup.maxSamplesPerBlock = args.blockSize;
    setup.sampleRate = args.sampleRate;
    if (plugin.processor->setupProcessing(setup) != kResultOk) { fail("setupProcessing rejected"); return 1; }

    // Only the main buses. Auto-Tune, for one, exposes a second input bus for
    // sidechain; activating it would oblige us to hand over a buffer for it
    // on every process call, for audio we do not have.
    for (int32 bus = 0; bus < inputBuses; ++bus)
        plugin.component->activateBus(kAudio, kInput, bus, bus == 0);
    for (int32 bus = 0; bus < outputBuses; ++bus)
        plugin.component->activateBus(kAudio, kOutput, bus, bus == 0);

    if (plugin.component->setActive(true) != kResultOk) { fail("setActive failed"); return 1; }
    plugin.processor->setProcessing(true);

    // Parameters reach the *processor* only through the change queues of a
    // process call. Setting them on the controller alone moves the knobs and
    // not the sound, which is why an editing session's values are replayed
    // here. They go on the first block and hold for the rest of the render;
    // automation curves are a later problem.
    std::map<ParamID, double> wanted = state.params;
    if (plugin.controller) {
        for (const auto& [index, value] : args.params) {
            ParameterInfo info{};
            if (plugin.controller->getParameterInfo(index, info) == kResultOk) wanted[info.id] = value;
        }
        for (const auto& entry : wanted)
            plugin.controller->setParamNormalized(entry.first, entry.second);
    }

    ParameterChanges changes;
    for (const auto& entry : wanted) {
        int32 queueIndex = 0;
        if (IParamValueQueue* queue = changes.addParameterData(entry.first, queueIndex)) {
            int32 pointIndex = 0;
            queue->addPoint(0, entry.second, pointIndex);
        }
    }

    std::vector<std::vector<float>> inputChannels(channels, std::vector<float>(args.blockSize, 0.f));
    std::vector<std::vector<float>> outputChannels(channels, std::vector<float>(args.blockSize, 0.f));
    std::vector<float*> inPtrs(channels), outPtrs(channels);
    for (int c = 0; c < channels; ++c) { inPtrs[c] = inputChannels[c].data(); outPtrs[c] = outputChannels[c].data(); }

    AudioBusBuffers inBus{}, outBus{};
    inBus.numChannels = channels;  inBus.channelBuffers32 = inPtrs.data();
    outBus.numChannels = channels; outBus.channelBuffers32 = outPtrs.data();

    ProcessContext context{};
    context.sampleRate = args.sampleRate;
    context.state = ProcessContext::kPlaying;

    // A plugin that looks ahead - any pitch corrector, any compressor with
    // lookahead - returns its input delayed by this many samples. Feeding that
    // much extra silence past the end and then dropping the same amount from
    // the front realigns the result, so a processed clip still starts where
    // the take did instead of drifting late.
    const int32 latency = plugin.processor->getLatencySamples();
    const size_t framesToPush = frames + static_cast<size_t>(latency);

    std::vector<float> rendered;
    rendered.reserve((frames + latency) * channels);

    for (size_t offset = 0; offset < framesToPush; offset += args.blockSize) {
        const int32 block = static_cast<int32>(std::min<size_t>(args.blockSize, framesToPush - offset));
        for (int c = 0; c < channels; ++c) {
            for (int32 i = 0; i < block; ++i) {
                const size_t frame = offset + i;
                inputChannels[c][i] = frame < frames ? samples[frame * channels + c] : 0.f;
            }
            for (int32 i = block; i < args.blockSize; ++i) inputChannels[c][i] = 0.f;
        }

        context.projectTimeSamples = static_cast<TSamples>(offset);
        ProcessData data{};
        data.processMode = kOffline;
        data.symbolicSampleSize = kSample32;
        data.numSamples = block;
        data.numInputs = inputBuses > 0 ? 1 : 0;
        data.numOutputs = outputBuses > 0 ? 1 : 0;
        data.inputs = data.numInputs ? &inBus : nullptr;
        data.outputs = data.numOutputs ? &outBus : nullptr;
        data.processContext = &context;
        if (offset == 0) data.inputParameterChanges = &changes;

        if (plugin.processor->process(data) != kResultOk) { fail("process() failed"); return 1; }

        for (int32 i = 0; i < block; ++i)
            for (int c = 0; c < channels; ++c)
                rendered.push_back(outputChannels[c][i]);
    }

    plugin.processor->setProcessing(false);
    plugin.component->setActive(false);
    // PlugProvider owns teardown.

    // Drop the plugin's latency from the front, then keep exactly as many
    // frames as came in, so the clip's length on the timeline is unchanged.
    const size_t skip = static_cast<size_t>(latency) * channels;
    const size_t keep = frames * channels;
    const float* start = rendered.data() + std::min(skip, rendered.size());
    const size_t available = std::min(keep, rendered.size() - std::min(skip, rendered.size()));

    std::ofstream output(args.outPath, std::ios::binary);
    if (!output) { fail("cannot write output: " + args.outPath); return 1; }
    output.write(reinterpret_cast<const char*>(start), available * sizeof(float));
    output.close();

    std::cout << "{\"ok\":true,\"frames\":" << (available / channels)
              << ",\"channels\":" << channels
              << ",\"latency_samples\":" << latency << "}" << std::endl;
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    // A COM apartment on this thread, before the plugin DLL is loaded rather
    // than after. Plugin interfaces use drag-and-drop and file pickers, which
    // need one on the thread that pumps their messages - but the timing is
    // what matters: a plugin that builds COM objects while it is loading gets
    // them in the apartment its window will later run in, instead of in none
    // at all. Serum 2 loads and reports itself perfectly either way, and then
    // faults inside attached() when this comes afterwards.
    OleInitialize(nullptr);

    Args args;
    if (!parseArgs(argc, argv, args)) {
        fail("usage: cashout_vst_host <scan|edit|process> --plugin <path> [...]");
        return 2;
    }

    HostApplication host;
    PluginContextFactory::instance().setPluginContext(&host);

    if (args.mode == "scan") return doScan(args, host);
    if (args.mode == "edit") return doEdit(args, host);
    if (args.mode == "process") {
        if (args.inPath.empty() || args.outPath.empty()) { fail("process needs --in and --out"); return 2; }
        return doProcess(args, host);
    }
    fail("unknown mode: " + args.mode);
    return 2;
}
