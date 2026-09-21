#include "editor_window.h"

#include "pluginterfaces/gui/iplugview.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <cstdint>

using namespace Steinberg;

namespace {

const wchar_t* kWindowClass = L"CashoutStudioPluginEditor";

// Sent to ourselves when a plugin has asked to be resized. It exists so the
// answer never runs inside the plugin's own call - see HostFrame.
const UINT kApplyViewSize = WM_APP + 1;

struct Session {
    IPlugView* view = nullptr;
    ViewRect pending{};
};

std::wstring widen(const std::string& text) {
    if (text.empty()) return L"";
    const int size = MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, nullptr, 0);
    std::wstring out(size > 0 ? size - 1 : 0, L'\0');
    if (size > 0) MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, out.data(), size);
    return out;
}

void resizeWindowTo(HWND window, const ViewRect& size) {
    RECT frame{0, 0, size.getWidth(), size.getHeight()};
    AdjustWindowRectEx(&frame, GetWindowLong(window, GWL_STYLE), FALSE,
                       GetWindowLong(window, GWL_EXSTYLE));
    SetWindowPos(window, nullptr, 0, 0, frame.right - frame.left, frame.bottom - frame.top,
                 SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
}

/** The host half of the resize protocol.
 *
 *  Plugins whose interface can change shape - a collapsible panel, a resizable
 *  browser - ask the host to grow the window rather than doing it themselves.
 *
 *  The acknowledgement is deliberately deferred. A plugin may ask for a resize
 *  from inside attached(), while it is still building its interface, and
 *  calling straight back into onSize() at that moment re-enters a plugin that
 *  is not ready for it - Serum faults on exactly that. So the window is
 *  resized now, which is the host's own business, and the reply is posted, to
 *  be delivered once the plugin has returned and the message loop is running.
 *
 *  Reference counting is inert: the frame lives on the stack of the function
 *  that runs the window, so it outlives every view that can hold a pointer. */
class HostFrame : public IPlugFrame {
public:
    HWND window = nullptr;
    Session* session = nullptr;

    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* newSize) override {
        if (!window || !view || !newSize) return kInvalidArgument;
        resizeWindowTo(window, *newSize);
        if (session) {
            session->pending = *newSize;
            PostMessageW(window, kApplyViewSize, 0, 0);
        }
        return kResultTrue;
    }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IPlugFrame)
        QUERY_INTERFACE(iid, obj, IPlugFrame::iid, IPlugFrame)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }
};

LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    auto* session = reinterpret_cast<Session*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    switch (message) {
        case kApplyViewSize:
            if (session && session->view) session->view->onSize(&session->pending);
            return 0;
        case WM_CLOSE:
            DestroyWindow(window);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProcW(window, message, wParam, lParam);
    }
}

bool registerWindowClass() {
    static bool registered = false;
    if (registered) return true;
    WNDCLASSEXW cls{};
    cls.cbSize = sizeof(cls);
    cls.style = CS_HREDRAW | CS_VREDRAW;
    cls.lpfnWndProc = windowProc;
    cls.hInstance = GetModuleHandleW(nullptr);
    cls.hCursor = LoadCursor(nullptr, IDC_ARROW);
    cls.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    cls.lpszClassName = kWindowClass;
    registered = RegisterClassExW(&cls) != 0;
    return registered;
}

/** Names the plugin when it takes the process down.
 *
 *  Hosting third-party machine code means some of it will fault, and a host
 *  process that simply vanishes tells the studio nothing worth showing the
 *  user. This turns that into one line naming the module at fault. */
LONG CALLBACK crashReporter(EXCEPTION_POINTERS* info) {
    const DWORD code = info->ExceptionRecord->ExceptionCode;
    if (code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_ILLEGAL_INSTRUCTION ||
        code == EXCEPTION_STACK_OVERFLOW || code == EXCEPTION_IN_PAGE_ERROR) {
        void* address = info->ExceptionRecord->ExceptionAddress;
        HMODULE module = nullptr;
        char name[MAX_PATH] = "?";
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                               GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCSTR>(address), &module);
        if (module) GetModuleFileNameA(module, name, MAX_PATH);
        std::fprintf(stderr, "[crash] code=0x%08lX at %p in %s\n", code, address, name);

        // The handler runs on the faulting stack, so walking back from here
        // walks back through whatever the plugin was doing. Module and offset
        // is as far as it goes - third-party plugins ship no symbols - but it
        // does say which library called which, which is usually the answer.
        void* frames[32];
        const USHORT captured = CaptureStackBackTrace(0, 32, frames, nullptr);
        for (USHORT i = 0; i < captured; ++i) {
            HMODULE owner = nullptr;
            char path[MAX_PATH] = "?";
            GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                   GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               reinterpret_cast<LPCSTR>(frames[i]), &owner);
            if (owner) GetModuleFileNameA(owner, path, MAX_PATH);
            const char* leaf = std::strrchr(path, '\\');
            std::fprintf(stderr, "[crash]   #%02u %s+0x%llX\n", i, leaf ? leaf + 1 : path,
                         static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(frames[i]) -
                                                        reinterpret_cast<uintptr_t>(owner)));
        }
        std::fflush(stderr);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

}  // namespace

bool runPluginEditor(Vst::IEditController* controller, const std::string& title, std::string& error) {
    if (!controller) { error = "plugin has no edit controller"; return false; }

    AddVectoredExceptionHandler(1, crashReporter);
    // Plugins report their editor size in physical pixels on Windows. Without
    // this the window is sized in scaled units on a high-DPI display and the
    // interface is clipped.
    SetProcessDPIAware();

    IPtr<IPlugView> view = owned(controller->createView(Vst::ViewType::kEditor));
    if (!view) { error = "plugin has no editor window"; return false; }
    if (view->isPlatformTypeSupported(kPlatformTypeHWND) != kResultTrue) {
        error = "plugin editor does not support Windows windows";
        return false;
    }
    if (!registerWindowClass()) { error = "could not register the editor window class"; return false; }

    ViewRect size{};
    view->getSize(&size);
    if (size.getWidth() <= 0 || size.getHeight() <= 0) { size.right = 800; size.bottom = 600; }

    const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT frame{0, 0, size.getWidth(), size.getHeight()};
    AdjustWindowRectEx(&frame, style, FALSE, 0);

    Session session;
    session.view = view;

    HWND window = CreateWindowExW(
        0, kWindowClass, widen(title).c_str(), style,
        CW_USEDEFAULT, CW_USEDEFAULT, frame.right - frame.left, frame.bottom - frame.top,
        nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
    if (!window) { error = "could not create the editor window"; return false; }
    SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(&session));

    HostFrame hostFrame;
    hostFrame.window = window;
    hostFrame.session = &session;

    // Shown before attaching: a plugin that builds a swap chain or a layered
    // surface does it inside attached(), against a parent that has to be real
    // by then.
    ShowWindow(window, SW_SHOW);
    UpdateWindow(window);

    // setFrame before attached: a plugin is allowed to ask for a different
    // size during attach, and with no frame yet that request is simply lost.
    view->setFrame(&hostFrame);
    if (view->attached(window, kPlatformTypeHWND) != kResultOk) {
        DestroyWindow(window);
        error = "plugin refused to attach its editor";
        return false;
    }

    // Whatever size it settled on while attaching.
    if (view->getSize(&size) == kResultOk && size.getWidth() > 0) resizeWindowTo(window, size);

    SetForegroundWindow(window);

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    view->removed();
    view->setFrame(nullptr);
    return true;
}
