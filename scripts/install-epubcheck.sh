#!/usr/bin/env bash
# Downloads EPUBCheck (W3C, BSD-3) into tools/epubcheck. One-time setup; runs offline afterwards.
source "$(dirname "$0")/lib/common.sh"
VERSION="5.1.0"
SHA256="74a59af8602bf59b1d04266a450d9cdcb5986e36d825adc403cde0d95e88c9e8"
DEST="$REPO_ROOT/tools/epubcheck"
require_cmd java "Install a Java 11+ runtime (e.g. sudo apt install default-jre-headless, brew install openjdk)."
require_cmd curl "Install curl."
require_cmd unzip "Install unzip."
if [[ -f "$DEST/epubcheck.jar" ]]; then info "EPUBCheck already installed at $DEST"; exit 0; fi
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
info "Downloading EPUBCheck $VERSION"
curl -fsSL -o "$tmp/epubcheck.zip" "https://github.com/w3c/epubcheck/releases/download/v$VERSION/epubcheck-$VERSION.zip"
actual="$( (sha256sum "$tmp/epubcheck.zip" 2>/dev/null || shasum -a 256 "$tmp/epubcheck.zip") | cut -d' ' -f1)"
[[ "$actual" == "$SHA256" ]] || fail "Checksum mismatch for EPUBCheck download ($actual)"
unzip -q "$tmp/epubcheck.zip" -d "$tmp"
mkdir -p "$DEST"
cp -R "$tmp/epubcheck-$VERSION/." "$DEST/"
info "Installed: $(java -jar "$DEST/epubcheck.jar" --version 2>&1 | head -1)"
