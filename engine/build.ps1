# Builds the native audio engine.
#
# Generates a real Visual Studio solution rather than driving the compiler
# directly, because the point of this being C++ is that you can open it in
# Visual Studio, set a breakpoint in the render callback and step through a
# block. `engine\build\cashout_engine.sln` is that solution.
#
#   .\build.ps1              full build, VST3 hosting on
#   .\build.ps1 -NoVst3      core only - about twenty seconds instead of minutes
#   .\build.ps1 -Open        build, then open the solution
[CmdletBinding()]
param(
    [switch]$NoVst3,
    [switch]$Open,
    [ValidateSet('Release', 'Debug', 'RelWithDebInfo')]
    [string]$Config = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Separate build directories per configuration of the VST3 switch. Toggling
# the option in one directory means CMake reconfigures and MSBuild rebuilds
# the whole SDK, which is minutes you do not need to spend.
$buildDir = if ($NoVst3) { Join-Path $root 'build-core' } else { Join-Path $root 'build' }
$vst3 = if ($NoVst3) { 'OFF' } else { 'ON' }

if (-not $NoVst3) {
    $sdk = Join-Path (Split-Path -Parent $root) 'external\vst3sdk\CMakeLists.txt'
    if (-not (Test-Path $sdk)) {
        Write-Host "The VST3 SDK is not at external\vst3sdk." -ForegroundColor Yellow
        Write-Host "Either clone steinbergmedia/vst3sdk there, or build without plugin hosting:" -ForegroundColor Yellow
        Write-Host "    .\build.ps1 -NoVst3" -ForegroundColor Yellow
        exit 1
    }
}

$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $cmake) {
    Write-Host "cmake is not on PATH. Install it, or use the one Visual Studio ships:" -ForegroundColor Yellow
    Write-Host '    $env:PATH += ";C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"'
    exit 1
}

Write-Host "Configuring ($Config, VST3=$vst3)..." -ForegroundColor Cyan
cmake -S $root -B $buildDir -G "Visual Studio 17 2022" -A x64 "-DCASHOUT_ENGINE_VST3=$vst3"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building..." -ForegroundColor Cyan
cmake --build $buildDir --config $Config -- /m
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exe = Join-Path $buildDir "$Config\cashout_engine.exe"
if (Test-Path $exe) {
    Write-Host "`nBuilt $exe" -ForegroundColor Green
    Write-Host "The studio finds it there automatically - the Audio engine panel in the DAW will pick it up."
} else {
    Write-Host "Build reported success but produced no executable." -ForegroundColor Red
    exit 1
}

if ($Open) {
    $sln = Join-Path $buildDir 'cashout_engine.sln'
    Write-Host "Opening $sln" -ForegroundColor Cyan
    Start-Process $sln
}
