#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
mkdir -p '量化控制台.app/Contents/MacOS' '量化控制台.app/Contents/Resources'
cp desktop/Info.plist '量化控制台.app/Contents/Info.plist'
pwd > '量化控制台.app/Contents/Resources/project.txt'
swiftc -swift-version 5 -module-cache-path /private/tmp/quant-swift-cache -parse-as-library desktop/QuantDesktop.swift -o '量化控制台.app/Contents/MacOS/QuantDesktop'
codesign --force --sign - '量化控制台.app'
