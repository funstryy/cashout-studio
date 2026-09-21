#include "vst_insert.h"

#include <windows.h>
#include <objbase.h>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <vector>

#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace cashout {
namespace {

// One host application object for every plugin in the process. The SDK hands
// this to each plugin as its host context; plugins query it for interfaces
// and expect it to outlive them.
HostApplication& hostApplication() {
    static HostApplication instance;
    return instance;
}

}  // namespace

struct VstInsert::Impl {
    VST3::Hosting::Module::Ptr module;
    IPtr<PlugProvider> provider;
    IPtr<IComponent> component;
    IPtr<IAudioProcessor> processor;
    IPtr<IEditController> controller;

    std::string name{"plugin"};
    int channels{2};
    double sampleRate{48000.0};
    std::uint32_t maxFrames{1024};
    bool active{false};

    // Everything the audio thread touches, allocated once here.
    std::vector<std::vector<float>> inputStorage;
    std::vector<std::vector<float>> outputStorage;
    std::vector<float*> inputPointers;
    std::vector<float*> outputPointers;
    AudioBusBuffers inputBus{};
    AudioBusBuffers outputBus{};
    ProcessContext context{};
    ParameterChanges changes;
    ParameterChanges emptyChanges;

    // Parameter moves posted from the control thread, picked up by the audio
    // thread at the top of a block. A plain flag plus a small array, because
    // a fader move must not allocate and must not make the control thread
    // wait on the audio thread.
    struct PendingParam {
        std::atomic<bool> set{false};
        std::uint32_t id{0};
        double value{0.0};
    };
    static constexpr int kMaxPending = 64;
    PendingParam pending[kMaxPending];

    int32 inputBusCount{0};
    int32 outputBusCount{0};

    void allocate() {
        inputStorage.assign(static_cast<std::size_t>(channels),
                            std::vector<float>(maxFrames, 0.0f));
        outputStorage.assign(static_cast<std::size_t>(channels),
                             std::vector<float>(maxFrames, 0.0f));
        inputPointers.resize(static_cast<std::size_t>(channels));
        outputPointers.resize(static_cast<std::size_t>(channels));
        for (int c = 0; c < channels; ++c) {
            inputPointers[static_cast<std::size_t>(c)] = inputStorage[static_cast<std::size_t>(c)].data();
            outputPointers[static_cast<std::size_t>(c)] = outputStorage[static_cast<std::size_t>(c)].data();
        }
        inputBus.numChannels = channels;
        inputBus.channelBuffers32 = inputPointers.data();
        outputBus.numChannels = channels;
        outputBus.channelBuffers32 = outputPointers.data();

        context.sampleRate = sampleRate;
        context.state = ProcessContext::kPlaying;

        // Sized once so addParameterData never allocates on the audio
        // thread. The SDK's ParameterChanges grows its queue list on demand,
        // and "on demand" would mean inside process().
        for (int i = 0; i < kMaxPending; ++i) {
            int32 index = 0;
            changes.addParameterData(static_cast<ParamID>(i), index);
        }
        changes.clearQueue();
    }

    bool activate(std::string& error) {
        SpeakerArrangement stereo = SpeakerArr::kStereo;
        inputBusCount = component->getBusCount(kAudio, kInput);
        outputBusCount = component->getBusCount(kAudio, kOutput);
        if (inputBusCount > 0 && outputBusCount > 0) {
            processor->setBusArrangements(&stereo, 1, &stereo, 1);
        }

        ProcessSetup setup{};
        // kRealtime, not kOffline. Plugins genuinely behave differently:
        // anything with lookahead or oversampling picks a cheaper path here,
        // and a few refuse to run at all in the mode they were not told
        // about.
        setup.processMode = kRealtime;
        setup.symbolicSampleSize = kSample32;
        setup.maxSamplesPerBlock = static_cast<int32>(maxFrames);
        setup.sampleRate = sampleRate;
        if (processor->setupProcessing(setup) != kResultOk) {
            error = "the plugin rejected " + std::to_string(static_cast<int>(sampleRate)) +
                    "Hz at " + std::to_string(maxFrames) + " frames";
            return false;
        }

        // Main buses only. A sidechain input activated here would oblige us
        // to hand over a buffer for it every block, for audio we do not have.
        for (int32 bus = 0; bus < inputBusCount; ++bus) {
            component->activateBus(kAudio, kInput, bus, bus == 0);
        }
        for (int32 bus = 0; bus < outputBusCount; ++bus) {
            component->activateBus(kAudio, kOutput, bus, bus == 0);
        }

        if (component->setActive(true) != kResultOk) {
            error = "the plugin refused to activate";
            return false;
        }
        processor->setProcessing(true);
        active = true;
        return true;
    }

    void deactivate() {
        if (!active) return;
        processor->setProcessing(false);
        component->setActive(false);
        active = false;
    }
};

VstInsert::VstInsert() : impl_(std::make_unique<Impl>()) {}

VstInsert::~VstInsert() {
    if (impl_) impl_->deactivate();
}

std::unique_ptr<VstInsert> VstInsert::load(const std::string& path, double sampleRate,
                                           std::uint32_t maxFrames, std::string& error) {
    // A COM apartment on this thread before the plugin's DLL is loaded, not
    // after. Serum 2 loads and reports itself perfectly either way and then
    // faults inside the editor when this comes afterwards - a day was spent
    // on that once already, in the standalone host.
    OleInitialize(nullptr);

    std::unique_ptr<VstInsert> insert(new VstInsert());
    auto& impl = *insert->impl_;
    impl.sampleRate = sampleRate > 0 ? sampleRate : 48000.0;
    impl.maxFrames = (std::max)(maxFrames, 64u);

    impl.module = VST3::Hosting::Module::create(path, error);
    if (!impl.module) return nullptr;

    auto factory = impl.module->getFactory();
    for (auto& classInfo : factory.classInfos()) {
        if (classInfo.category() != kVstAudioEffectClass) continue;
        impl.name = classInfo.name();

        // PlugProvider rather than wiring this by hand: a component and its
        // controller have to be connected through IConnectionPoint and the
        // component's state passed across before a plugin reports any
        // parameters at all. Done manually, every plugin tested came back
        // with an empty parameter list.
        impl.provider = owned(new PlugProvider(factory, classInfo, true));
        if (!impl.provider->initialize()) {
            error = "plugin initialisation failed";
            return nullptr;
        }
        impl.component = impl.provider->getComponentPtr();
        impl.controller = impl.provider->getControllerPtr();
        if (!impl.component) {
            error = "plugin exposes no component";
            return nullptr;
        }
        impl.processor = FUnknownPtr<IAudioProcessor>(impl.component);
        if (!impl.processor) {
            error = "plugin exposes no audio processor";
            return nullptr;
        }

        impl.allocate();
        if (!impl.activate(error)) return nullptr;
        error.clear();
        return insert;
    }

    error = "no audio effect class in " + path;
    return nullptr;
}

void VstInsert::reset(double sampleRate, std::uint32_t maxFrames) {
    auto& impl = *impl_;
    if (impl.sampleRate == sampleRate && impl.maxFrames == maxFrames) return;
    impl.deactivate();
    impl.sampleRate = sampleRate;
    impl.maxFrames = (std::max)(maxFrames, 64u);
    impl.allocate();
    std::string error;
    impl.activate(error);
}

const char* VstInsert::name() const { return impl_->name.c_str(); }

int VstInsert::latencySamples() const {
    return impl_->processor ? static_cast<int>(impl_->processor->getLatencySamples()) : 0;
}

void VstInsert::setParameter(std::uint32_t id, double normalised) {
    auto& impl = *impl_;
    // First free slot wins. Dropping a move when 64 are already queued for
    // one block is the right failure: the next block takes it, and a human
    // hand cannot tell.
    for (int i = 0; i < Impl::kMaxPending; ++i) {
        bool expected = false;
        if (impl.pending[i].set.compare_exchange_strong(expected, false)) {
            impl.pending[i].id = id;
            impl.pending[i].value = normalised;
            impl.pending[i].set.store(true, std::memory_order_release);
            break;
        }
    }
    // The plugin's own UI reads the controller, so it has to be told too or
    // the knob on screen will not move with the automation.
    if (impl.controller) {
        impl.controller->setParamNormalized(static_cast<ParamID>(id), normalised);
    }
}

void VstInsert::process(float* const* channels, int channelCount, std::uint32_t frames) {
    auto& impl = *impl_;
    if (!impl.active || impl.processor == nullptr) return;
    if (frames > impl.maxFrames) frames = impl.maxFrames;

    const int useChannels = (std::min)(channelCount, impl.channels);
    for (int c = 0; c < useChannels; ++c) {
        std::memcpy(impl.inputPointers[static_cast<std::size_t>(c)], channels[c],
                    sizeof(float) * frames);
    }

    // Parameter moves that arrived since the last block.
    impl.changes.clearQueue();
    bool haveChanges = false;
    for (int i = 0; i < Impl::kMaxPending; ++i) {
        if (!impl.pending[i].set.load(std::memory_order_acquire)) continue;
        int32 queueIndex = 0;
        if (auto* queue = impl.changes.addParameterData(
                static_cast<ParamID>(impl.pending[i].id), queueIndex)) {
            int32 pointIndex = 0;
            queue->addPoint(0, impl.pending[i].value, pointIndex);
            haveChanges = true;
        }
        impl.pending[i].set.store(false, std::memory_order_release);
    }

    ProcessData data{};
    data.processMode = kRealtime;
    data.symbolicSampleSize = kSample32;
    data.numSamples = static_cast<int32>(frames);
    data.numInputs = impl.inputBusCount > 0 ? 1 : 0;
    data.numOutputs = impl.outputBusCount > 0 ? 1 : 0;
    data.inputs = data.numInputs ? &impl.inputBus : nullptr;
    data.outputs = data.numOutputs ? &impl.outputBus : nullptr;
    data.processContext = &impl.context;
    data.inputParameterChanges = haveChanges ? &impl.changes : &impl.emptyChanges;

    if (impl.processor->process(data) != kResultOk) return;

    for (int c = 0; c < useChannels; ++c) {
        std::memcpy(channels[c], impl.outputPointers[static_cast<std::size_t>(c)],
                    sizeof(float) * frames);
    }

    impl.context.projectTimeSamples += static_cast<TSamples>(frames);
    impl.context.continousTimeSamples += static_cast<TSamples>(frames);
}

}  // namespace cashout
