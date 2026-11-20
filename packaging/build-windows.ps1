# Builds the Windows version:
#   dist\Cuttycat\              program folder (Cuttycat.exe, _internal\, ffmpeg\)
#   dist\cuttycat-portable.zip  the same folder as a zip
#   dist\cuttycat_install.exe   installer (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
# Needs: pip install ".[all]" pyinstaller   (run from the repository root)
# ffmpeg.exe, ffprobe.exe and ffmpeg's license go into packaging\ffmpeg\ (not in git).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$version = (Select-String -Path cuttycat\__init__.py -Pattern '__version__ = "(.*)"').Matches[0].Groups[1].Value

pyinstaller --noconfirm --clean --workpath build\windows --distpath dist packaging\cuttycat.spec
if ($LASTEXITCODE) { exit $LASTEXITCODE }

$app = "dist\Cuttycat"
$ff = "packaging\ffmpeg"
if ((Test-Path "$ff\ffmpeg.exe") -and (Test-Path "$ff\ffprobe.exe")) {
    New-Item -ItemType Directory -Force "$app\ffmpeg" | Out-Null
    Copy-Item "$ff\ffmpeg.exe", "$ff\ffprobe.exe" "$app\ffmpeg"
    Get-ChildItem $ff -File | Where-Object Name -match '^(LICENSE|COPYING|README)' | Copy-Item -Destination "$app\ffmpeg"
} else {
    Write-Warning "No ffmpeg in $ff, the build will need ffmpeg installed on the system."
}
Copy-Item LICENSE "$app\LICENSE.txt"
"Built: $app ({0:N0} MB)" -f ((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)

Compress-Archive -Path $app -DestinationPath dist\cuttycat-portable.zip -Force
"Built: dist\cuttycat-portable.zip ({0:N0} MB)" -f ((Get-Item dist\cuttycat-portable.zip).Length / 1MB)

$iscc = @((Get-Command iscc -ErrorAction SilentlyContinue).Source,
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q "/DAppVersion=$version" packaging\windows\cuttycat.iss
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    "Built: dist\cuttycat_install.exe ({0:N0} MB)" -f ((Get-Item dist\cuttycat_install.exe).Length / 1MB)
} else {
    Write-Warning "Inno Setup 6 not found, no installer built (winget install JRSoftware.InnoSetup)."
}
