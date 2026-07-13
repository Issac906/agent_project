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
  --onefile `
  --name $AppName `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "skills;skills" `
  --hidden-import webview `
  --hidden-import webview.platforms.winforms `
  desktop_launcher.py

Write-Host "已生成：dist\$AppName.exe"
