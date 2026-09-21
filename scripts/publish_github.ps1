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

.PARAMETER SetupOut
    Where build.py leaves the installer. Defaults to a sibling folder.

.EXAMPLE
    gh auth login
    .\scripts\publish_github.ps1
#>
param(
    [string]$Name = "cashout-studio",
    [string]$Owner = "",
    [string]$Tag = "v1.0.0",
    [string]$SetupOut = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) { $gh = (Get-Command gh -ErrorAction SilentlyContinue).Source }
if (-not $gh) { throw "GitHub CLI not found. Install it with: winget install GitHub.cli" }

<#
Runs gh (or any native tool) and hands back its exit code.

Windows PowerShell turns a native program's stderr into error records the
moment it is redirected, and with $ErrorActionPreference = 'Stop' those
records are fatal. That made "does this repository exist yet?" crash the
script with the answer, because `gh repo view` reports "not found" on
stderr and exits non-zero, which is exactly the reply being asked for.

So: no redirection, and the preference is relaxed for the duration of the
call. Output is captured rather than printed, and only shown when the
caller wants it. The exit code is the answer.
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Show
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($Show -and $output) { $output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    return [pscustomobject]@{ Code = $code; Output = ($output -join "`n") }
}

function Assert-Native {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $r = Invoke-Native -Exe $Exe -Arguments $Arguments -Show
    if ($r.Code -ne 0) { throw "$What failed (exit $($r.Code))`n$($r.Output)" }
    return $r
}

# ---------------------------------------------------------------- checks
if ((Invoke-Native -Exe $gh -Arguments @("auth", "status")).Code -ne 0) {
    throw "Not logged in. Run: gh auth login"
}

if (-not $Owner) {
    $who = Assert-Native -Exe $gh -Arguments @("api", "user", "--jq", ".login") -What "gh api user"
    $Owner = $who.Output.Trim()
}
$slug = "$Owner/$Name"
Write-Host "Publishing to $slug" -ForegroundColor Cyan

if ((git status --porcelain).Length -gt 0) {
    throw "The working tree has uncommitted changes. Commit or stash them first."
}

if (-not $SetupOut) {
    $SetupOut = Join-Path (Split-Path -Parent $repoRoot) "Cashout Studio Setup\out"
}
if (-not (Test-Path $SetupOut)) {
    throw "Installer output not found: $SetupOut`nPass -SetupOut <path> if it is elsewhere."
}

$assets = @(
    (Join-Path $SetupOut "Cashout Studio 1.0.0.rar"),
    (Join-Path $SetupOut "CashoutStudio-Setup-1.0.0.exe"),
    (Join-Path $SetupOut "CashoutStudio-Setup-1.0.0.exe.sha256")
)
foreach ($a in $assets) {
    if (-not (Test-Path $a)) { throw "Release asset missing: $a" }
}

# ------------------------------------------ the placeholder in SECURITY.md
# Written before the repository existed, so it names one that may not.
$sec = Get-Content SECURITY.md -Raw
$correct = "https://github.com/$slug/security/advisories/new"
if ($sec -notmatch [regex]::Escape($correct)) {
    $sec = $sec -replace 'https://github\.com/[^/\s]+/[^/\s]+/security/advisories/new', $correct
    Set-Content SECURITY.md -Value $sec -NoNewline -Encoding utf8
    git add SECURITY.md
    git commit -q -m "docs: point the security advisory link at this repository"
    git tag -f $Tag -m "Cashout Studio 1.0.0" | Out-Null
    Write-Host "  SECURITY.md advisory link corrected" -ForegroundColor DarkGray
}

# -------------------------------------------------------------- the repo
$exists = (Invoke-Native -Exe $gh -Arguments @("repo", "view", $slug)).Code -eq 0

if (-not $exists) {
    Write-Host "Creating $slug (private)" -ForegroundColor Cyan
    Assert-Native -Exe $gh -Arguments @(
        "repo", "create", $slug, "--private",
        "--description", "A complete local music production studio: a multitrack DAW with a native C++ audio engine, and AI that runs on your own graphics card."
    ) -What "gh repo create" | Out-Null
} else {
    Write-Host "$slug already exists, reusing it" -ForegroundColor Yellow
}

if (git remote | Select-String -Quiet '^origin$') {
    git remote set-url origin "https://github.com/$slug.git"
} else {
    git remote add origin "https://github.com/$slug.git"
}

Write-Host "Pushing master and tags" -ForegroundColor Cyan
Assert-Native -Exe "git" -Arguments @("push", "-u", "origin", "master") -What "git push" | Out-Null
Assert-Native -Exe "git" -Arguments @("push", "--force", "origin", $Tag) -What "git push tag" | Out-Null

# ------------------------------------------------------------- polish
Invoke-Native -Exe $gh -Arguments @(
    "repo", "edit", $slug,
    "--add-topic", "daw", "--add-topic", "music-production", "--add-topic", "audio",
    "--add-topic", "vst3", "--add-topic", "wasapi", "--add-topic", "cpp",
    "--add-topic", "ai-music", "--add-topic", "stem-separation",
    "--add-topic", "mastering", "--add-topic", "vue", "--add-topic", "fastapi",
    "--add-topic", "windows"
) | Out-Null

# ------------------------------------------------------------- release
$hasRelease = (Invoke-Native -Exe $gh -Arguments @("release", "view", $Tag, "--repo", $slug)).Code -eq 0

if ($hasRelease) {
    Write-Host "Release $Tag already exists, replacing its assets" -ForegroundColor Yellow
    Assert-Native -Exe $gh -Arguments (@("release", "upload", $Tag) + $assets + @("--repo", $slug, "--clobber")) `
        -What "gh release upload" | Out-Null
} else {
    Write-Host "Creating release $Tag with 3 assets, about 384 MB. This takes a few minutes." -ForegroundColor Cyan
    Assert-Native -Exe $gh -Arguments (@("release", "create", $Tag) + $assets + @(
        "--repo", $slug,
        "--title", "Cashout Studio 1.0.0",
        "--notes-file", "docs/RELEASE-1.0.0.md"
    )) -What "gh release create" | Out-Null
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Repository: https://github.com/$slug"
Write-Host "  Release:    https://github.com/$slug/releases/tag/$Tag"
Write-Host ""
Write-Host "It is PRIVATE. Check the README renders and the screenshots load, then:" -ForegroundColor Yellow
Write-Host "  gh repo edit $slug --visibility public --accept-visibility-change-consequences"
