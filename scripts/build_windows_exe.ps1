$ErrorActionPreference = "Stop"

$AppName = if ($env:APP_NAME) { $env:APP_NAME } else { "PatentAgent" }

Set-Location (Join-Path $PSScriptRoot "..")

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "当前 Python 环境没有 PyInstaller，正在安装..."
  python -m pip install pyinstaller
}

python -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "当前 Python 环境没有 pywebview，正在安装..."
  python -m pip install pywebview
}

if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }

python -m PyInstaller `
  --noconfirm `
  --noconsole `
  --onedir `
  --name $AppName `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "skills;skills" `
  --exclude-module PIL._avif `
  --hidden-import webview `
  --hidden-import webview.platforms.winforms `
  desktop_launcher.py

$AvifFiles = @(Get-ChildItem "dist\$AppName" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*_avif*.pyd" })
if ($AvifFiles.Count -gt 0) {
  $AvifFiles | ForEach-Object { Write-Host "检测到不兼容文件：$($_.FullName)" }
  throw "Windows 发布包中仍包含 Pillow AVIF 原生扩展，已停止发布。"
}

if (-not (Test-Path "dist\$AppName\_internal")) {
  throw "Windows 构建不是便携目录版：缺少 dist\$AppName\_internal。"
}

Write-Host "已生成：dist\$AppName\$AppName.exe"
Write-Host "发布时请保留 dist\$AppName 整个目录，不要只复制 exe。"
