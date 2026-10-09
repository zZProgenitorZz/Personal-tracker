<#
.SYNOPSIS
    Zet een snelkoppeling "Progen" op je bureaublad (en eventueel in het Startmenu).

.DESCRIPTION
    De snelkoppeling start launch.pyw met pythonw.exe uit .venv: geen consolevenster,
    de server start als dat nodig is, en Progen opent als eigen venster.
    Eén keer uitvoeren is genoeg; opnieuw uitvoeren werkt de snelkoppelingen bij.

.PARAMETER StartMenu
    Zet "Progen" en "Stop Progen" ook in het Startmenu.

.PARAMETER StopOnDesktop
    Zet "Stop Progen" ook op het bureaublad.

.PARAMETER Folder
    Andere map dan het bureaublad (vooral om te testen).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1 -StartMenu
#>
param(
    [switch]$StartMenu,
    [switch]$StopOnDesktop,
    [string]$Folder = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "app\static\icon.ico"

if (-not (Test-Path $pythonw)) {
    Write-Error "Geen $pythonw gevonden. Maak eerst de venv aan (zie README, 'Snel starten')."
}
if (-not (Test-Path $icon)) {
    Write-Error "Geen $icon gevonden. Maak hem met: .venv\Scripts\python.exe scripts\make_icon.py"
}

$shell = New-Object -ComObject WScript.Shell

function New-ProgenShortcut([string]$Directory, [string]$Name, [string]$Script, [string]$Description) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $path = Join-Path $Directory "$Name.lnk"
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"' + (Join-Path $root $Script) + '"'
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = $Description
    $shortcut.Save()
    Write-Host "Snelkoppeling gemaakt: $path"
}

New-ProgenShortcut $Folder "Progen" "launch.pyw" "Open Progen"
if ($StopOnDesktop) {
    New-ProgenShortcut $Folder "Stop Progen" "stop.pyw" "Stop de Progen-server"
}
if ($StartMenu) {
    $programs = Join-Path ([Environment]::GetFolderPath("Programs")) "Progen"
    New-ProgenShortcut $programs "Progen" "launch.pyw" "Open Progen"
    New-ProgenShortcut $programs "Stop Progen" "stop.pyw" "Stop de Progen-server"
}
