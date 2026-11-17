# Install the built app for the current user, with a Start menu shortcut.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1 [-Desktop]
# Copies dist\Hatsumimi to %LOCALAPPDATA%\Programs\Hatsumimi (no admin needed).
# Run it again after a rebuild to update. Uninstall: delete that folder and the shortcut(s).
param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
$src = Join-Path $root 'dist\Hatsumimi'
if (-not (Test-Path "$src\Hatsumimi.exe")) { throw "Build first: packaging\windows\build.ps1" }

$dest = Join-Path $env:LOCALAPPDATA 'Programs\Hatsumimi'
if (Get-Process Hatsumimi -ErrorAction SilentlyContinue) { throw "Close Hatsumimi first." }
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse $src $dest

$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Programs'))
if ($Desktop) { $targets += [Environment]::GetFolderPath('Desktop') }
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir 'Hatsumimi.lnk'))
    $lnk.TargetPath = Join-Path $dest 'Hatsumimi.exe'
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = (Join-Path $dest 'Hatsumimi.exe') + ',0'
    $lnk.Description = 'Watch Japanese media without subtitles'
    $lnk.Save()
}
"Installed to $dest"
"Shortcut(s): " + ($targets -join ', ')
