#!/usr/bin/env bash
set -euo pipefail

AT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AT_PACKAGE_DIR="$(cd "$AT_SCRIPT_DIR/.." && pwd)"
AT_REPO_ROOT="$(cd "$AT_PACKAGE_DIR/../.." && pwd)"
AT_OUTPUT_DIR="${AGENT_TELEMETRY_OUTPUT_DIR:-$AT_PACKAGE_DIR/build}"
AT_RELEASE="20261003"
AT_PYTHON_VERSION="3.13.16"
AT_SIGN_IDENTITY="${AGENT_TELEMETRY_SIGN_IDENTITY:--}"
AT_ARCH="$(uname -m)"

case "$AT_ARCH" in
  arm64)
    AT_PYTHON_TARGET="aarch64-apple-darwin"
    AT_PYTHON_SHA256="9e01f63bbb08576cd9c8bc2d0564d098cb30c8453a0cd4bcf6aef458f6d2a147"
    ;;
  x86_64)
    AT_PYTHON_TARGET="x86_64-apple-darwin"
    AT_PYTHON_SHA256="b4dad38ba6a344555ccb71a1b08caad0a6c0dda88c5803658bc95bd7f04e9f5c"
    ;;
  *)
    echo "Unsupported build architecture: $AT_ARCH" >&2
    exit 2
    ;;
esac

AT_ARCHIVE_NAME="cpython-${AT_PYTHON_VERSION}+${AT_RELEASE}-${AT_PYTHON_TARGET}-install_only_stripped.tar.gz"
AT_ARCHIVE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${AT_RELEASE}/${AT_ARCHIVE_NAME/+/%2B}"
AT_TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$AT_TEMP_DIR"' EXIT

mkdir -p "$AT_OUTPUT_DIR"
AT_SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
AT_EXECUTABLE="$AT_TEMP_DIR/AgentTelemetryMac"
AT_SWIFT_SOURCES=("$AT_PACKAGE_DIR"/Sources/AgentTelemetryMac/*.swift)
swiftc -sdk "$AT_SDK_PATH" -target "$AT_ARCH-apple-macosx13.0" \
  -framework SwiftUI -framework AppKit -framework WebKit -framework ServiceManagement \
  -o "$AT_EXECUTABLE" "${AT_SWIFT_SOURCES[@]}"

AT_APP_PATH="$AT_OUTPUT_DIR/AgentTelemetry.app"
rm -rf "$AT_APP_PATH"
mkdir -p "$AT_APP_PATH/Contents/MacOS" "$AT_APP_PATH/Contents/Resources"
cp "$AT_EXECUTABLE" "$AT_APP_PATH/Contents/MacOS/AgentTelemetryMac"
cp "$AT_PACKAGE_DIR/Resources/Info.plist" "$AT_APP_PATH/Contents/Info.plist"

# Build a complete macOS icon set from the supplied 1024px master image.
AT_ICONSET_DIR="$AT_TEMP_DIR/AppIcon.iconset"
AT_ICON_MASTER="$AT_PACKAGE_DIR/Resources/AppIcon.png"
mkdir -p "$AT_ICONSET_DIR"
make_icon() {
  local AT_ICON_NAME="$1"
  local AT_ICON_SIZE="$2"
  /usr/bin/sips -z "$AT_ICON_SIZE" "$AT_ICON_SIZE" "$AT_ICON_MASTER" \
    --out "$AT_ICONSET_DIR/$AT_ICON_NAME" >/dev/null
}
make_icon icon_16x16.png 16
make_icon icon_16x16@2x.png 32
make_icon icon_32x32.png 32
make_icon icon_32x32@2x.png 64
make_icon icon_128x128.png 128
make_icon icon_128x128@2x.png 256
make_icon icon_256x256.png 256
make_icon icon_256x256@2x.png 512
make_icon icon_512x512.png 512
cp "$AT_ICON_MASTER" "$AT_ICONSET_DIR/icon_512x512@2x.png"
/usr/bin/iconutil --convert icns --output "$AT_APP_PATH/Contents/Resources/AppIcon.icns" \
  "$AT_ICONSET_DIR"

AT_BACKEND_DIR="$AT_APP_PATH/Contents/Resources/AgentTelemetry"
mkdir -p "$AT_BACKEND_DIR"
cp "$AT_REPO_ROOT/dashboard.py" "$AT_REPO_ROOT/parser.py" \
   "$AT_REPO_ROOT/index.html" "$AT_REPO_ROOT/chart.umd.min.js" \
   "$AT_REPO_ROOT/manifest.json" "$AT_REPO_ROOT/sw.js" \
   "$AT_REPO_ROOT/LICENSE" "$AT_BACKEND_DIR/"
ditto "$AT_REPO_ROOT/static" "$AT_BACKEND_DIR/static"

if [[ -n "${AGENT_TELEMETRY_PYTHON_ARCHIVE:-}" ]]; then
  cp "$AGENT_TELEMETRY_PYTHON_ARCHIVE" "$AT_TEMP_DIR/$AT_ARCHIVE_NAME"
else
  curl --fail --location --retry 3 "$AT_ARCHIVE_URL" -o "$AT_TEMP_DIR/$AT_ARCHIVE_NAME"
fi
printf '%s  %s\n' "$AT_PYTHON_SHA256" "$AT_TEMP_DIR/$AT_ARCHIVE_NAME" | shasum -a 256 -c
mkdir -p "$AT_TEMP_DIR/unpacked" "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime"
tar -xzf "$AT_TEMP_DIR/$AT_ARCHIVE_NAME" -C "$AT_TEMP_DIR/unpacked"
ditto "$AT_TEMP_DIR/unpacked/python" "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime"

# The backend uses only CPython's standard library. Remove its unused package installer.
rm -rf "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/lib/python3.13/site-packages/pip" \
       "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/lib/python3.13/site-packages/pip-"*.dist-info \
       "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/lib/python3.13/ensurepip"
rm -f "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/bin/pip"* \
      "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/bin/idle3"* \
      "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/bin/pydoc3"*
cat > "$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/BUILD-INFO.txt" <<EOF
Runtime: CPython $AT_PYTHON_VERSION
Source: python-build-standalone release $AT_RELEASE
Target: $AT_PYTHON_TARGET
Archive: $AT_ARCHIVE_NAME
SHA-256: $AT_PYTHON_SHA256
EOF

"$AT_APP_PATH/Contents/Resources/AgentTelemetryRuntime/bin/python3" --version

if find "$AT_APP_PATH/Contents/Resources" \( -name .usage_cache.json -o -name server.log \
     -o -name .peers.json -o -name .peers \) -print -quit | grep -q .; then
  echo "Refusing to package user telemetry data." >&2
  exit 1
fi

# Sign nested Mach-O files first, then the app bundle. Ad-hoc signing is for local builds.
while IFS= read -r -d '' AT_FILE; do
  if [[ "$(/usr/bin/file -b "$AT_FILE")" == *"Mach-O"* ]]; then
    if [[ "$AT_SIGN_IDENTITY" == "-" ]]; then
      /usr/bin/codesign --force --sign - "$AT_FILE"
    else
      /usr/bin/codesign --force --options runtime --timestamp --sign "$AT_SIGN_IDENTITY" "$AT_FILE"
    fi
  fi
done < <(find "$AT_APP_PATH/Contents" -type f -print | sed -n '1!G;h;$p')
if [[ "$AT_SIGN_IDENTITY" == "-" ]]; then
  /usr/bin/codesign --force --sign - "$AT_APP_PATH"
else
  /usr/bin/codesign --force --options runtime --timestamp --sign "$AT_SIGN_IDENTITY" "$AT_APP_PATH"
fi
/usr/bin/codesign --verify --deep --strict "$AT_APP_PATH"

AT_DMG_ROOT="$AT_TEMP_DIR/dmg"
AT_DMG_PATH="$AT_OUTPUT_DIR/AgentTelemetry-macos-$AT_ARCH.dmg"
mkdir -p "$AT_DMG_ROOT"
ditto "$AT_APP_PATH" "$AT_DMG_ROOT/AgentTelemetry.app"
ln -s /Applications "$AT_DMG_ROOT/Applications"
/usr/bin/hdiutil create -volname AgentTelemetry -srcfolder "$AT_DMG_ROOT" \
  -ov -format UDZO "$AT_DMG_PATH"

if [[ -n "${AGENT_TELEMETRY_NOTARY_PROFILE:-}" ]]; then
  /usr/bin/xcrun notarytool submit "$AT_DMG_PATH" \
    --keychain-profile "$AGENT_TELEMETRY_NOTARY_PROFILE" --wait
  /usr/bin/xcrun stapler staple "$AT_DMG_PATH"
fi

echo "Created $AT_DMG_PATH"
