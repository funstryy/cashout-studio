<#
.SYNOPSIS
    Creates the GitHub repository, pushes it, and cuts the 1.0.0 release.

.DESCRIPTION
    Everything here needs an authenticated GitHub CLI, which is the one
    step that cannot be scripted: `gh auth login` opens a browser and
    waits for you to approve it. Run that once, then run this.

    The repository is created PRIVATE on purpose. Look at it on the web,
    check the README renders and the screenshots load, then make it
    public with the command printed at the end. Creating it public and
    fixing it afterwards means the first version is the one archived by
    everything that scrapes new repositories.

.PARAMETER Name
    Repository name. Default: cashout-studio

.PARAMETER Owner
    Account or organisation. Defaults to whoever gh is logged in as.

.EXAMPLE
    gh auth login
    .\scripts\publish_github.ps1
#>
param(
    [string]$Name = "cashout-studio",
    [string]$Owner = "",
    [string]$Tag = "v1.0.0",
    # Where build.py leaves the installer. Defaults to a sibling of the
    # repository, which is where it lives, rather than an absolute path
    # that stops being true the moment either folder is renamed.
    [string]$SetupOut = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) { $gh = (Get-Command gh -ErrorAction SilentlyContinue).Source }
if (-not $gh) { throw "GitHub CLI not found. Install it with: winget install GitHub.cli" }

# ---------------------------------------------------------------- checks
& $gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in. Run: gh auth login" }

if (-not $Owner) { $Owner = (& $gh api user --jq .login) }
$slug = "$Owner/$Name"
Write-Host "Publishing to $slug" -ForegroundColor Cyan

if ((git status --porcelain).Length -gt 0) {
    throw "The working tree has uncommitted changes. Commit or stash them first."
}

if (-not $SetupOut) {
    $SetupOut = Join-Path (Split-Path -Parent $repoRoot) "Cashout Studio Setup\out"
}
$setupDir = $SetupOut
if (-not (Test-Path $setupDir)) {
    throw "Installer output not found: $setupDir`nPass -SetupOut <path> if it is elsewhere."
}
$assets = @(
    "$setupDir\Cashout Studio 1.0.0.rar",
    "$setupDir\CashoutStudio-Setup-1.0.0.exe",
    "$setupDir\CashoutStudio-Setup-1.0.0.exe.sha256"
)
foreach ($a in $assets) {
    if (-not (Test-Path $a)) { throw "Release asset missing: $a" }
}

# ------------------------------------------ the placeholder in SECURITY.md
# Written before the repository existed, so it names one that may not.
$sec = Get-Content SECURITY.md -Raw
$correct = "https://github.com/$slug/security/advisories/new"
if ($sec -notmatch [regex]::Escape($correct)) {
    $sec = $sec -replace 'https://github\.com/[^/]+/[^/]+/security/advisories/new', $correct
    Set-Content SECURITY.md -Value $sec -NoNewline -Encoding utf8
    git add SECURITY.md
    git commit -q -m "docs: point the security advisory link at this repository"
    Write-Host "  SECURITY.md advisory link corrected" -ForegroundColor DarkGray
}

# ------------------------------------------------------------ the repo
$exists = $true
& $gh repo view $slug 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { $exists = $false }

if (-not $exists) {
    Write-Host "Creating $slug (private)" -ForegroundColor Cyan
    & $gh repo create $slug --private `
        --description "A complete local music production studio: a multitrack DAW with a native C++ audio engine, and AI that runs on your own graphics card." `
        --homepage "https://github.com/$slug"
    if ($LASTEXITCODE -ne 0) { throw "gh repo create failed" }
} else {
    Write-Host "$slug already exists, reusing it" -ForegroundColor Yellow
}

if (-not (git remote | Select-String -Quiet '^origin$')) {
    git remote add origin "https://github.com/$slug.git"
} else {
    git remote set-url origin "https://github.com/$slug.git"
}

Write-Host "Pushing master and tags" -ForegroundColor Cyan
git push -u origin master
git push origin --tags

# --------------------------------------------------------------- topics
& $gh repo edit $slug `
    --add-topic daw --add-topic music-production --add-topic audio `
    --add-topic vst3 --add-topic wasapi --add-topic cpp `
    --add-topic ai-music --add-topic stem-separation `
    --add-topic mastering --add-topic vue3 --add-topic fastapi `
    --add-topic windows 2>&1 | Out-Null

# -------------------------------------------------------------- release
& $gh release view $Tag --repo $slug 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Release $Tag already exists, uploading assets over it" -ForegroundColor Yellow
    & $gh release upload $Tag @assets --repo $slug --clobber
} else {
    Write-Host "Creating release $Tag with 3 assets (about 384 MB total)" -ForegroundColor Cyan
    & $gh release create $Tag @assets `
        --repo $slug `
        --title "Cashout Studio 1.0.0" `
        --notes-file docs/RELEASE-1.0.0.md
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Repository: https://github.com/$slug"
Write-Host "  Release:    https://github.com/$slug/releases/tag/$Tag"
Write-Host ""
Write-Host "It is PRIVATE. Check the README renders and the screenshots load, then:" -ForegroundColor Yellow
Write-Host "  gh repo edit $slug --visibility public --accept-visibility-change-consequences"
