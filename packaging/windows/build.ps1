# Build the Windows app and a zip of it.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Output: dist\Hatsumimi\Hatsumimi.exe  and  dist\Hatsumimi-windows.zip
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root

if (-not (Test-Path 'vendor\libmpv\libmpv-2.dll')) {
    throw "vendor\libmpv\libmpv-2.dll is missing (see README: libmpv)."
}
uv sync
uv run pyinstaller packaging\hatsumimi.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$zip = 'dist\Hatsumimi-windows.zip'
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path 'dist\Hatsumimi' -DestinationPath $zip
"Built dist\Hatsumimi\Hatsumimi.exe and $zip ({0:n0} MB)" -f ((Get-Item $zip).Length / 1MB)
