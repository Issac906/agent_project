#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-PatentAgent}"
DMG_NAME="${DMG_NAME:-PatentAgent-macOS.dmg}"

cd "$(dirname "$0")/.."

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "错误：当前 Python 环境没有 PyInstaller。请先运行：python3 -m pip install pyinstaller" >&2
  exit 1
fi

if ! python3 -c "import webview" >/dev/null 2>&1; then
  echo "错误：当前 Python 环境没有 pywebview。请先运行：python3 -m pip install pywebview" >&2
  exit 1
fi

rm -rf build dist
export PYINSTALLER_CONFIG_DIR="${PWD}/.pyinstaller-cache"
mkdir -p "${PYINSTALLER_CONFIG_DIR}"

python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --onedir \
  --name "${APP_NAME}" \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --add-data "skills:skills" \
  --hidden-import webview \
  --hidden-import webview.platforms.cocoa \
  --hidden-import patent_agent_mcp \
  --hidden-import patent_agent_cli \
  --hidden-import patent_agent_bridge \
  --hidden-import backend_runtime \
  desktop_launcher.py

python3 -m PyInstaller \
  --noconfirm \
  --console \
  --onedir \
  --name "${APP_NAME}MCP" \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --add-data "skills:skills" \
  --hidden-import patent_agent_mcp \
  --hidden-import patent_agent_cli \
  --hidden-import patent_agent_bridge \
  --hidden-import backend_runtime \
  desktop_launcher.py

DMG_ROOT="dist/dmg-root"
rm -rf "${DMG_ROOT}"
mkdir -p "${DMG_ROOT}"
cp -R "dist/${APP_NAME}.app" "${DMG_ROOT}/"
cp -R "dist/${APP_NAME}MCP" "${DMG_ROOT}/"
cp "AI_CLIENT_INTEGRATION.md" "${DMG_ROOT}/"

hdiutil create \
  -volname "${APP_NAME}" \
  -srcfolder "${DMG_ROOT}" \
  -ov \
  -format UDZO \
  "dist/${DMG_NAME}"

echo "已生成：dist/${DMG_NAME}"
