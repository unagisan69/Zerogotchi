#!/bin/sh
set -eu

: "${STAGE:?Package staging directory is required}"
: "${INSTALL_PREFIX:?APPLaunch install prefix is required}"

cd "$(dirname "$0")/.."
runtime="$STAGE/usr/lib/zerogotchi"
install -d -m755 "$runtime"
install -m755 app/zerogotchi app/radio "$runtime/"
install -m644 app/wifi.py app/gnss.py app/wigle.py app/updater.py "$runtime/"
for asset in app/themes/*/*.png; do
    install -Dm644 "$asset" "$runtime/${asset#app/}"
done

install -d -m755 "$STAGE/usr/bin" "$STAGE$INSTALL_PREFIX/bin"
ln -s ../lib/zerogotchi/zerogotchi "$STAGE/usr/bin/zerogotchi"
ln -s ../../../lib/zerogotchi/zerogotchi "$STAGE$INSTALL_PREFIX/bin/zerogotchi"
install -Dm644 packaging/icon.png "$STAGE$INSTALL_PREFIX/share/images/zerogotchi.png"
install -Dm644 LICENSE "$STAGE/usr/share/doc/zerogotchi/copyright"
