<#
.SYNOPSIS
    Puts a "Progen" shortcut on your desktop (and optionally in the Start menu).

.DESCRIPTION
    The shortcut starts launch.pyw with pythonw.exe from .venv: no console window,
    the server starts when needed, and Progen opens as its own window.
    Running it once is enough; running it again updates the shortcuts.

.PARAMETER StartMenu
    Also puts "Progen" and "Stop Progen" in the Start menu.

.PARAMETER StopOnDesktop
    Also puts "Stop Progen" on the desktop.

.PARAMETER Folder
    A folder other than the desktop (mainly for testing).

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
    Write-Error "$pythonw not found. Create the venv first (see README, 'Quick start')."
}
if (-not (Test-Path $icon)) {
    Write-Error "$icon not found. Make it with: .venv\Scripts\python.exe scripts\make_icon.py"
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
    Write-Host "Shortcut created: $path"
}

New-ProgenShortcut $Folder "Progen" "launch.pyw" "Open Progen"
if ($StopOnDesktop) {
    New-ProgenShortcut $Folder "Stop Progen" "stop.pyw" "Stop the Progen server"
}
if ($StartMenu) {
    $programs = Join-Path ([Environment]::GetFolderPath("Programs")) "Progen"
    New-ProgenShortcut $programs "Progen" "launch.pyw" "Open Progen"
    New-ProgenShortcut $programs "Stop Progen" "stop.pyw" "Stop the Progen server"
}
