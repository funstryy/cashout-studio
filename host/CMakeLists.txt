cmake_minimum_required(VERSION 3.20)
project(cashout_vst_host CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The SDK builds a lot by default - example plugins, hosting demos and the
# whole VSTGUI stack. None of that is wanted here: this is a headless host,
# and every extra target is build time and another way for the build to fail.
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)
set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)

set(VST3_SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../external/vst3sdk" CACHE PATH "VST3 SDK root")
if(NOT EXISTS "${VST3_SDK_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "VST3 SDK not found at ${VST3_SDK_DIR}. Clone steinbergmedia/vst3sdk there.")
endif()

add_subdirectory("${VST3_SDK_DIR}" vst3sdk EXCLUDE_FROM_ALL)

# module_win32.cpp is the platform half of Module::create - it is not part of
# sdk_hosting, so a host that only links that target fails at link time with
# an unresolved Module::create.
add_executable(cashout_vst_host
    main.cpp
    "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/module_win32.cpp"
    # Needed to hand the component's state to a separately-created edit
    # controller, which is what makes a plugin report its parameters.
    "${VST3_SDK_DIR}/public.sdk/source/common/memorystream.cpp"
    "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/plugprovider.cpp"
    # Parameter values only reach a plugin's processor through these queues,
    # so an editing session's settings are replayed into the render with them.
    "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/parameterchanges.cpp"
    # The plugin's own interface, in a real window.
    editor_window.cpp
)
target_link_libraries(cashout_vst_host PRIVATE sdk_hosting base pluginterfaces shlwapi)
target_include_directories(cashout_vst_host PRIVATE "${VST3_SDK_DIR}")

if(MSVC)
    target_compile_options(cashout_vst_host PRIVATE /W3 /EHsc)
    # The SDK's own headers trip these; they are not our warnings to fix.
    target_compile_definitions(cashout_vst_host PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
endif()
