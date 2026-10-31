#!/bin/bash
# Baut "Shelfcast.app" (SwiftUI) neu. Braucht nur die Command Line Tools (swiftc).
set -e
cd "$(dirname "$0")"
APP="Shelfcast.app"
rm -rf "$APP" "Boox Picco Studio.app" "EPUB zu Boox.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -parse-as-library -target arm64-apple-macos26.0 $(ls mac/*.swift | grep -v make_icon) -o "$APP/Contents/MacOS/Shelfcast"
cp mac/Info.plist "$APP/Contents/Info.plist"
cp opub.py droplet_helper.py bookinfo.py "$APP/Contents/Resources/"
# Icon (optional): wird bei Bedarf generiert
if [ -f mac/make_icon.swift ]; then
  TMP=$(mktemp -d)
  mkdir "$TMP/AppIcon.iconset"
  swiftc -o "$TMP/mkicon" mac/make_icon.swift && "$TMP/mkicon" "$TMP/AppIcon.iconset" \
    && iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns" || echo "Icon uebersprungen"
  rm -rf "$TMP"
fi
codesign -s - --force --deep "$APP"
echo "Gebaut: $PWD/$APP"
