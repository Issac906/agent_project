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

$Npm = Get-Command npm -ErrorAction SilentlyContinue
$Node = Get-Command node -ErrorAction SilentlyContinue
if (-not $Npm -or -not $Node) {
  throw "构建 Windows 完整包需要 Node.js 22 和 npm。"
}
$PiRuntime = Join-Path (Get-Location) "build\pi-runtime"
New-Item -ItemType Directory -Force $PiRuntime | Out-Null
& $Npm.Source install --prefix $PiRuntime --omit=dev --no-audit --no-fund "@earendil-works/pi-coding-agent"
if ($LASTEXITCODE -ne 0) {
  throw "Pi Coding Agent 运行时安装失败。"
}
Copy-Item $Node.Source (Join-Path $PiRuntime "node.exe") -Force

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
  --hidden-import patent_agent_mcp `
  --hidden-import patent_agent_cli `
  --hidden-import patent_agent_bridge `
  --hidden-import backend_runtime `
  --hidden-import feishu_integration `
  --hidden-import feishu_rendering `
  --hidden-import feishu_agent `
  --collect-submodules lark_oapi `
  --hidden-import apscheduler `
  desktop_launcher.py

python -m PyInstaller `
  --noconfirm `
  --console `
  --onedir `
  --name "${AppName}MCP" `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "skills;skills" `
  --exclude-module PIL._avif `
  --hidden-import patent_agent_mcp `
  --hidden-import patent_agent_cli `
  --hidden-import patent_agent_bridge `
  --hidden-import backend_runtime `
  --hidden-import feishu_integration `
  --hidden-import feishu_rendering `
  --hidden-import feishu_agent `
  --collect-submodules lark_oapi `
  --hidden-import apscheduler `
  desktop_launcher.py

Copy-Item "dist\${AppName}MCP" "dist\$AppName\${AppName}MCP" -Recurse -Force
Copy-Item $PiRuntime "dist\$AppName\pi-runtime" -Recurse -Force
Copy-Item "AI_CLIENT_INTEGRATION.md" "dist\$AppName\AI_CLIENT_INTEGRATION.md" -Force
Copy-Item "packaging\windows\Register-Codex.ps1" "dist\$AppName\Register-Codex.ps1" -Force
Copy-Item "packaging\windows\Register-Codex.cmd" "dist\$AppName\Register-Codex.cmd" -Force

$AvifFiles = @(Get-ChildItem "dist\$AppName" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*_avif*.pyd" })
if ($AvifFiles.Count -gt 0) {
  $AvifFiles | ForEach-Object { Write-Host "检测到不兼容文件：$($_.FullName)" }
  throw "Windows 发布包中仍包含 Pillow AVIF 原生扩展，已停止发布。"
}

if (-not (Test-Path "dist\$AppName\_internal")) {
  throw "Windows 构建不是便携目录版：缺少 dist\$AppName\_internal。"
}
if (-not (Test-Path "dist\$AppName\${AppName}MCP\${AppName}MCP.exe")) {
  throw "Windows AI 接入程序缺失。"
}
if (-not (Test-Path "dist\$AppName\${AppName}MCP\_internal")) {
  throw "Windows AI 接入程序不是便携目录版。"
}
if (-not (Test-Path "dist\$AppName\pi-runtime\node.exe")) {
  throw "Windows 包缺少内置 Node.js。"
}
if (-not (Test-Path "dist\$AppName\pi-runtime\node_modules\@earendil-works\pi-coding-agent\dist\cli.js")) {
  throw "Windows 包缺少内置 Pi Coding Agent。"
}

Write-Host "已生成：dist\$AppName\$AppName.exe"
Write-Host "AI 接入程序：dist\$AppName\${AppName}MCP\${AppName}MCP.exe --mcp"
Write-Host "Codex 注册脚本：dist\$AppName\Register-Codex.cmd"
Write-Host "Pi Agent 运行时：dist\$AppName\pi-runtime"
Write-Host "发布时请保留 dist\$AppName 整个目录，不要只复制 exe。"
