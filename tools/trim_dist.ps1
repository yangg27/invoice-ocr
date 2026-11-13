param(
    [string]$Dist = "dist\InvoiceOCR"
)

# 精简打包体积：删掉用不到的视频解码 DLL 与 Qt 翻译文件（不影响发票识别与界面）
$root = (Resolve-Path $Dist).Path
$removed = @()

$ffmpeg = Get-ChildItem "$root\_internal\cv2" -Filter "opencv_videoio_ffmpeg*.dll" -ErrorAction SilentlyContinue
foreach ($f in $ffmpeg) {
    Remove-Item $f.FullName -Force
    $removed += $f.Name
}

$trans = Join-Path $root "_internal\PySide6\translations"
if (Test-Path $trans) {
    Remove-Item $trans -Recurse -Force
    $removed += "PySide6\translations"
}

$size = (Get-ChildItem $root -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output ("已移除: " + ($removed -join ", "))
Write-Output ("打包后体积: {0:N1} MB" -f ($size / 1MB))