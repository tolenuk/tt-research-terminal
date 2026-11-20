<#
.SYNOPSIS
    Install, update or remove TT Research Terminal for the current Windows user.

.DESCRIPTION
    Run from the unpacked source folder (see packaging/windows/README.md):

        powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
        powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1 -Uninstall

    Installs uv if it is missing, installs the app as a uv tool on Python 3.12, adds an
    "TT Research Terminal" Start Menu shortcut (pythonw, so no console window) and checks that the
    native Claude Code is available for the AI analyst. Nothing needs administrator rights.
#>
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Task = 'OBB Terminal Alerts'  # alerts/schtasks.py TASK
$Shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'TT Research Terminal.lnk'
$OldShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'OBB Terminal.lnk'  # before the rename
$DataDir = Join-Path $env:LOCALAPPDATA 'obb-terminal'

function Test-Command($Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-AlertsTask {
    # Windows PowerShell turns a native command's stderr into an error, fatal under 'Stop',
    # and schtasks writes "cannot find the file" there when the task does not exist.
    $ErrorActionPreference = 'Continue'
    & schtasks.exe /Query /TN $Task 2>&1 | Out-Null
    $LASTEXITCODE -eq 0
}

function Remove-AlertsTask {
    if (Test-AlertsTask) {
        & schtasks.exe /Delete /TN $Task /F | Out-Null
        Write-Host "Removed the background-alerts task."
    }
}

if ($Uninstall) {
    Remove-AlertsTask
    Remove-Item $Shortcut -ErrorAction SilentlyContinue
    Remove-Item $OldShortcut -ErrorAction SilentlyContinue
    if (Test-Command 'uv') { & uv tool uninstall obb-terminal }
    Write-Host "TT Research Terminal removed. Your watchlist, journal and reports are kept in $DataDir."
    return
}

if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    throw "Run this script from inside the TT Research Terminal source folder (no pyproject.toml in $Root)."
}

if (-not (Test-Command 'uv')) {
    Write-Host 'Installing uv (Python package manager) from astral.sh ...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Test-Command 'uv')) {
        throw 'uv was installed but is not on PATH yet. Open a new PowerShell window and run this script again.'
    }
}

Write-Host "Installing TT Research Terminal from $Root (first run downloads Python 3.12 and the libraries) ..."
& uv tool install --python 3.12 --reinstall-package obb-terminal $Root
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed (exit code $LASTEXITCODE)." }

$ToolDir = (& uv tool dir).Trim()
$Pythonw = Join-Path $ToolDir 'obb-terminal\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "Expected $Pythonw after install, but it is missing." }

$Shell = New-Object -ComObject WScript.Shell
Remove-Item $OldShortcut -ErrorAction SilentlyContinue
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Pythonw
$Link.Arguments = '-m obb_terminal'
$Link.IconLocation = Join-Path $ToolDir 'obb-terminal\Lib\site-packages\obb_terminal\ui\assets\tt-research.ico'
$Link.WorkingDirectory = $env:USERPROFILE
$Link.Description = 'Bloomberg-style research terminal powered by OpenBB'
$Link.Save()
Write-Host "Start Menu shortcut: $Shortcut"

# An existing alerts task points at the venv's pythonw; re-register it in case the path moved.
if (Test-AlertsTask) {
    & (Join-Path $ToolDir 'obb-terminal\Scripts\obb-terminal-alerts.exe') --install
}

$Claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $Claude) {
    Write-Warning ("Claude Code not found, so the AI analyst will be unavailable. Install it with " +
        "'irm https://claude.ai/install.ps1 | iex', then run 'claude' once to log in.")
} elseif ($Claude.Source -match '\.(cmd|bat|ps1)$') {
    Write-Warning ("Claude Code here is the npm version ($($Claude.Source)); the app needs the native " +
        "claude.exe. Install it with 'irm https://claude.ai/install.ps1 | iex'.")
} else {
    Write-Host "Claude Code: $($Claude.Source) (run 'claude' once to log in if you have not)."
}

Write-Host ''
Write-Host 'Done. Open "TT Research Terminal" from the Start Menu.'
