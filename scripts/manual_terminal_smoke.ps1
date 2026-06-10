param(
    [string]$FilePath = "",
    [switch]$UseInstalledCommand
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$manualRoot = Join-Path $repoRoot ".manual-terminal-smoke"
$homeDir = Join-Path $manualRoot "home"
$docsDir = Join-Path $manualRoot "Documents"

New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
New-Item -ItemType Directory -Force -Path $docsDir | Out-Null

if (-not $FilePath) {
    Write-Host "No sample file path provided. Launching without a preloaded document." -ForegroundColor Yellow
    Write-Host "Use /load inside Study TUI or pass -FilePath to this script." -ForegroundColor Yellow
}

$launchCommand = if ($UseInstalledCommand) {
    if ($FilePath) { "study --file `"$FilePath`"" } else { "study" }
} else {
    if ($FilePath) { "python -m src --file `"$FilePath`"" } else { "python -m src" }
}

$bootstrap = @"
`$env:HOME = '$homeDir'
`$env:USERPROFILE = '$homeDir'
`$env:STUDY_DOCS_DIR = '$docsDir'
Set-Location '$repoRoot'
Write-Host 'Launching Study TUI manual smoke session.' -ForegroundColor Cyan
$launchCommand
"@

Start-Process pwsh -ArgumentList @("-NoExit", "-Command", $bootstrap)
