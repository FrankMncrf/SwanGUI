#!/bin/sh
# Build swangui_<version>_all.deb into ./dist (run from the source tree).
set -eu
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' swangui.py)
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
make install DESTDIR="$ROOT" PREFIX=/usr LICENSEDIR=/usr/share/doc/swangui
mv "$ROOT/usr/share/doc/swangui/LICENSE" "$ROOT/usr/share/doc/swangui/copyright"
gzip -9n -c CHANGELOG.md > "$ROOT/usr/share/doc/swangui/changelog.gz"
mkdir -p "$ROOT/DEBIAN"
sed "s/@VERSION@/$VERSION/" packaging/debian/control.in > "$ROOT/DEBIAN/control"
echo "Installed-Size: $(du -sk "$ROOT/usr" | cut -f1)" >> "$ROOT/DEBIAN/control"
mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "dist/swangui_${VERSION}-1_all.deb"
