#!/bin/sh
# Instala SwanGUI en /usr/local (sin gestor de paquetes).
#   ./install.sh               instala o actualiza (pide sudo)
#   ./install.sh --uninstall   desinstala (la configuración de /etc/swanctl se conserva)
#   ./install.sh --no-deps     instala sin instalar dependencias
set -e
cd "$(dirname "$0")"
if [ "$1" = "--uninstall" ]; then
    pkill -f '^python3 /usr/local/bin/swangui' || true
    sudo make uninstall PREFIX=/usr/local POLKIT_RULESDIR=/etc/polkit-1/rules.d
    rm -f ~/.config/autostart/swangui.desktop
    echo "SwanGUI desinstalado (la configuración en /etc/swanctl se conserva)."
    exit 0
fi
# dependencies (the same ones the .deb, .rpm and Arch packages pull in)
if [ "$1" != "--no-deps" ]; then
    if command -v pacman >/dev/null; then
        deps="python python-pyqt6 strongswan polkit nftables iproute2 systemd openssl iputils python-cryptography libsecret networkmanager polkit-gnome"
        missing=$(pacman -T $deps || true)
        [ -z "$missing" ] || sudo pacman -S --needed --noconfirm $missing
    elif command -v apt-get >/dev/null; then
        deps="python3 python3-pyqt6 strongswan-swanctl charon-systemd polkitd nftables iproute2 openssl iputils-ping python3-cryptography libsecret-tools network-manager"
        missing=$(for d in $deps; do dpkg -s "$d" >/dev/null 2>&1 || echo "$d"; done)
        [ -z "$missing" ] || { sudo apt-get update && sudo apt-get install -y $missing; }
    elif command -v dnf >/dev/null; then
        deps="python3 python3-pyqt6 strongswan polkit nftables iproute openssl iputils python3-cryptography libsecret NetworkManager systemd-resolved"
        missing=$(for d in $deps; do rpm -q "$d" >/dev/null 2>&1 || echo "$d"; done)
        [ -z "$missing" ] || sudo dnf install -y $missing
    else
        echo "Aviso: instala tú las dependencias (ver README → Requisitos)." >&2
    fi
fi
# leftovers from older versions
sudo rm -rf /usr/local/lib/swangui/__pycache__
rm -f ~/.local/share/applications/swangui.desktop
sudo make install PREFIX=/usr/local POLKIT_RULESDIR=/etc/polkit-1/rules.d
# autostart only on first install; afterwards it is a SwanGUI preference
if [ ! -e ~/.config/swangui/settings.json ] && [ ! -e ~/.config/autostart/swangui.desktop ]; then
    install -D -m 644 data/swangui.desktop ~/.config/autostart/swangui.desktop
    sed -i 's/^Exec=.*/Exec=swangui/' ~/.config/autostart/swangui.desktop
fi
echo "SwanGUI instalado. Ejecuta: swangui &"
