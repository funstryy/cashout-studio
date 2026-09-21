cmake_minimum_required(VERSION 3.20)
project(cashout_engine CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# VST3 hosting doubles the build time, because it drags in the Steinberg SDK
# and that is not a small library. The engine's core - device, mixer,
# transport, capture - does not need it, so it stays a switch: you can get a
# working engine in twenty seconds while iterating, and turn plugins back on
# when you need them.
option(CASHOUT_ENGINE_VST3 "Host VST3 plugins live in the signal path" ON)

add_executable(cashout_engine
    src/main.cpp
    src/device.cpp
    src/mixer.cpp
    src/wav.cpp
    src/dsp.cpp
    src/mastering.cpp
    src/json.cpp
)

target_include_directories(cashout_engine PRIVATE src)

# avrt is MMCSS - the Pro Audio scheduling class. Without it the render
# thread is an ordinary thread and Windows will deschedule it under UI load,
# which is exactly when a DAW must not glitch.
target_link_libraries(cashout_engine PRIVATE avrt ole32 ws2_32)

if(MSVC)
    target_compile_options(cashout_engine PRIVATE /W4 /EHsc /fp:fast)
    target_compile_definitions(cashout_engine PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN)
    # Release builds get the vectoriser; the mixer's inner loops are exactly
    # the shape it handles well.
    target_compile_options(cashout_engine PRIVATE $<$<CONFIG:Release>:/O2>)
endif()

if(CASHOUT_ENGINE_VST3)
    set(VST3_SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../external/vst3sdk" CACHE PATH "VST3 SDK root")
    if(NOT EXISTS "${VST3_SDK_DIR}/CMakeLists.txt")
        message(FATAL_ERROR "VST3 SDK not found at ${VST3_SDK_DIR}. Clone steinbergmedia/vst3sdk there, or configure with -DCASHOUT_ENGINE_VST3=OFF.")
    endif()

    # Same trimming as the standalone host: none of the SDK's examples,
    # validators or GUI stack are wanted, and every one of them is build
    # time plus another way for the build to break.
    set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
    set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)
    set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)

    add_subdirectory("${VST3_SDK_DIR}" vst3sdk EXCLUDE_FROM_ALL)

    target_sources(cashout_engine PRIVATE
        src/vst_insert.cpp
        # module_win32.cpp is the platform half of Module::create and is not
        # part of sdk_hosting; without it the link fails on an unresolved
        # Module::create.
        "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/module_win32.cpp"
        "${VST3_SDK_DIR}/public.sdk/source/common/memorystream.cpp"
        "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/plugprovider.cpp"
        "${VST3_SDK_DIR}/public.sdk/source/vst/hosting/parameterchanges.cpp"
    )
    target_include_directories(cashout_engine PRIVATE "${VST3_SDK_DIR}")
    target_link_libraries(cashout_engine PRIVATE sdk_hosting base pluginterfaces shlwapi)
    target_compile_definitions(cashout_engine PRIVATE CASHOUT_ENGINE_VST3=1)
else()
    target_compile_definitions(cashout_engine PRIVATE CASHOUT_ENGINE_VST3=0)
endif()
