#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-PatentAgent}"
DMG_NAME="${DMG_NAME:-PatentAgent-macOS.dmg}"
PKG_NAME="${PKG_NAME:-PatentAgent-macOS.pkg}"
APP_VERSION="${APP_VERSION:-1.0.0}"
APP_IDENTIFIER="${APP_IDENTIFIER:-com.patentagent.desktop}"

cd "$(dirname "$0")/.."

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "错误：当前 Python 环境没有 PyInstaller。请先运行：python3 -m pip install pyinstaller" >&2
  exit 1
fi

if ! python3 -c "import webview" >/dev/null 2>&1; then
  echo "错误：当前 Python 环境没有 pywebview。请先运行：python3 -m pip install pywebview" >&2
  exit 1
fi

if [[ -d build ]]; then
  chmod -R u+w build 2>/dev/null || true
fi
if [[ -d dist ]]; then
  chmod -R u+w dist 2>/dev/null || true
fi
rm -rf build
mkdir -p dist
# Finder may recreate dist/.DS_Store while the folder is open, so remove only
# artifacts owned by this build instead of trying to delete the directory.
rm -rf \
  "dist/${APP_NAME}" \
  "dist/${APP_NAME}.app" \
  "dist/dmg-root" \
  "dist/${DMG_NAME}" \
  "dist/${PKG_NAME}" \
  "dist/${APP_NAME}-component.pkg"
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
  --hidden-import feishu_integration \
  --hidden-import feishu_rendering \
  --hidden-import feishu_agent \
  --collect-submodules lark_oapi \
  --hidden-import apscheduler \
  desktop_launcher.py

DMG_ROOT="dist/dmg-root"
rm -rf "${DMG_ROOT}"
mkdir -p "${DMG_ROOT}"
cp -R "dist/${APP_NAME}.app" "${DMG_ROOT}/"
ln -s /Applications "${DMG_ROOT}/Applications"

hdiutil create \
  -volname "${APP_NAME}" \
  -srcfolder "${DMG_ROOT}" \
  -ov \
  -format UDBZ \
  "dist/${DMG_NAME}"

COMPONENT_PKG="dist/${APP_NAME}-component.pkg"
pkgbuild \
  --component "dist/${APP_NAME}.app" \
  --install-location "/Applications" \
  --identifier "${APP_IDENTIFIER}" \
  --version "${APP_VERSION}" \
  "${COMPONENT_PKG}"

productbuild \
  --package "${COMPONENT_PKG}" \
  "dist/${PKG_NAME}"

rm -f "${COMPONENT_PKG}"

echo "已生成轻量拖拽安装包：dist/${DMG_NAME}"
echo "已生成向导式安装包：dist/${PKG_NAME}"
