#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
# Use the installed compatible SDK when Command Line Tools also contain a newer SDK
# whose SwiftUI macro plugins are unavailable in that toolchain.
if [[ -z "${SDKROOT:-}" && "$(xcode-select -p)" == /Library/Developer/CommandLineTools && -d /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk ]]; then
  export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
fi
app_output="${AEGIS_APP_OUTPUT:-$PWD/AegisQuant.app}"
mkdir -p "$app_output/Contents/MacOS" "$app_output/Contents/Resources"
cp desktop/Info.plist "$app_output/Contents/Info.plist"
printf '%s\n' "${AEGIS_PROJECT_ROOT:-$PWD}" > "$app_output/Contents/Resources/project.txt"
xcrun swiftc -swift-version 5 -module-cache-path /private/tmp/aegis-swift-cache -parse-as-library desktop/QuantDesktop.swift -o "$app_output/Contents/MacOS/QuantDesktop"
codesign --force --sign - "$app_output"
