$ErrorActionPreference = "Stop"

$McpExe = Join-Path $PSScriptRoot "PatentAgentMCP\PatentAgentMCP.exe"
if (-not (Test-Path $McpExe)) {
  throw "未找到 PatentAgentMCP.exe。请完整解压 PatentAgent 发布目录后再运行。"
}

$Codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $Codex) {
  throw "未找到 Codex CLI。请先在 Codex 中启用 CLI，或把 codex 命令加入 PATH。"
}

& $Codex.Source mcp remove patent-agent 2>$null
& $Codex.Source mcp add patent-agent -- $McpExe --mcp
if ($LASTEXITCODE -ne 0) {
  throw "Codex MCP 注册失败，退出码：$LASTEXITCODE"
}

Write-Host "Patent Agent 已注册到 Codex。请完全退出并重新打开 Codex。" -ForegroundColor Green
Write-Host "MCP 路径：$McpExe"
