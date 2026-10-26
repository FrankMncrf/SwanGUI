#!/bin/sh
# Instala SwanGUI en /usr/local (sin gestor de paquetes).
#   ./install.sh               instala o actualiza (pide sudo)
#   ./install.sh --uninstall   desinstala (la configuración de /etc/swanctl se conserva)
set -e
cd "$(dirname "$0")"
if [ "$1" = "--uninstall" ]; then
    pkill -f '^python3 /usr/local/bin/swangui' || true
    sudo make uninstall PREFIX=/usr/local POLKIT_RULESDIR=/etc/polkit-1/rules.d
    rm -f ~/.config/autostart/swangui.desktop
    echo "SwanGUI desinstalado (la configuración en /etc/swanctl se conserva)."
    exit 0
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
