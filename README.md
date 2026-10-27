# SwanGUI

**Cliente VPN IPsec para strongSwan, al estilo de FortiClient** — vive en la bandeja del sistema, junto al reloj.
*IPsec VPN client for strongSwan in the style of FortiClient — it lives in the system tray.* [English below](#english)

![license](https://img.shields.io/badge/licencia-GPL--3.0-blue) ![strongSwan](https://img.shields.io/badge/strongSwan-5.9%2B%20%2F%206.x-green)

## Funciones

- **Conexiones IPsec** IKEv1 (main/aggressive + XAuth) e IKEv2 (PSK, certificados, EAP): crear, editar, duplicar, borrar, conectar y desconectar desde la bandeja.
- **Configuración como FortiClient**: gateway, método de autenticación, usuario/contraseña, Fase 1 y Fase 2 en tablas de cifrado/autenticación, DH, DPD, PFS, NAT‑T, MOBIKE, fragmentación…
- **Plantillas** para FortiGate, pfSense/OPNsense, MikroTik, Windows RRAS, Cisco y strongSwan.
- **Importar / exportar** (con o sin contraseñas, y **cifrado con contraseña**): FortiClient, strongSwan (swanctl), MikroTik RouterOS, pfSense/OPNsense, Windows (PowerShell), Cisco IOS, Apple (.mobileconfig) y strongSwan Android (.sswan).
- **DNS dividido** con systemd-resolved: solo los dominios de la empresa van al DNS de la VPN, el resto sigue en tu DNS local. Comprueba que el DNS de la VPN responde antes de usarlo.
- **Kill switch** (nftables) que también cubre contenedores y máquinas virtuales.
- **Siempre conectada**, conexión automática al arrancar o fuera de tus **redes de confianza**, reconexión al **despertar el equipo** y espera ante **portales cautivos**.
- **Diagnóstico** guiado, prueba de **IP pública y fugas DNS**, registro filtrable, historial y gráfica de tráfico.
- Contraseñas en el **llavero del sistema** (opcional), **OTP / FortiToken**, aviso de **caducidad de certificados**, atajos globales y línea de comandos (`swangui connect NOMBRE`).
- Interfaz en **español e inglés**.

## Seguridad

SwanGUI separa la interfaz (tu usuario) de un pequeño *helper* que corre como root mediante **polkit**:

| Operación | Autorización |
|---|---|
| Ver estado, conectar, desconectar, diagnosticar | sin contraseña (administradores en sesión local activa) |
| Crear, editar, borrar, importar, editor de texto, exportar **con** contraseñas | **contraseña de administrador** (se recuerda unos minutos) |

- El helper valida todo lo que recibe: no acepta `updown`, `include` ni rutas fuera de `/etc/swanctl`, y rechaza nombres y caracteres peligrosos.
- Cada operación sensible queda registrada en el journal (`journalctl -t swangui-helper`); nunca se registran contraseñas.
- Las contraseñas de un solo uso viajan por stdin, nunca en la línea de comandos.
- Los archivos de SwanGUI en tu carpeta personal son privados (0600) y el canal de control solo acepta a tu usuario.

Si encuentras un problema de seguridad, consulta [SECURITY.md](SECURITY.md).

## Instalación

Descarga el paquete de tu distribución desde la [última versión](https://github.com/FrankMncrf/SwanGUI/releases/latest):

| Distribución | Paquete |
|---|---|
| Debian 12+ / Ubuntu 24.04+ | [swangui_1.0.5-1_all.deb](https://github.com/FrankMncrf/SwanGUI/releases/download/v1.0.5/swangui_1.0.5-1_all.deb) |
| Fedora | [swangui-1.0.5-1.fc44.noarch.rpm](https://github.com/FrankMncrf/SwanGUI/releases/download/v1.0.5/swangui-1.0.5-1.fc44.noarch.rpm) |
| Arch Linux, CachyOS, Manjaro, EndeavourOS | [swangui-1.0.5-1-any.pkg.tar.zst](https://github.com/FrankMncrf/SwanGUI/releases/download/v1.0.5/swangui-1.0.5-1-any.pkg.tar.zst) · [PKGBUILD](https://github.com/FrankMncrf/SwanGUI/releases/download/v1.0.5/PKGBUILD) |
| Sumas de verificación | [SHA256SUMS](https://github.com/FrankMncrf/SwanGUI/releases/download/v1.0.5/SHA256SUMS) |

### Debian / Ubuntu

```sh
sudo apt install ./swangui_1.0.5-1_all.deb
```

### Fedora

```sh
sudo dnf install ./swangui-1.0.5-1.fc44.noarch.rpm
```

### Arch Linux y derivadas

```sh
sudo pacman -U swangui-1.0.5-1-any.pkg.tar.zst
```

O constrúyelo tú con el `PKGBUILD`: `makepkg -si`. Pronto también en AUR (`yay -S swangui`).

### Desde el código

```sh
git clone https://github.com/FrankMncrf/SwanGUI.git && cd SwanGUI
./install.sh            # instala en /usr/local (pide sudo)
```

**Requisitos:** strongSwan con `swanctl` y el servicio `strongswan` (charon-systemd), Python 3.10+, PyQt6, polkit con un agente de autenticación en el escritorio, nftables, iproute2 y systemd-resolved (para DNS dividido). Opcionales: python-cryptography (exportaciones cifradas), libsecret/secret-tool (llavero) y NetworkManager (redes de confianza, portales cautivos).

## Uso

Abre **SwanGUI** desde el menú de aplicaciones. El icono del escudo en la bandeja indica el estado (verde conectado, ámbar conectando, rojo error o kill switch bloqueando). Clic derecho para conectar o desconectar. Al dejar el puntero sobre el icono aparece una tarjeta con los detalles de cada conexión: calidad, IP virtual, tiempo conectado, tráfico, velocidad, DNS y renegociación, con botones para conectar o desconectar.

```text
swangui status | list | connect NOMBRE | disconnect NOMBRE | toggle NOMBRE
```

## Contribuir

Reporta errores y sugerencias en [Issues](https://github.com/FrankMncrf/SwanGUI/issues) o [Discussions](https://github.com/FrankMncrf/SwanGUI/discussions) (también desde *Ayuda → Reportar un error*). Las pruebas se ejecutan con `make test`.

## Apoyar el proyecto

Si SwanGUI te resulta útil, puedes apoyarlo (también desde *Ayuda → Donar*):

- ☕ [Buy Me a Coffee](https://buymeacoffee.com/frankmncrf)
- ₿ Bitcoin: `bc1qm45kpx35uazdts3q8vc77edqwyajuxe9cexw0er5409dyevl3nysxhln9g`
- ⭐ Una estrella en GitHub también ayuda.

## Licencia

GPL-3.0-or-later. Consulta [LICENSE](LICENSE).

---

## English

SwanGUI is a tray application to create, edit, import/export, connect and monitor **strongSwan IPsec** VPNs with a FortiClient-like interface: IKEv1/IKEv2, PSK, certificates, EAP and XAuth, vendor templates, split DNS through systemd-resolved (with a liveness check of the VPN DNS), an nftables kill switch that also covers containers, trusted networks, auto-reconnect after suspend, captive portal awareness, a hover card on the tray icon with live connection details, diagnostics, DNS leak test, keyring storage, OTP, certificate expiry warnings, keyboard shortcuts and a CLI.

Import/export formats: FortiClient, strongSwan swanctl, MikroTik RouterOS, pfSense/OPNsense, Windows PowerShell, Cisco IOS, Apple .mobileconfig and strongSwan for Android (.sswan), optionally **password-encrypted** (scrypt + AES-256-GCM).

Privileged work goes through a small helper started with **pkexec**: routine operations need no password, while anything that changes the configuration or can reveal secrets requires the administrator password. Packages for Debian/Ubuntu (`.deb`), Fedora (`.rpm`) and Arch (`.pkg.tar.zst`, `PKGBUILD`) are on the [releases page](https://github.com/FrankMncrf/SwanGUI/releases/latest); see the table above. AUR coming soon. The interface follows your locale (Spanish or English) and can be changed in Preferences.
