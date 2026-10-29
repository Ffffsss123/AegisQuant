#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
APP="${AEGIS_PREVIEW_OUTPUT:-$PWD/data/preview/AegisQuant Capabilities.app}"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
python3 -m trading.market_capabilities > "$APP/Contents/Resources/capabilities.json"
xcrun swiftc desktop/CapabilitiesPreview.swift -o "$APP/Contents/MacOS/CapabilitiesPreview"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleExecutable</key><string>CapabilitiesPreview</string><key>CFBundleIdentifier</key><string>local.aegisquant.capabilities</string><key>CFBundleName</key><string>AegisQuant Capabilities</string><key>CFBundlePackageType</key><string>APPL</string><key>NSHighResolutionCapable</key><true/></dict></plist>
PLIST
codesign --force --sign - "$APP"
