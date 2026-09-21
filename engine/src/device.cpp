#include "device.h"

#include <windows.h>

// initguid must come first and is not interchangeable with a forward
// declaration: it is what turns DEFINE_PROPERTYKEY from a declaration into a
// definition, and without it every PKEY_* in functiondiscoverykeys is an
// undeclared identifier at the point the device name is read.
#include <initguid.h>

#include <audioclient.h>
#include <avrt.h>
#include <mmdeviceapi.h>
#include <functiondiscoverykeys_devpkey.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <thread>

namespace cashout {
namespace {

std::string toUtf8(const wchar_t* wide) {
    if (wide == nullptr) return {};
    const int need = WideCharToMultiByte(CP_UTF8, 0, wide, -1, nullptr, 0, nullptr, nullptr);
    if (need <= 1) return {};
    std::string out(static_cast<std::size_t>(need - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide, -1, out.data(), need, nullptr, nullptr);
    return out;
}

std::wstring toWide(const std::string& narrow) {
    if (narrow.empty()) return {};
    const int need = MultiByteToWideChar(CP_UTF8, 0, narrow.c_str(), -1, nullptr, 0);
    if (need <= 1) return {};
    std::wstring out(static_cast<std::size_t>(need - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, narrow.c_str(), -1, out.data(), need);
    return out;
}

template <typename T>
void release(T*& ptr) {
    if (ptr) {
        ptr->Release();
        ptr = nullptr;
    }
}

std::string deviceName(IMMDevice* device) {
    IPropertyStore* props = nullptr;
    if (FAILED(device->OpenPropertyStore(STGM_READ, &props))) return "Unknown device";
    PROPVARIANT value;
    PropVariantInit(&value);
    std::string name = "Unknown device";
    if (SUCCEEDED(props->GetValue(PKEY_Device_FriendlyName, &value)) && value.vt == VT_LPWSTR) {
        name = toUtf8(value.pwszVal);
    }
    PropVariantClear(&value);
    props->Release();
    return name;
}

// 100-nanosecond units, which is what every WASAPI duration is in.
constexpr REFERENCE_TIME kRefTimesPerSecond = 10000000LL;

REFERENCE_TIME framesToRefTime(std::uint32_t frames, std::uint32_t rate) {
    return static_cast<REFERENCE_TIME>((static_cast<double>(frames) * kRefTimesPerSecond) / rate + 0.5);
}

}  // namespace

struct AudioDevice::Impl {
    IMMDeviceEnumerator* enumerator{nullptr};
    IMMDevice* device{nullptr};
    IAudioClient* client{nullptr};
    IAudioRenderClient* render{nullptr};
    HANDLE event{nullptr};
    WAVEFORMATEX* format{nullptr};

    std::thread thread;
    RenderCallback callback;
    std::atomic<bool> stop{false};

    // Status, written by the audio thread and read by anyone.
    std::atomic<std::uint64_t> framesRendered{0};
    std::atomic<std::uint64_t> xruns{0};
    std::atomic<double> loadPeak{0.0};
    std::atomic<double> loadRecent{0.0};

    std::string name;
    std::uint32_t sampleRate{0};
    std::uint32_t bufferFrames{0};
    int channels{0};
    bool exclusive{false};
    bool floatFormat{true};

    std::vector<float> mixScratch;
};

AudioDevice::AudioDevice() : impl_(new Impl()) {}

AudioDevice::~AudioDevice() {
    close();
    delete impl_;
}

std::vector<DeviceInfo> enumerateOutputDevices() {
    std::vector<DeviceInfo> out;
    // Every caller of this is a control thread that may or may not have
    // initialised COM already; asking again is cheap and the matching
    // uninitialise is skipped deliberately, since tearing COM down under a
    // caller that was already using it would be worse than leaking an
    // apartment reference for the life of the process.
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);

    IMMDeviceEnumerator* enumerator = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                IID_PPV_ARGS(&enumerator)))) {
        return out;
    }

    std::string defaultId;
    IMMDevice* defaultDevice = nullptr;
    if (SUCCEEDED(enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &defaultDevice))) {
        LPWSTR id = nullptr;
        if (SUCCEEDED(defaultDevice->GetId(&id))) {
            defaultId = toUtf8(id);
            CoTaskMemFree(id);
        }
        defaultDevice->Release();
    }

    IMMDeviceCollection* collection = nullptr;
    if (SUCCEEDED(enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &collection))) {
        UINT count = 0;
        collection->GetCount(&count);
        for (UINT i = 0; i < count; ++i) {
            IMMDevice* device = nullptr;
            if (FAILED(collection->Item(i, &device))) continue;

            DeviceInfo info;
            LPWSTR id = nullptr;
            if (SUCCEEDED(device->GetId(&id))) {
                info.id = toUtf8(id);
                CoTaskMemFree(id);
            }
            info.name = deviceName(device);
            info.isDefault = !info.id.empty() && info.id == defaultId;

            // The mix format is what shared mode will run at, which is the
            // one number that decides whether a project needs resampling.
            IAudioClient* probe = nullptr;
            if (SUCCEEDED(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                           reinterpret_cast<void**>(&probe)))) {
                WAVEFORMATEX* mix = nullptr;
                if (SUCCEEDED(probe->GetMixFormat(&mix)) && mix != nullptr) {
                    info.mixRate = mix->nSamplesPerSec;
                    info.mixChannels = mix->nChannels;
                    CoTaskMemFree(mix);
                }
                probe->Release();
            }

            out.push_back(std::move(info));
            device->Release();
        }
        collection->Release();
    }
    enumerator->Release();
    return out;
}

bool AudioDevice::open(const DeviceConfig& config, RenderCallback callback, std::string& error) {
    close();
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);

    auto& impl = *impl_;
    impl.callback = std::move(callback);
    impl.stop.store(false);
    impl.framesRendered.store(0);
    impl.xruns.store(0);
    impl.loadPeak.store(0.0);

    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                IID_PPV_ARGS(&impl.enumerator)))) {
        error = "could not create the audio device enumerator";
        return false;
    }

    HRESULT hr;
    if (config.deviceId.empty()) {
        hr = impl.enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &impl.device);
    } else {
        hr = impl.enumerator->GetDevice(toWide(config.deviceId).c_str(), &impl.device);
    }
    if (FAILED(hr) || impl.device == nullptr) {
        error = "no such output device";
        close();
        return false;
    }
    impl.name = deviceName(impl.device);

    if (FAILED(impl.device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                     reinterpret_cast<void**>(&impl.client)))) {
        error = "could not activate " + impl.name;
        close();
        return false;
    }

    const AUDCLNT_SHAREMODE shareMode =
        config.exclusive ? AUDCLNT_SHAREMODE_EXCLUSIVE : AUDCLNT_SHAREMODE_SHARED;

    if (config.exclusive) {
        // Exclusive mode takes the device over entirely, so nothing else on
        // the machine can make a sound - which is the point, and also why it
        // is never the default. The format has to be one the hardware takes
        // natively; there is no mixer in the way to convert.
        WAVEFORMATEXTENSIBLE wanted{};
        wanted.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
        wanted.Format.nChannels = 2;
        wanted.Format.nSamplesPerSec = config.sampleRate;
        wanted.Format.wBitsPerSample = 32;
        wanted.Format.nBlockAlign = static_cast<WORD>(wanted.Format.nChannels * 4);
        wanted.Format.nAvgBytesPerSec = wanted.Format.nSamplesPerSec * wanted.Format.nBlockAlign;
        wanted.Format.cbSize = sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
        wanted.Samples.wValidBitsPerSample = 32;
        wanted.dwChannelMask = SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT;
        wanted.SubFormat = KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;

        WAVEFORMATEX* closest = nullptr;
        hr = impl.client->IsFormatSupported(shareMode, &wanted.Format, &closest);
        if (hr == S_OK) {
            impl.format = static_cast<WAVEFORMATEX*>(CoTaskMemAlloc(sizeof(WAVEFORMATEXTENSIBLE)));
            std::memcpy(impl.format, &wanted, sizeof(WAVEFORMATEXTENSIBLE));
        } else if (closest != nullptr) {
            impl.format = closest;
        } else {
            // Plenty of interfaces only take 24-bit in exclusive mode. Rather
            // than fail the open and leave the user with no sound at all,
            // fall back to 16-bit integer, which everything supports.
            WAVEFORMATEXTENSIBLE pcm = wanted;
            pcm.Format.wBitsPerSample = 16;
            pcm.Format.nBlockAlign = static_cast<WORD>(pcm.Format.nChannels * 2);
            pcm.Format.nAvgBytesPerSec = pcm.Format.nSamplesPerSec * pcm.Format.nBlockAlign;
            pcm.Samples.wValidBitsPerSample = 16;
            pcm.SubFormat = KSDATAFORMAT_SUBTYPE_PCM;
            if (impl.client->IsFormatSupported(shareMode, &pcm.Format, nullptr) != S_OK) {
                error = impl.name + " will not take 32-bit float or 16-bit at " +
                        std::to_string(config.sampleRate) + "Hz in exclusive mode";
                close();
                return false;
            }
            impl.format = static_cast<WAVEFORMATEX*>(CoTaskMemAlloc(sizeof(WAVEFORMATEXTENSIBLE)));
            std::memcpy(impl.format, &pcm, sizeof(WAVEFORMATEXTENSIBLE));
        }
    } else {
        if (FAILED(impl.client->GetMixFormat(&impl.format)) || impl.format == nullptr) {
            error = "could not read the mix format for " + impl.name;
            close();
            return false;
        }
    }

    // Which of the two paths the render loop takes when it writes samples.
    impl.floatFormat = impl.format->wBitsPerSample == 32;
    if (impl.format->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        const auto* ext = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(impl.format);
        impl.floatFormat = IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT) != 0;
    } else {
        impl.floatFormat = impl.format->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
    }

    impl.sampleRate = impl.format->nSamplesPerSec;
    impl.channels = impl.format->nChannels;
    impl.exclusive = config.exclusive;

    REFERENCE_TIME duration = framesToRefTime(config.bufferFrames, impl.sampleRate);
    if (!config.exclusive) {
        // Shared mode ignores the requested duration on Windows 10 and uses
        // the engine period, so asking for less than the minimum is not an
        // error - it just has no effect. Passing the device's own default
        // keeps the numbers in the status honest.
        REFERENCE_TIME defaultPeriod = 0, minPeriod = 0;
        if (SUCCEEDED(impl.client->GetDevicePeriod(&defaultPeriod, &minPeriod))) {
            duration = (std::max)(duration, minPeriod);
        }
    }

    hr = impl.client->Initialize(shareMode, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, duration,
                                 config.exclusive ? duration : 0, impl.format, nullptr);

    if (hr == AUDCLNT_E_BUFFER_SIZE_NOT_ALIGNED) {
        // Exclusive mode insists the buffer be a whole number of the device's
        // own periods. The documented recovery is to ask what size it landed
        // on, throw the client away and start again with exactly that.
        UINT32 aligned = 0;
        impl.client->GetBufferSize(&aligned);
        release(impl.client);
        if (FAILED(impl.device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                         reinterpret_cast<void**>(&impl.client)))) {
            error = "could not reopen " + impl.name + " at an aligned buffer size";
            close();
            return false;
        }
        duration = framesToRefTime(aligned, impl.sampleRate);
        hr = impl.client->Initialize(shareMode, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, duration,
                                     duration, impl.format, nullptr);
    }

    if (FAILED(hr)) {
        if (hr == AUDCLNT_E_DEVICE_IN_USE) {
            error = impl.name + " is already held exclusively by something else";
        } else if (hr == AUDCLNT_E_UNSUPPORTED_FORMAT) {
            error = impl.name + " does not support that format";
        } else {
            char buf[64];
            sprintf_s(buf, "0x%08lX", static_cast<unsigned long>(hr));
            error = std::string("could not initialise ") + impl.name + " (" + buf + ")";
        }
        close();
        return false;
    }

    UINT32 actual = 0;
    impl.client->GetBufferSize(&actual);
    impl.bufferFrames = actual;

    impl.event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (impl.event == nullptr || FAILED(impl.client->SetEventHandle(impl.event))) {
        error = "could not attach the render event";
        close();
        return false;
    }

    if (FAILED(impl.client->GetService(IID_PPV_ARGS(&impl.render)))) {
        error = "could not get the render client";
        close();
        return false;
    }

    // One allocation for the whole session. The render loop must never grow
    // this, so it is sized for the full device buffer up front.
    impl.mixScratch.assign(static_cast<std::size_t>(impl.bufferFrames) * impl.channels, 0.0f);

    running_.store(true, std::memory_order_release);
    impl.thread = std::thread([this] { threadMain(); });
    error.clear();
    return true;
}

void AudioDevice::threadMain() {
    auto& impl = *impl_;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);

    // Without this the thread is just another thread: Windows will happily
    // deschedule it to run a browser's layout pass, and the result is a
    // dropout under exactly the load a DAW is under. MMCSS "Pro Audio" is
    // the scheduling class the OS reserves for this.
    DWORD taskIndex = 0;
    HANDLE task = AvSetMmThreadCharacteristicsW(L"Pro Audio", &taskIndex);
    if (task != nullptr) AvSetMmThreadPriority(task, AVRT_PRIORITY_CRITICAL);

    const double budgetSeconds =
        static_cast<double>(impl.bufferFrames) / (std::max)(1u, impl.sampleRate);

    // Pre-roll silence so the first callback is not racing the first buffer.
    BYTE* data = nullptr;
    if (SUCCEEDED(impl.render->GetBuffer(impl.bufferFrames, &data))) {
        impl.render->ReleaseBuffer(impl.bufferFrames, AUDCLNT_BUFFERFLAGS_SILENT);
    }
    impl.client->Start();

    while (!impl.stop.load(std::memory_order_acquire)) {
        // A timeout here means the device stopped calling us: a USB
        // interface pulled out, or a driver reset. Treated as an xrun and
        // retried rather than silently spinning.
        const DWORD wait = WaitForSingleObject(impl.event, 2000);
        if (wait != WAIT_OBJECT_0) {
            impl.xruns.fetch_add(1, std::memory_order_relaxed);
            continue;
        }

        UINT32 available = impl.bufferFrames;
        if (!impl.exclusive) {
            UINT32 padding = 0;
            if (FAILED(impl.client->GetCurrentPadding(&padding))) continue;
            available = impl.bufferFrames - padding;
        }
        if (available == 0) continue;

        if (FAILED(impl.render->GetBuffer(available, &data)) || data == nullptr) {
            impl.xruns.fetch_add(1, std::memory_order_relaxed);
            continue;
        }

        const auto started = std::chrono::steady_clock::now();

        float* mix = impl.mixScratch.data();
        std::memset(mix, 0, sizeof(float) * available * impl.channels);
        if (impl.callback) impl.callback(mix, available, impl.channels);

        if (impl.floatFormat) {
            std::memcpy(data, mix, sizeof(float) * available * impl.channels);
        } else {
            // 16-bit exclusive fallback. Clamped rather than wrapped: a
            // sample over full scale should sound like clipping, not like
            // the loudest possible noise in the opposite polarity.
            auto* pcm = reinterpret_cast<std::int16_t*>(data);
            const std::size_t total = static_cast<std::size_t>(available) * impl.channels;
            for (std::size_t i = 0; i < total; ++i) {
                const float clamped = (std::max)(-1.0f, (std::min)(1.0f, mix[i]));
                pcm[i] = static_cast<std::int16_t>(clamped * 32767.0f);
            }
        }

        impl.render->ReleaseBuffer(available, 0);
        impl.framesRendered.fetch_add(available, std::memory_order_relaxed);

        const double spent =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        const double load = budgetSeconds > 0 ? spent / budgetSeconds : 0.0;
        impl.loadRecent.store(load, std::memory_order_relaxed);
        if (load > impl.loadPeak.load(std::memory_order_relaxed)) {
            impl.loadPeak.store(load, std::memory_order_relaxed);
        }
        // Over budget means the next buffer was already due. Counting it
        // here catches the near misses that never become audible glitches
        // but say the project is at the edge of what the buffer size allows.
        if (load >= 1.0) impl.xruns.fetch_add(1, std::memory_order_relaxed);
    }

    impl.client->Stop();
    if (task != nullptr) AvRevertMmThreadCharacteristics(task);
    CoUninitialize();
}

void AudioDevice::close() {
    auto& impl = *impl_;
    impl.stop.store(true, std::memory_order_release);
    if (impl.event != nullptr) SetEvent(impl.event);
    if (impl.thread.joinable()) impl.thread.join();
    running_.store(false, std::memory_order_release);

    release(impl.render);
    release(impl.client);
    release(impl.device);
    release(impl.enumerator);
    if (impl.format != nullptr) {
        CoTaskMemFree(impl.format);
        impl.format = nullptr;
    }
    if (impl.event != nullptr) {
        CloseHandle(impl.event);
        impl.event = nullptr;
    }
    impl.callback = nullptr;
}

DeviceStatus AudioDevice::status() const {
    const auto& impl = *impl_;
    DeviceStatus s;
    s.open = running_.load(std::memory_order_acquire);
    s.exclusive = impl.exclusive;
    s.deviceName = impl.name;
    s.sampleRate = impl.sampleRate;
    s.bufferFrames = impl.bufferFrames;
    s.channels = impl.channels;
    s.outputLatencyMs =
        impl.sampleRate > 0 ? (1000.0 * impl.bufferFrames) / impl.sampleRate : 0.0;
    s.framesRendered = impl.framesRendered.load(std::memory_order_relaxed);
    s.xruns = impl.xruns.load(std::memory_order_relaxed);
    s.loadPeak = impl.loadPeak.load(std::memory_order_relaxed);
    s.loadRecent = impl.loadRecent.load(std::memory_order_relaxed);
    return s;
}

}  // namespace cashout
