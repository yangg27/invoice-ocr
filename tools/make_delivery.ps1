param(
    [string]$Source = "dist\InvoiceOCR",
    [string]$Name = "电子发票识别助手"
)

# 生成交付目录与压缩包
$root = (Get-Location).Path
$target = Join-Path (Join-Path $root "dist") $Name
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $root $Source) -Destination $target -Recurse
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $target "使用说明.md") -Force
Copy-Item -LiteralPath (Join-Path $root "快速上手.txt") -Destination (Join-Path $target "快速上手.txt") -Force
Copy-Item -LiteralPath (Join-Path $root "识别准确率报告.md") -Destination (Join-Path $target "识别准确率报告.md") -Force

$zip = Join-Path (Join-Path $root "dist") "$Name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $target "*") -DestinationPath $zip -CompressionLevel Optimal

$size = (Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output ("交付目录: {0}" -f $target)
Write-Output ("压缩包:   {0} ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
Write-Output ("目录体积: {0:N1} MB" -f ($size / 1MB))