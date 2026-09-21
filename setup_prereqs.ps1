#Requires -Version 5.1
<#
Cashout Studio Prerequisite Installer - Configured for AMD GPUs on Windows 10/11.
Installs essential background tools while omitting the NVIDIA CUDA Toolkit
payload, and adds the Vulkan SDK needed to compile audio.cpp's Vulkan
compute backend (works on any GPU vendor, including AMD, on Windows 10).
#>

$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget was not found. Install 'App Installer' from the Microsoft Store first." -ForegroundColor Red
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as Administrator - Build Tools might require an elevated console prompt." -ForegroundColor Yellow
}

function Install-Winget($id, $overrideArgs) {
    Write-Host ""
    Write-Host "== $id ==" -ForegroundColor Cyan
    $installed = winget list --id $id --accept-source-agreements 2>$null | Select-String -SimpleMatch $id
    if ($installed) {
        Write-Host "Already installed, skipping."
        return
    }
    $wingetArgs = @("install", "--id", $id, "--silent", "--accept-package-agreements", "--accept-source-agreements")
    if ($overrideArgs) { $wingetArgs += @("--override", $overrideArgs) }
    winget @wingetArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "winget could not install $id automatically - please verify manually." -ForegroundColor Yellow
    }
}

Write-Host "=== Installing Foundation Environment ===" -ForegroundColor Green
Install-Winget "Git.Git"
Install-Winget "Python.Python.3.12"
Install-Winget "astral-sh.uv"
Install-Winget "OpenJS.NodeJS.LTS"
Install-Winget "Kitware.CMake"
Install-Winget "Gyan.FFmpeg"

Write-Host ""
Write-Host "=== Installing Build Engines (AMD Native Compilation Target) ===" -ForegroundColor Green
Install-Winget "Microsoft.VisualStudio.2022.BuildTools" "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
# audio.cpp's Vulkan preset (windows-vulkan-release) shells out to glslc.exe
# at build time to compile its compute shaders - needs the SDK, not just a
# GPU driver. The Vulkan *runtime* loader (vulkan-1.dll) that audiocpp_server
# needs afterward already ships with any GPU driver, so nothing extra is
# needed for that part.
Install-Winget "KhronosGroup.VulkanSDK"

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "1. CLOSE this current terminal window completely." -ForegroundColor Cyan
Write-Host "2. Open a BRAND NEW PowerShell window to load the fresh environment paths." -ForegroundColor Cyan
Write-Host "3. Run 'cd remiqora' and kick off '.\setup_models.bat' to map the audio engines." -ForegroundColor Cyan
