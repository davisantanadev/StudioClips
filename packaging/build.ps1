# Monta o StudioClips.exe e o zip pronto pra distribuir.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -FFmpegDir "C:\caminho\ffmpeg\bin"
# Sem -FFmpegDir, baixa o FFmpeg "essentials" (gyan.dev, com NVENC) e põe junto.
param(
    [string]$Python = "",
    [string]$FFmpegDir = "",
    [string]$FFmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $Python) {
    $Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
}

Write-Host "== PyInstaller"
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\studioclips.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }
$app = Join-Path $root "dist\StudioClips"

Write-Host "== FFmpeg"
$ffOut = Join-Path $app "ffmpeg"
New-Item -ItemType Directory -Force $ffOut | Out-Null
if (-not $FFmpegDir) {
    $tmp = Join-Path $root "build\ffmpeg-dl"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $zip = Join-Path $tmp "ffmpeg.zip"
    if (-not (Test-Path $zip)) {
        try { Invoke-WebRequest -Uri $FFmpegUrl -OutFile $zip -UseBasicParsing }
        catch {  # plano B: build do BtbN no GitHub
            Write-Host "gyan.dev falhou ($_); usando o build do BtbN"
            Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip -UseBasicParsing
        }
    }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $FFmpegDir = (Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
    $licDir = Split-Path -Parent $FFmpegDir
} else {
    $licDir = Split-Path -Parent $FFmpegDir
}
Copy-Item (Join-Path $FFmpegDir "ffmpeg.exe"), (Join-Path $FFmpegDir "ffprobe.exe") $ffOut -Force
foreach ($f in "LICENSE", "LICENSE.txt", "README.txt") {
    $p = Join-Path $licDir $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $ffOut "FFmpeg-$f") -Force }
}

Write-Host "== Arquivos do usuario"
Copy-Item "clipador\defaults\streamers.yaml" (Join-Path $app "streamers.yaml") -Force
Copy-Item "packaging\LEIA-ME.txt" (Join-Path $app "LEIA-ME.txt") -Force

Write-Host "== Zip"
$zipOut = Join-Path $root "dist\StudioClips-windows.zip"
if (Test-Path $zipOut) { Remove-Item $zipOut -Force }
Compress-Archive -Path $app -DestinationPath $zipOut -CompressionLevel Optimal
Write-Host ("Pronto: {0} ({1:N0} MB)" -f $zipOut, ((Get-Item $zipOut).Length / 1MB))
