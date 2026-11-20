<#
    Installs what the AI analyst needs, for the installer's "Install Claude Code" option:
    Git for Windows (Claude Code on Windows runs its tools through Git Bash) and the native
    Claude Code. Exit code 0 when claude.exe is in place afterwards.
#>
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$HasGit = (Get-Command git -ErrorAction SilentlyContinue) -or
    (Test-Path "$env:ProgramFiles\Git\bin\bash.exe") -or
    (Test-Path "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe")
if ($HasGit) {
    Write-Host 'Git for Windows: already installed'
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host 'Installing Git for Windows with winget ...'
    $WingetArgs = @('install', '--id', 'Git.Git', '-e', '--source', 'winget', '--silent',
        '--accept-package-agreements', '--accept-source-agreements')
    & winget @WingetArgs --scope user
    if ($LASTEXITCODE -ne 0) { & winget @WingetArgs }  # no per-user package: machine-wide
} else {
    Write-Warning 'winget is missing: install Git for Windows from https://git-scm.com/download/win'
}

Write-Host 'Installing Claude Code ...'
Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression

if (Test-Path "$env:USERPROFILE\.local\bin\claude.exe") {
    Write-Host 'Claude Code installed.'
    exit 0
}
Write-Warning 'claude.exe was not found after the install.'
exit 1
