# Windows PowerShell 脚本
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$targetDir = "D:\ztb_collector"
if (-not (Test-Path $targetDir)) {
    $targetDir = (Get-Item $PSScriptRoot).Parent.FullName
}

Write-Host "[1/3] 正在从云端拉取最新脚本更新 (update_i5.zip)..." -ForegroundColor Cyan
$zipPath = Join-Path $targetDir "update_i5.zip"
$url = "https://ztb.139771.xyz/update_i5.zip?t=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

try {
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing -TimeoutSec 30
    if (Test-Path $zipPath) {
        Write-Host "[2/3] 正在解压并覆盖更新文件..." -ForegroundColor Cyan
        Expand-Archive -Path $zipPath -DestinationPath $targetDir -Force
        Remove-Item $zipPath -Force
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "   v0.1.7 脚本与控制台已成功更新就绪！   " -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
    }
} catch {
    Write-Host "[更新失败] 下载更新包异常: $_`n请检查网络连接或稍后重试。" -ForegroundColor Red
}

Write-Host "按回车键退出..."
Read-Host
