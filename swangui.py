#!/usr/bin/env python3
"""SwanGUI — cliente VPN IPsec (strongSwan/swanctl) al estilo FortiClient, en la bandeja del sistema."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from collections import deque
import xml.etree.ElementTree as ET

from PyQt6.QtCore import (
    QLibraryInfo, QLocale, QObject, QPointF, QProcess, QSize, Qt, QTimer, QTranslator,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
    QKeySequenceEdit, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QSplitter, QSystemTrayIcon, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------------------
# translations: the source language is Spanish; i18n/<lang>.json maps its
# strings to other languages (missing entries fall back to Spanish)
# ---------------------------------------------------------------------------

I18N_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "i18n")
LANGUAGES = [("auto", "Automático / Automatic"), ("es", "Español"), ("en", "English")]


def _ui_language():
    """The "language" preference in settings.json, or else the session locale."""
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "swangui", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            lang = json.load(f).get("language", "auto")
    except (OSError, ValueError, AttributeError):
        lang = "auto"
    if lang == "auto":
        lang = "en"
        for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            val = os.environ.get(var, "").split(":")[0]
            if val:
                lang = "en" if val in ("C", "POSIX") or val.startswith("C.") else val[:2].lower()
                break
    if lang != "es" and not os.path.exists(os.path.join(I18N_DIR, f"{lang}.json")):
        lang = "en"
    return lang


def _load_catalog(lang):
    if lang == "es":
        return {}
    try:
        with open(os.path.join(I18N_DIR, f"{lang}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


UI_LANG = _ui_language()
_CATALOG = _load_catalog(UI_LANG)
os.environ["SWANGUI_LANG"] = UI_LANG  # pkexec clears the environment; the helper reads ours


def tr(text):
    return _CATALOG.get(text, text)


APP = "SwanGUI"
# the helper lives next to this file (/usr/lib/swangui or /usr/local/lib/swangui)
HELPER = os.environ.get("SWANGUI_HELPER",
                        os.path.join(os.path.dirname(os.path.realpath(__file__)), "swangui-helper"))
POLL_MS = 4000

COLORS = {
    "up": "#2ea043",
    "connecting": "#d29922",
    "down": "#8b949e",
    "error": "#da3633",
}


# ---------------------------------------------------------------------------
# icons
# ---------------------------------------------------------------------------

# outline of the shield: dark on light panels, light on dark ones ("auto" follows the Qt palette)
ICON_THEME = {"mode": "auto"}


def _outline_color():
    mode = ICON_THEME["mode"]
    if mode == "auto":
        app = QGuiApplication.instance()
        dark = app is not None and app.palette().window().color().lightness() < 128
        mode = "dark" if dark else "light"
    return "#e6edf3" if mode == "dark" else "#1b1f24"


def shield_icon(state, size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    path = QPainterPath()
    path.moveTo(32 * s, 4 * s)
    path.lineTo(56 * s, 13 * s)
    path.cubicTo(56 * s, 36 * s, 47 * s, 52 * s, 32 * s, 60 * s)
    path.cubicTo(17 * s, 52 * s, 8 * s, 36 * s, 8 * s, 13 * s)
    path.closeSubpath()
    p.setPen(QPen(QColor(_outline_color()), 2.5 * s))
    p.setBrush(QColor(COLORS[state]))
    p.drawPath(path)
    p.setPen(QPen(QColor("white"), 5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if state == "up":
        p.drawPolyline([pt(22, 33, s), pt(29, 40, s), pt(43, 25, s)])
    elif state == "connecting":
        for x in (22, 32, 42):
            p.drawPoint(pt(x, 33, s))
    elif state == "error":
        p.drawLine(pt(32, 20, s), pt(32, 35, s))
        p.drawPoint(pt(32, 44, s))
    else:
        # padlock
        p.setPen(QPen(QColor("white"), 3.5 * s))
        p.drawArc(int(25 * s), int(19 * s), int(14 * s), int(16 * s), 0, 180 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(int(22 * s), int(27 * s), int(20 * s), int(15 * s), 2 * s, 2 * s)
    p.end()
    return QIcon(pm)


def pt(x, y, s):
    return QPointF(x * s, y * s)


def dot_icon(state):
    pm = QPixmap(14, 14)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(COLORS[state]))
    p.drawEllipse(1, 1, 12, 12)
    p.end()
    return QIcon(pm)


def human_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def human_time(sec):
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# helper bridge
# ---------------------------------------------------------------------------

class Helper(QObject):
    """Runs the privileged helper asynchronously via pkexec."""

    def run(self, args, callback, stdin=None):
        proc = QProcess(self)
        proc.setProgram("pkexec")
        proc.setArguments([HELPER, *args])

        def done(_code, _status):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip()
            try:
                data = json.loads(out) if out.strip() else {}
            except json.JSONDecodeError:
                data = {"error": out.strip() or err}
            if proc.exitCode() != 0 and "error" not in data:
                if proc.exitCode() in (126, 127):
                    data = {"error": tr("Autorización denegada o cancelada (pkexec).")}
                else:
                    data = {"error": err or tr("El helper terminó con código {0}").format(proc.exitCode())}
            proc.deleteLater()
            callback(data)

        proc.finished.connect(done)
        proc.start()
        if stdin is not None:
            proc.write(stdin.encode())
        proc.closeWriteChannel()
        return proc


# ---------------------------------------------------------------------------
# proposals (Phase 1 / Phase 2)
# ---------------------------------------------------------------------------

# --- proposal vocabulary (FortiClient-style names -> strongSwan keywords)
ENCRYPTIONS = [
    ("aes128", "AES128"), ("aes192", "AES192"), ("aes256", "AES256"),
    ("aes128gcm16", "AES128-GCM"), ("aes256gcm16", "AES256-GCM"),
    ("chacha20poly1305", "CHACHA20-POLY1305"), ("3des", "3DES"),
]
AEAD = {"aes128gcm16", "aes192gcm16", "aes256gcm16", "chacha20poly1305"}
INTEGRITIES = [("sha1", "SHA1"), ("sha256", "SHA256"), ("sha384", "SHA384"),
               ("sha512", "SHA512"), ("md5", "MD5")]
DH_GROUPS = [
    ("modp768", "1"), ("modp1024", "2"), ("modp1536", "5"), ("modp2048", "14"),
    ("modp3072", "15"), ("modp4096", "16"), ("modp8192", "18"), ("ecp256", "19"),
    ("ecp384", "20"), ("ecp521", "21"), ("curve25519", "31"), ("curve448", "32"),
]
ENC_ALIASES = {"aes": "aes128", "aes128gcm": "aes128gcm16", "aes256gcm": "aes256gcm16",
               "aes128gcm128": "aes128gcm16", "aes256gcm128": "aes256gcm16"}
INTEG_ALIASES = {"sha": "sha1", "sha2_256": "sha256", "sha2_384": "sha384", "sha2_512": "sha512"}
DH_ALIASES = {"x25519": "curve25519", "x448": "curve448"}


def parse_proposals(text):
    """'aes256-sha256-modp2048,...' -> ([(enc, integ)], [dh]) or None if not representable."""
    rows, dhs = [], []
    for prop in filter(None, (p.strip() for p in text.split(","))):
        encs, integs = [], []
        for tok in prop.lower().split("-"):
            tok = ENC_ALIASES.get(tok, tok)
            if tok.startswith("prf"):
                tok = INTEG_ALIASES.get(tok[3:], tok[3:])
                if tok not in dict(INTEGRITIES):
                    return None
                if tok not in integs:
                    integs.append(tok)
                continue
            tok = INTEG_ALIASES.get(tok, DH_ALIASES.get(tok, tok))
            if tok in dict(ENCRYPTIONS):
                encs.append(tok)
            elif tok in dict(INTEGRITIES):
                integs.append(tok)
            elif tok in dict(DH_GROUPS):
                if tok not in dhs:
                    dhs.append(tok)
            elif tok in ("esn", "noesn"):
                continue
            else:
                return None
        if not encs:
            return None
        for e in encs:
            for i in (integs or ["sha256"]):
                if (e, i) not in rows:
                    rows.append((e, i))
    return rows, dhs


def build_proposals(rows, dhs, ike):
    out = []
    for enc, integ in rows:
        if enc in AEAD:
            parts = [enc, f"prf{integ}"] if ike else [enc]
        else:
            parts = [enc, integ]
        out.append("-".join(parts + dhs))
    return ",".join(dict.fromkeys(out))


def parse_seconds(text):
    """'4h' / '14400s' / '30m' / '1d' / '' -> int seconds (0 = default)."""
    text = (text or "").strip().lower()
    if not text:
        return 0
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        if text[-1] in mult:
            return int(float(text[:-1]) * mult[text[-1]])
        return int(text)
    except ValueError:
        return 0


def parse_bytes_kb(text):
    text = (text or "").strip().lower()
    if not text:
        return 0
    mult = {"k": 1, "m": 1024, "g": 1024 * 1024}
    try:
        if text[-1] in mult:
            return int(float(text[:-1]) * mult[text[-1]])
        return int(text) // 1024
    except ValueError:
        return 0


def combo(items):
    c = QComboBox()
    for val, label in items:
        c.addItem(label, val)
    return c


def set_combo(c, val):
    i = c.findData(val)
    if i < 0:
        c.addItem(val, val)
        i = c.count() - 1
    c.setCurrentIndex(i)


def spin(maximum, suffix, special=tr("Por defecto")):
    s = QSpinBox()
    s.setRange(0, maximum)
    s.setSuffix(suffix)
    s.setSpecialValueText(special)
    s.setGroupSeparatorShown(True)
    return s


class ProposalEditor(QWidget):
    """Table of Encryption/Authentication rows, like FortiClient's 'IKE Proposal'."""

    def __init__(self, ike):
        super().__init__()
        self.ike = ike
        self.rows = []
        self.default = QCheckBox(tr("Usar propuestas por defecto de strongSwan"))
        self.default.toggled.connect(self._update)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.addWidget(QLabel("<b>Cifrado</b>"), 0, 0)
        self.grid.addWidget(QLabel(tr("<b>Autenticación</b>")), 0, 1)
        self.add_btn = QPushButton(tr("+ Añadir propuesta"))
        self.add_btn.clicked.connect(lambda: self.add_row("aes256", "sha256"))
        self.raw = QLineEdit()
        self.raw_note = QLabel(tr("Esta propuesta usa algoritmos que no se pueden mostrar como tabla; edítala como texto:"))
        self.raw_note.setWordWrap(True)
        self.raw_note.setStyleSheet("color: #d29922")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.default)
        lay.addLayout(self.grid)
        row = QHBoxLayout()
        row.addWidget(self.add_btn)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(self.raw_note)
        lay.addWidget(self.raw)
        self.raw_mode = False

    def add_row(self, enc, integ):
        e, i = combo(ENCRYPTIONS), combo(INTEGRITIES)
        set_combo(e, enc)
        set_combo(i, integ)
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(tr("Quitar"))
        n = len(self.rows) + 1
        self.grid.addWidget(e, n, 0)
        self.grid.addWidget(i, n, 1)
        self.grid.addWidget(rm, n, 2)
        entry = (e, i, rm)
        self.rows.append(entry)
        rm.clicked.connect(lambda: self._remove(entry))
        e.currentIndexChanged.connect(self._update)
        self._update()

    def _remove(self, entry):
        if len(self.rows) <= 1:
            return
        self.rows.remove(entry)
        for w in entry:
            self.grid.removeWidget(w)
            w.deleteLater()
        # re-pack remaining rows
        for n, (e, i, rm) in enumerate(self.rows, 1):
            for col, w in enumerate((e, i, rm)):
                self.grid.addWidget(w, n, col)
        self._update()

    def _update(self):
        on = not self.default.isChecked()
        for e, i, rm in self.rows:
            for w in (e, i, rm):
                w.setVisible(not self.raw_mode)
                w.setEnabled(on)
            # ESP with AEAD (GCM/ChaCha) carries its own integrity
            i.setEnabled(on and (self.ike or e.currentData() not in AEAD))
            rm.setEnabled(on and len(self.rows) > 1)
        self.add_btn.setVisible(not self.raw_mode)
        self.add_btn.setEnabled(on)
        for w in (self.raw_note, self.raw):
            w.setVisible(self.raw_mode)
        self.raw.setEnabled(on)
        for n in range(2):
            self.grid.itemAtPosition(0, n).widget().setVisible(not self.raw_mode)

    def set_rows(self, rows, default=False):
        for r in self.rows:
            for w in r:
                w.deleteLater()
        self.rows = []
        for enc, integ in rows:
            self.add_row(enc, integ)
        self.default.setChecked(default)
        self._update()

    def set_raw(self, text):
        self.raw_mode = True
        self.raw.setText(text)
        self._update()

    def get_rows(self):
        return [(e.currentData(), i.currentData()) for e, i, _ in self.rows]


class Section(QGroupBox):
    def __init__(self, title):
        super().__init__(title)
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)


# ---------------------------------------------------------------------------
# per-user settings (description, autoconnect, always up, preferences)
# ---------------------------------------------------------------------------

XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
SETTINGS_PATH = os.path.join(XDG_CONFIG, "swangui", "settings.json")
AUTOSTART_PATH = os.path.join(XDG_CONFIG, "autostart", "swangui.desktop")
AUTOSTART_ENTRY = """[Desktop Entry]
Type=Application
Name=SwanGUI
Comment=Cliente VPN IPsec (strongSwan)
Exec=swangui
Icon=swangui
X-GNOME-Autostart-enabled=true
"""


class Settings:
    def __init__(self):
        self.data = {"notifications": True, "connections": {}}
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_PATH)

    def conn(self, name):
        return self.data["connections"].get(name, {})

    def set_conn(self, name, meta, old_name=None):
        if old_name and old_name != name:
            self.data["connections"].pop(old_name, None)
        self.data["connections"][name] = meta
        self.save()

    def remove(self, name):
        self.data["connections"].pop(name, None)
        self.save()

    @property
    def autostart(self):
        return os.path.exists(AUTOSTART_PATH)

    @autostart.setter
    def autostart(self, on):
        if on:
            os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
            with open(AUTOSTART_PATH, "w", encoding="utf-8") as f:
                f.write(AUTOSTART_ENTRY)
        elif os.path.exists(AUTOSTART_PATH):
            os.unlink(AUTOSTART_PATH)


# ---------------------------------------------------------------------------
# password-protected exports: scrypt + AES-256-GCM
# ---------------------------------------------------------------------------

ENC_BEGIN = "-----BEGIN SWANGUI ENCRYPTED EXPORT-----"
ENC_END = "-----END SWANGUI ENCRYPTED EXPORT-----"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def crypto_available():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except ImportError:
        return False


def encrypt_export(text, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    key = hashlib.scrypt(passphrase.encode(), salt=salt, **SCRYPT)
    ct = AESGCM(key).encrypt(nonce, text.encode(), ENC_BEGIN.encode())
    body = base64.b64encode(b"\x01" + salt + nonce + ct).decode()
    return (tr("{0}\n# Exportación de SwanGUI protegida con contraseña (scrypt + AES-256-GCM).\n# Impórtala desde SwanGUI: Archivo → Importar.\n\n").format(ENC_BEGIN)
            + "\n".join(textwrap.wrap(body, 64)) + f"\n{ENC_END}\n")


def is_encrypted_export(text):
    return text.lstrip().startswith(ENC_BEGIN)


def decrypt_export(text, passphrase):
    """Plain text of an encrypted export; ValueError if the password is wrong or the file damaged."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        inner = text.split(ENC_BEGIN, 1)[1].split(ENC_END, 1)[0]
        body = "".join(l.strip() for l in inner.splitlines() if l.strip() and not l.startswith("#"))
        raw = base64.b64decode(body, validate=True)
    except (IndexError, ValueError):
        raise ValueError(tr("El archivo cifrado está dañado"))
    if len(raw) < 1 + 16 + 12 + 16 or raw[0] != 1:
        raise ValueError(tr("Formato de archivo cifrado desconocido"))
    salt, nonce, ct = raw[1:17], raw[17:29], raw[29:]
    key = hashlib.scrypt(passphrase.encode(), salt=salt, **SCRYPT)
    try:
        return AESGCM(key).decrypt(nonce, ct, ENC_BEGIN.encode()).decode()
    except InvalidTag:
        raise ValueError(tr("Contraseña incorrecta"))


# ---------------------------------------------------------------------------
# FortiClient configuration files (XML backup / EMS profile)
# ---------------------------------------------------------------------------

FORTI_ENC = {"aes128gcm": "aes128gcm16", "aes256gcm": "aes256gcm16", "aes128-gcm": "aes128gcm16",
             "aes256-gcm": "aes256gcm16", "chacha20poly1305": "chacha20poly1305"}
FORTI_ENC_OUT = {"aes128gcm16": "AES128GCM", "aes256gcm16": "AES256GCM",
                 "chacha20poly1305": "CHACHA20POLY1305"}
DH_BY_NUMBER = {label: val for val, label in DH_GROUPS}
NUMBER_BY_DH = {val: label for val, label in DH_GROUPS}


def _safe_name(text):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip()).strip("_.-")[:64]
    return name or "vpn"


def _forti_secret(text):
    """FortiClient stores secrets as 'Enc <hex>' — those can't be recovered."""
    text = (text or "").strip()
    return "" if text.lower().startswith("enc ") else text


def _forti_rows(proposals, ike):
    rows, bad = [], []
    for p in proposals:
        p = (p or "").strip()
        if not p:
            continue
        enc, _, integ = p.partition("|")
        e = enc.strip().lower()
        e = FORTI_ENC.get(e, e)
        i = integ.strip().lower()
        if i.startswith("prf"):
            i = i[3:]
        i = INTEG_ALIASES.get(i, i)
        if e in dict(ENCRYPTIONS) and (i in dict(INTEGRITIES) or (e in AEAD and not i)):
            rows.append((e, i or "sha256"))
        else:
            bad.append(p)
    return rows, bad


def forti_import(text):
    """Parse a FortiClient XML config. Returns (list of (values, meta), warnings)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(tr("No es un XML válido: {0}").format(e))
    conns = root.findall(".//ipsecvpn/connections/connection")
    if not conns:
        raise ValueError(tr("El archivo no contiene conexiones IPsec de FortiClient (forticlient_configuration/vpn/ipsecvpn/connections)."))
    result, warnings = [], []

    def t(el, path, default=""):
        if el is None:
            return default
        r = el.findtext(path)
        return r.strip() if r and r.strip() else default

    for c in conns:
        raw_name = t(c, "name", "vpn")
        name = _safe_name(raw_name)
        ike = c.find("ike_settings")
        ips = c.find("ipsec_settings")
        version = t(ike, "version") or t(c, "version") or "1"
        version = "2" if version == "2" else "1"
        server = t(ike, "server")
        if not server:
            warnings.append(tr("«{0}»: no tiene gateway remoto; se omite.").format(raw_name))
            continue
        method = t(ike, "authentication_method", "Preshared Key").lower()
        cert = "cert" in method or "x509" in method or "smartcard" in method
        psk = _forti_secret(t(ike, "auth_data/preshared_key") or t(ike, "preshared_key"))
        if not cert and not psk:
            warnings.append(tr("«{0}»: la clave precompartida está cifrada por FortiClient; edita la conexión y escríbela.").format(raw_name))
        xauth_on = t(ike, "xauth/enabled") == "1" or t(ike, "eap/enabled") == "1"
        prompt = t(ike, "xauth/prompt_username") == "1"
        username = _forti_secret(t(ike, "xauth/username"))
        password = _forti_secret(t(ike, "xauth/password"))
        mode = "none"
        if xauth_on:
            mode = "save" if (password and not prompt) else "prompt"
            if not prompt and not password:
                warnings.append(tr("«{0}»: la contraseña de usuario está cifrada; se pedirá al conectar.").format(raw_name))
            if not username and t(ike, "xauth/username").lower().startswith("enc "):
                warnings.append(tr("«{0}»: el usuario está cifrado; edita la conexión y escríbelo.").format(raw_name))

        dhs = [DH_BY_NUMBER[n] for n in re.split(r"[;,\s]+", t(ike, "dhgroup", "14"))
               if n in DH_BY_NUMBER] or ["modp2048"]
        ike_rows, bad1 = _forti_rows([p.text for p in (ike.findall("proposals/proposal") if ike is not None else [])], True)
        esp_rows, bad2 = _forti_rows([p.text for p in (ips.findall("proposals/proposal") if ips is not None else [])], False)
        for b in bad1 + bad2:
            warnings.append(tr("«{0}»: propuesta no soportada «{1}» omitida.").format(raw_name, b))
        ike_rows = ike_rows or [("aes256", "sha256"), ("aes128", "sha256")]
        esp_rows = esp_rows or [("aes256", "sha256"), ("aes128", "sha256")]
        pfs = t(ips, "pfs", "1") == "1"
        pfs_group = DH_BY_NUMBER.get(t(ips, "dhgroup", "14").split(";")[0], "modp2048")

        nets = []
        for n in (ips.findall("remote_networks/network") if ips is not None else []):
            addr, mask = t(n, "addr"), t(n, "mask", "255.255.255.255")
            try:
                nets.append(str(ipaddress.ip_network(f"{addr}/{mask}", strict=False)))
            except ValueError:
                warnings.append(tr("«{0}»: red remota inválida {1}/{2} omitida.").format(raw_name, addr, mask))
        if not nets or "0.0.0.0/0" in nets:
            nets = ["0.0.0.0/0"]

        life_type = t(ips, "key_life_type", "seconds").lower()
        p2_secs = t(ips, "key_life_seconds") if life_type in ("seconds", "both") else ""
        p2_kb = t(ips, "key_life_Kbytes") if life_type in ("kbytes", "both") else ""
        mode_cfg = t(ike, "mode_config", "1") == "1" or t(ips, "virtualip/type") == "modeconfig"
        user_auth = mode != "none"
        eap = version == "2" and user_auth
        local_auth = "eap-mschapv2" if eap else ("pubkey" if cert else "psk")

        values = {
            "orig_name": "", "auto_rename": True, "name": name,
            "remote_addrs": server, "local_addrs": "", "version": version,
            "vips": mode_cfg,
            "aggressive": version == "1" and t(ike, "mode").lower() == "aggressive",
            "proposals": build_proposals(ike_rows, dhs, True),
            "esp_proposals": build_proposals(esp_rows, [pfs_group] if pfs else [], False),
            "rekey_time": f"{t(ike, 'key_life')}s" if t(ike, "key_life").isdigit() else "",
            "child_rekey_time": f"{p2_secs}s" if p2_secs.isdigit() else "",
            "child_rekey_bytes": f"{p2_kb}k" if p2_kb.isdigit() else "",
            "replay_window": "" if t(ips, "replay_detection", "1") == "1" else "0",
            "dpd_delay": "30s" if t(ike, "dpd", "1") == "1" else "",
            "dpd_action": "restart" if t(ike, "dpd", "1") == "1" else "",
            "encap": False, "mobike": True,
            "fragmentation": t(ike, "enable_ike_fragmentation", "1") == "1",
            "local_auth": local_auth,
            "local_id": t(ike, "localid"),
            "eap_id": username if eap else "",
            "local_certs": "",
            "remote_auth": "pubkey" if cert else "psk",
            "remote_id": "%any",
            "xauth": version == "1" and user_auth,
            "xauth_id": username if (version == "1" and user_auth) else "",
            "child_name": name, "local_ts": "", "remote_ts": ",".join(nets),
            "start_action": "",
            "psk": psk if not cert else "",
            "eap_password": password if (eap and mode == "save") else "",
            "xauth_password": password if (version == "1" and mode == "save") else "",
        }
        meta = {"description": t(c, "description")}
        result.append((values, meta))
    return result, warnings


def forti_export(items):
    """items: [(view, secrets_or_None, meta)] -> FortiClient XML text."""
    root = ET.Element("forticlient_configuration")
    conns = ET.SubElement(ET.SubElement(ET.SubElement(root, "vpn"), "ipsecvpn"), "connections")

    def sub(parent, tag, text=None):
        el = ET.SubElement(parent, tag)
        if text is not None:
            el.text = str(text)
        return el

    for view, secrets, meta in items:
        secrets = secrets or {}
        c = sub(conns, "connection")
        sub(c, "name", view["name"])
        sub(c, "description", meta.get("description", ""))
        sub(c, "type", "manual")
        ike = sub(c, "ike_settings")
        sub(ike, "version", view["version"] if view["version"] in ("1", "2") else "2")
        sub(ike, "server", view["remote_addrs"].split(",")[0].strip())
        cert = view["remote_auth"] != "psk"
        sub(ike, "authentication_method", "X509 Certificate" if cert else "Preshared Key")
        sub(ike, "mode", "aggressive" if view.get("aggressive") else "main")
        ike_parsed = parse_proposals(view["proposals"]) or ([], [])
        sub(ike, "dhgroup", ";".join(NUMBER_BY_DH[d] for d in ike_parsed[1]) or "14")
        sub(ike, "key_life", parse_seconds(view.get("rekey_time")) or 86400)
        sub(ike, "localid", view["local_id"])
        sub(ike, "nat_traversal", "1")
        sub(ike, "mode_config", "1" if view["vips"] else "0")
        sub(ike, "dpd", "1" if view["dpd_delay"] else "0")
        sub(ike, "enable_ike_fragmentation", "1" if view.get("fragmentation", True) else "0")
        user_auth = view["local_auth"].startswith("eap") or view.get("xauth")
        user = view["xauth_id"] if view.get("xauth") else (view["eap_id"] or view["local_id"])
        x = sub(ike, "xauth")
        sub(x, "enabled", "1" if user_auth else "0")
        pw = secrets.get("password", "")
        sub(x, "prompt_username", "0" if pw else ("1" if user_auth else "0"))
        sub(x, "username", user if user_auth else "")
        sub(x, "password", pw)
        sub(sub(ike, "auth_data"), "preshared_key", secrets.get("psk", "") if not cert else "")
        props = sub(ike, "proposals")
        for e, i in ike_parsed[0]:
            sub(props, "proposal", f"{FORTI_ENC_OUT.get(e, e.upper())}|"
                                   f"{'PRF' if e in AEAD else ''}{i.upper()}")

        ips = sub(c, "ipsec_settings")
        nets = sub(ips, "remote_networks")
        for cidr in filter(None, (s.strip() for s in view["remote_ts"].split(","))):
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            if net.version != 4:
                continue
            n = sub(nets, "network")
            sub(n, "addr", net.network_address)
            sub(n, "mask", net.netmask)
        esp_parsed = parse_proposals(view["esp_proposals"]) or ([], [])
        sub(ips, "dhgroup", NUMBER_BY_DH.get(esp_parsed[1][0], "14") if esp_parsed[1] else "14")
        secs = parse_seconds(view.get("child_rekey_time"))
        kb = parse_bytes_kb(view.get("child_rekey_bytes"))
        sub(ips, "key_life_type", "both" if secs and kb else ("kbytes" if kb else "seconds"))
        sub(ips, "key_life_seconds", secs or 43200)
        sub(ips, "key_life_Kbytes", kb or 5120)
        sub(ips, "replay_detection", "0" if view.get("replay_window") == "0" else "1")
        sub(ips, "pfs", "1" if esp_parsed[1] else "0")
        sub(ips, "use_vip", "1" if view["vips"] else "0")
        props = sub(ips, "proposals")
        for e, i in esp_parsed[0]:
            sub(props, "proposal", f"{FORTI_ENC_OUT.get(e, e.upper())}|"
                                   f"{'' if e in AEAD else i.upper()}")
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------------------
# other vendors: the same connection written as another device's or client's
# configuration. Importers return ([(values, meta)], warnings) like
# forti_import; exporters take [(view, secrets_or_None, meta)] and return
# (text, warnings).
# ---------------------------------------------------------------------------

DEFAULT_IKE = ([("aes256", "sha256"), ("aes128", "sha256")], ["modp2048"])
DEFAULT_ESP = ([("aes256", "sha256"), ("aes128", "sha256")], [])


def blank_values(name, server):
    name = _safe_name(name)
    return {"orig_name": "", "auto_rename": True, "name": name, "remote_addrs": server,
            "local_addrs": "", "version": "2", "vips": True, "aggressive": False,
            "proposals": "", "esp_proposals": "", "rekey_time": "", "child_rekey_time": "",
            "child_rekey_bytes": "", "replay_window": "", "dpd_delay": "30s", "dpd_action": "restart",
            "encap": False, "mobike": True, "fragmentation": True, "local_auth": "psk", "local_id": "",
            "eap_id": "", "local_certs": "", "remote_auth": "psk", "remote_id": "%any", "xauth": False,
            "xauth_id": "", "child_name": name, "local_ts": "", "remote_ts": "0.0.0.0/0",
            "start_action": "", "psk": "", "eap_password": "", "xauth_password": "", "user_mode": "none"}


def set_user_auth(values, user, password, cert=False, eap="eap-mschapv2"):
    """User authentication as the editor writes it: EAP on IKEv2, XAuth on IKEv1."""
    values["remote_auth"] = "pubkey" if cert else "psk"
    values["local_auth"] = "pubkey" if cert else "psk"
    if not user and not password:
        values["user_mode"] = "none"
        return
    if values["version"] == "2":
        values.update(local_auth=eap, eap_id=user, eap_password=password)
    else:
        values.update(xauth=True, xauth_id=user, xauth_password=password)
    values["user_mode"] = "save" if password else "prompt"
    values["clear_user_secret"] = not password


def set_proposals(values, ike, esp):
    """ike/esp: (rows, dhs). Empty rows keep strongSwan's defaults."""
    if ike[0]:
        values["proposals"] = build_proposals(ike[0], ike[1] or ["modp2048"], True)
    if esp[0]:
        values["esp_proposals"] = build_proposals(esp[0], esp[1], False)


def view_proposals(view):
    ike = parse_proposals(view.get("proposals", "")) or DEFAULT_IKE
    esp = parse_proposals(view.get("esp_proposals", "")) or DEFAULT_ESP
    return (ike if ike[0] else DEFAULT_IKE), (esp if esp[0] else DEFAULT_ESP)


def view_user(view):
    """(user, user authentication on?) of a connection view."""
    if view.get("xauth"):
        return view.get("xauth_id", ""), True
    if view["local_auth"].startswith("eap") and view["local_auth"] != "eap-tls":
        return view.get("eap_id") or view.get("local_id", ""), True
    return "", False


def view_nets(view, version=4):
    nets = []
    for cidr in filter(None, (s.strip() for s in view.get("remote_ts", "").split(","))):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if version is None or net.version == version:
            nets.append(net)
    return nets or [ipaddress.ip_network("0.0.0.0/0")]


def is_full_tunnel(view):
    return any(n.prefixlen == 0 for n in view_nets(view, None))


def nets_text(nets):
    nets = [str(ipaddress.ip_network(n, strict=False)) for n in nets]
    return "0.0.0.0/0" if not nets or "0.0.0.0/0" in nets else ",".join(dict.fromkeys(nets))


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def human_lifetime(secs, style="ros"):
    secs = int(secs or 0)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs and secs % n == 0:
            return f"{secs // n}{unit}"
    return f"{secs}s"


def ros_seconds(text):
    """RouterOS time: '1d', '8h', '30m', '00:30:00', '1d 02:00:00', '1w2d'."""
    text = (text or "").strip()
    total = 0
    for num, unit in re.findall(r"(\d+)([wdhms])", text):
        total += int(num) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    m = re.search(r"(\d+):(\d+):(\d+)", text)
    if m:
        total += int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    if not total and text.isdigit():
        total = int(text)
    return total


def _rows(encs, integs):
    rows = []
    for e in encs:
        for i in (["sha256"] if e in AEAD else (integs or ["sha256"])):
            if (e, i) not in rows:
                rows.append((e, i))
    return rows


# --- MikroTik RouterOS -------------------------------------------------------

ROS_IKE_ENC = {"aes128": "aes-128", "aes192": "aes-192", "aes256": "aes-256", "3des": "3des",
               "aes128gcm16": "aes-128-gcm", "aes256gcm16": "aes-256-gcm"}
ROS_ESP_ENC = {"aes128": "aes-128-cbc", "aes192": "aes-192-cbc", "aes256": "aes-256-cbc", "3des": "3des",
               "aes128gcm16": "aes-128-gcm", "aes256gcm16": "aes-256-gcm"}
ROS_HASH = {"sha1": "sha1", "sha256": "sha256", "sha384": "sha384", "sha512": "sha512", "md5": "md5"}
ROS_ESP_AUTH = {"sha1": "sha1", "sha256": "sha256", "sha512": "sha512", "md5": "md5"}
ROS_DH = {d: d for d in ("modp768", "modp1024", "modp1536", "modp2048", "modp3072", "modp4096",
                         "modp8192", "ecp256", "ecp384", "ecp521")}


def _ros_q(value):
    value = str(value)
    if value and re.fullmatch(r"[\w.:@/,+-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _ros_id(value):
    if not value or value == "%any":
        return ""
    if _is_ip(value):
        return f"address:{value}"
    return f"user-fqdn:{value}" if "@" in value else f"fqdn:{value}"


def mikrotik_export(items):
    out = [f"# SwanGUI {time.strftime('%Y-%m-%d %H:%M')} — MikroTik RouterOS 7"]
    warnings = []
    for view, secrets, meta in items:
        secrets = secrets or {}
        name = view["name"]
        q = _ros_q(name)
        (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
        encs = [ROS_IKE_ENC[e] for e, _ in ike_rows if e in ROS_IKE_ENC]
        hashes = [ROS_HASH[i] for _, i in ike_rows if i in ROS_HASH]
        dh = [ROS_DH[d] for d in dhs if d in ROS_DH]
        esp_encs = [ROS_ESP_ENC[e] for e, _ in esp_rows if e in ROS_ESP_ENC]
        esp_auth = [ROS_ESP_AUTH[i] for e, i in esp_rows if i in ROS_ESP_AUTH]
        if len(encs) < len(ike_rows) or len(dh) < len(dhs) or len(esp_encs) < len(esp_rows):
            warnings.append(tr("«{0}»: algunos algoritmos no existen en RouterOS y se omitieron.").format(name))
        v2 = view["version"] == "2"
        out += ["", f"# {name}" + (f" — {meta.get('description')}" if meta.get("description") else ""),
                "/ip ipsec profile",
                f"add name={q} enc-algorithm={','.join(dict.fromkeys(encs)) or 'aes-256'} "
                f"hash-algorithm={(hashes or ['sha256'])[0]} dh-group={','.join(dh) or 'modp2048'} "
                f"lifetime={human_lifetime(parse_seconds(view.get('rekey_time')) or 86400)} nat-traversal=yes "
                + (f"dpd-interval={view['dpd_delay']}" if view.get("dpd_delay") else "dpd-interval=disable-dpd"),
                "/ip ipsec proposal",
                f"add name={q} enc-algorithms={','.join(dict.fromkeys(esp_encs)) or 'aes-256-cbc'} "
                f"auth-algorithms={','.join(dict.fromkeys(esp_auth)) or 'sha256'} "
                f"pfs-group={ROS_DH.get(pfs[0], 'none') if pfs else 'none'} "
                f"lifetime={human_lifetime(parse_seconds(view.get('child_rekey_time')) or 3600)}",
                "/ip ipsec policy group", f"add name={q}"]
        if view["vips"]:
            out += ["/ip ipsec mode-config", f"add name={q} responder=no"]
        mode = "ike2" if v2 else ("aggressive" if view.get("aggressive") else "main")
        out += ["/ip ipsec peer", f"add name={q} address={view['remote_addrs'].split(',')[0].strip()} "
                f"exchange-mode={mode} profile={q}"]
        user, user_on = view_user(view)
        pw = secrets.get("password", "")
        ident = [f"add peer={q}"]
        if view["remote_auth"] == "psk" or view["local_auth"] == "psk":
            if user_on and not v2:
                ident.append("auth-method=pre-shared-key-xauth")
                ident.append(f"xauth-login={_ros_q(user)}")
                if pw:
                    ident.append(f"xauth-password={_ros_q(pw)}")
            elif user_on:
                ident += ["auth-method=eap", "eap-methods=eap-mschapv2", f"username={_ros_q(user)}"]
                if pw:
                    ident.append(f"password={_ros_q(pw)}")
            else:
                ident.append("auth-method=pre-shared-key")
            if secrets.get("psk") and not (user_on and v2):
                ident.append(f"secret={_ros_q(secrets['psk'])}")
        else:
            ident += ["auth-method=digital-signature",
                      f"certificate={_ros_q(view.get('local_certs', '').split(',')[0].strip() or 'client')}"]
        if user_on and v2 and view["remote_auth"] == "psk":
            warnings.append(tr("«{0}»: RouterOS no combina EAP con clave precompartida; el router debe verificar "
                               "el certificado del servidor.").format(name))
        if _ros_id(view.get("local_id")):
            ident.append(f"my-id={_ros_q(_ros_id(view['local_id']))}")
        if _ros_id(view.get("remote_id")):
            ident.append(f"remote-id={_ros_q(_ros_id(view['remote_id']))}")
        ident += ["generate-policy=port-strict", f"policy-template-group={q}"]
        if view["vips"]:
            ident.append(f"mode-config={q}")
        out += ["/ip ipsec identity", " ".join(ident), "/ip ipsec policy"]
        for net in view_nets(view):
            out.append(f"add group={q} template=yes src-address=0.0.0.0/0 dst-address={net} proposal={q}")
    return "\n".join(out) + "\n", warnings


def _ros_parse(text):
    text = re.sub(r"\\\r?\n\s*", "", text)
    sections, path = {}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            m = re.match(r"^(/[^=\[]+?)\s+(add|set)\b(.*)$", line)
            if not m:
                path = " ".join(line.split())
                continue
            path, cmd, rest = " ".join(m.group(1).split()), m.group(2), m.group(3)
        else:
            m = re.match(r"^(add|set)\b(.*)$", line)
            if not m:
                continue
            cmd, rest = m.groups()
        kv = {k: (re.sub(r'\\(.)', r"\1", v[1:-1]) if v.startswith('"') else v)
              for k, v in re.findall(r'([\w-]+)=("(?:[^"\\]|\\.)*"|\S+)', rest)}
        if cmd == "set":
            if "default=yes" not in rest.replace(" ", ""):
                continue
            kv.setdefault("name", "default")
        sections.setdefault(path, []).append(kv)
    return sections


def _ros_id_value(text):
    return text.split(":", 1)[1] if ":" in text and text.split(":", 1)[0] in (
        "address", "fqdn", "user-fqdn", "key-id", "auto") else text


def mikrotik_import(text):
    s = _ros_parse(text)
    peers = s.get("/ip ipsec peer", [])
    if not peers:
        raise ValueError(tr("El archivo no contiene pares IPsec de RouterOS (/ip ipsec peer)."))
    by_name = lambda sect, n: next((x for x in s.get(sect, []) if x.get("name") == n), {})
    rev_ike = {v: k for k, v in ROS_IKE_ENC.items()}
    rev_esp = {v: k for k, v in ROS_ESP_ENC.items()}
    result, warnings = [], []
    for peer in peers:
        server = re.sub(r"/32$", "", peer.get("address", "")).strip()
        name = peer.get("name") or server
        if not server or server.startswith("0.0.0.0"):
            warnings.append(tr("«{0}»: no tiene gateway remoto; se omite.").format(name))
            continue
        values = blank_values(name, server)
        mode = peer.get("exchange-mode", "main")
        values["version"] = "2" if mode == "ike2" else "1"
        values["aggressive"] = mode == "aggressive"
        prof = by_name("/ip ipsec profile", peer.get("profile", "default"))
        encs = [rev_ike.get(e) for e in prof.get("enc-algorithm", "aes-128").split(",")]
        if None in encs:
            warnings.append(tr("«{0}»: algunos algoritmos no están soportados y se omitieron.").format(name))
        integs = [i for i in prof.get("hash-algorithm", "sha1").split(",") if i in ROS_HASH]
        dhs = [d for d in prof.get("dh-group", "modp2048,modp1024").split(",") if d in ROS_DH]
        values["rekey_time"] = f"{ros_seconds(prof.get('lifetime', '1d'))}s"
        dpd = prof.get("dpd-interval", "8s")
        values["dpd_delay"] = "" if dpd == "disable-dpd" else f"{ros_seconds(dpd)}s"
        ident = next((x for x in s.get("/ip ipsec identity", []) if x.get("peer") == peer.get("name")), {})
        method = ident.get("auth-method", "pre-shared-key")
        cert = method in ("digital-signature", "rsa-signature", "rsa-key")
        values["psk"] = ident.get("secret", "")
        if method in ("pre-shared-key", "pre-shared-key-xauth") and not values["psk"]:
            warnings.append(tr("«{0}»: el archivo no trae la clave precompartida (exporta con «show-sensitive») "
                               "o escríbela al editar.").format(name))
        if method == "pre-shared-key-xauth":
            set_user_auth(values, ident.get("xauth-login", ""), ident.get("xauth-password", ""))
        elif method == "eap":
            set_user_auth(values, ident.get("username", ""), ident.get("password", ""), cert=True,
                          eap="eap-tls" if "eap-tls" in ident.get("eap-methods", "") else "eap-mschapv2")
        else:
            set_user_auth(values, "", "", cert=cert)
        values["local_id"] = _ros_id_value(ident.get("my-id", ""))
        values["remote_id"] = _ros_id_value(ident.get("remote-id", "")) or "%any"
        values["vips"] = bool(ident.get("mode-config"))
        group = ident.get("policy-template-group", "default")
        policies = [p for p in s.get("/ip ipsec policy", [])
                    if p.get("peer") == peer.get("name") or (p.get("template") == "yes" and p.get("group") == group
                                                          and group != "default")]
        nets = [p["dst-address"] for p in policies if p.get("dst-address")]
        prop = by_name("/ip ipsec proposal", (policies[0].get("proposal") if policies else None) or "default")
        esp_encs = [rev_esp.get(e) for e in prop.get("enc-algorithms", "aes-256-cbc,aes-128-cbc").split(",")]
        esp_auth = [a for a in prop.get("auth-algorithms", "sha1").split(",") if a in ROS_ESP_AUTH]
        pfs = prop.get("pfs-group", "modp1024")
        set_proposals(values, (_rows([e for e in encs if e], integs), dhs),
                      (_rows([e for e in esp_encs if e], esp_auth), [pfs] if pfs in ROS_DH else []))
        values["child_rekey_time"] = f"{ros_seconds(prop.get('lifetime', '30m'))}s"
        try:
            values["remote_ts"] = nets_text(nets)
        except ValueError:
            warnings.append(tr("«{0}»: redes remotas no válidas; se usa todo el tráfico.").format(name))
        result.append((values, {"description": peer.get("comment", "")}))
    return result, warnings


# --- pfSense / OPNsense (config.xml, legacy IPsec section) --------------------

PF_ENC = {"aes128": ("aes", "128"), "aes192": ("aes", "192"), "aes256": ("aes", "256"),
          "aes128gcm16": ("aes128gcm", "128"), "aes256gcm16": ("aes256gcm", "256"),
          "chacha20poly1305": ("chacha20poly1305", ""), "3des": ("3des", "")}


def _pf_enc(name, keylen):
    name, keylen = (name or "").lower(), (keylen or "").strip()
    if name == "aes":
        return {"128": "aes128", "192": "aes192", "256": "aes256"}.get(keylen, "aes128")
    if name in ("aes128gcm", "aes256gcm"):
        return f"aes{keylen or name[3:6]}gcm16" if keylen in ("128", "256", "") else None
    return {"3des": "3des", "chacha20poly1305": "chacha20poly1305"}.get(name)


def pfsense_export(items):
    root = ET.Element("ipsec")
    warnings = []

    def sub(parent, tag, text=None):
        el = ET.SubElement(parent, tag)
        if text is not None:
            el.text = str(text)
        return el

    for n, (view, secrets, meta) in enumerate(items, 1):
        secrets = secrets or {}
        p1 = sub(root, "phase1")
        sub(p1, "ikeid", n)
        sub(p1, "iketype", "ikev2" if view["version"] == "2" else "ikev1")
        if view["version"] != "2":
            sub(p1, "mode", "aggressive" if view.get("aggressive") else "main")
        sub(p1, "interface", "wan")
        sub(p1, "remote-gateway", view["remote_addrs"].split(",")[0].strip())
        sub(p1, "protocol", "inet")
        lid = view.get("local_id", "")
        sub(p1, "myid_type", "myaddress" if not lid else ("address" if _is_ip(lid) else
                                                           ("user_fqdn" if "@" in lid else "fqdn")))
        sub(p1, "myid_data", lid)
        rid = view.get("remote_id", "")
        sub(p1, "peerid_type", "peeraddress" if rid in ("", "%any") else ("address" if _is_ip(rid) else "fqdn"))
        sub(p1, "peerid_data", "" if rid == "%any" else rid)
        (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
        enc = sub(p1, "encryption")
        for e, i in ike_rows:
            for dh in dhs:
                item = sub(enc, "item")
                ea = sub(item, "encryption-algorithm")
                sub(ea, "name", PF_ENC.get(e, ("aes", "256"))[0])
                sub(ea, "keylen", PF_ENC.get(e, ("aes", "256"))[1])
                sub(item, "hash-algorithm", i)
                sub(item, "dhgroup", NUMBER_BY_DH.get(dh, "14"))
        sub(p1, "lifetime", parse_seconds(view.get("rekey_time")) or 28800)
        cert = view["remote_auth"] != "psk"
        sub(p1, "authentication_method", "cert" if cert else "pre_shared_key")
        sub(p1, "pre-shared-key", secrets.get("psk", "") if not cert else "")
        sub(p1, "descr", meta.get("description") or view["name"])
        sub(p1, "nat_traversal", "on")
        sub(p1, "mobike", "on" if view.get("mobike", True) else "off")
        if view.get("dpd_delay"):
            sub(p1, "dpd_delay", parse_seconds(view["dpd_delay"]) or 30)
            sub(p1, "dpd_maxfail", 5)
        if view_user(view)[1]:
            warnings.append(tr("«{0}»: pfSense/OPNsense no se autentican como usuario (EAP/XAuth) en un túnel; "
                               "se exporta solo la clave o el certificado.").format(view["name"]))
        if view["vips"]:
            warnings.append(tr("«{0}»: el túnel de pfSense/OPNsense no recibe IP virtual; revisa la red local "
                               "de la fase 2.").format(view["name"]))
        for net in view_nets(view):
            p2 = sub(root, "phase2")
            sub(p2, "ikeid", n)
            sub(p2, "uniqid", hashlib.sha1(f"{view['name']}{net}".encode()).hexdigest()[:13])
            sub(p2, "mode", "tunnel")
            sub(sub(p2, "localid"), "type", "lan")
            rem = sub(p2, "remoteid")
            sub(rem, "type", "network")
            sub(rem, "address", net.network_address)
            sub(rem, "netbits", net.prefixlen)
            sub(p2, "protocol", "esp")
            hashes = []
            for e, i in esp_rows:
                opt = sub(p2, "encryption-algorithm-option")
                sub(opt, "name", PF_ENC.get(e, ("aes", "256"))[0])
                sub(opt, "keylen", PF_ENC.get(e, ("aes", "256"))[1] or "auto")
                if e not in AEAD and f"hmac_{i}" not in hashes:
                    hashes.append(f"hmac_{i}")
            for h in hashes:
                sub(p2, "hash-algorithm-option", h)
            sub(p2, "pfsgroup", NUMBER_BY_DH.get(pfs[0], "0") if pfs else "0")
            sub(p2, "lifetime", parse_seconds(view.get("child_rekey_time")) or 3600)
            sub(p2, "descr", view["name"])
    ET.indent(root)
    text = ("<?xml version=\"1.0\"?>\n<!-- SwanGUI: " + tr("pega la sección <ipsec> en config.xml o "
            "restáurala desde Diagnóstico → Copia de seguridad (área IPsec)") + " -->\n"
            + ET.tostring(root, encoding="unicode") + "\n")
    return text, warnings


def pfsense_import(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(tr("No es un XML válido: {0}").format(e))
    ipsec = root if root.tag == "ipsec" else root.find(".//ipsec")
    phase1s = ipsec.findall("phase1") if ipsec is not None else []
    if not phase1s:
        raise ValueError(tr("El archivo no contiene túneles IPsec de pfSense/OPNsense (<ipsec><phase1>)."))
    t = lambda el, path, d="": ((el.findtext(path) or "").strip() or d) if el is not None else d
    result, warnings = [], []
    for p1 in phase1s:
        if p1.find("disabled") is not None or p1.find("mobile") is not None:
            continue
        name = t(p1, "descr") or t(p1, "remote-gateway") or "pfsense"
        server = t(p1, "remote-gateway")
        if not server:
            warnings.append(tr("«{0}»: no tiene gateway remoto; se omite.").format(name))
            continue
        values = blank_values(name, server)
        values["version"] = "1" if t(p1, "iketype", "ikev2") == "ikev1" else "2"
        values["aggressive"] = t(p1, "mode") == "aggressive"
        values["vips"] = False
        items = p1.findall("encryption/item") or [p1]
        encs, integs, dhs = [], [], []
        for it in items:
            e = _pf_enc(t(it, "encryption-algorithm/name"), t(it, "encryption-algorithm/keylen"))
            if e and e not in encs:
                encs.append(e)
            h = t(it, "hash-algorithm", "sha256")
            if h in dict(INTEGRITIES) and h not in integs:
                integs.append(h)
            dh = DH_BY_NUMBER.get(t(it, "dhgroup", "14"))
            if dh and dh not in dhs:
                dhs.append(dh)
        method = t(p1, "authentication_method", "pre_shared_key")
        cert = method in ("cert", "rsasig", "eap-tls")
        set_user_auth(values, "", "", cert=cert)
        values["psk"] = t(p1, "pre-shared-key") if not cert else ""
        if method not in ("pre_shared_key", "cert", "rsasig"):
            warnings.append(tr("«{0}»: método de autenticación «{1}» convertido a clave/certificado.").format(name, method))
        if t(p1, "myid_type") not in ("myaddress", ""):
            values["local_id"] = t(p1, "myid_data")
        if t(p1, "peerid_type") not in ("peeraddress", "any", ""):
            values["remote_id"] = t(p1, "peerid_data") or "%any"
        values["rekey_time"] = f"{t(p1, 'lifetime', '28800')}s"
        values["dpd_delay"] = f"{t(p1, 'dpd_delay')}s" if t(p1, "dpd_delay") else ""
        values["mobike"] = t(p1, "mobike", "off") == "on"
        nets, esp_encs, esp_auth, pfs, life = [], [], [], [], ""
        for p2 in ipsec.findall("phase2"):
            if t(p2, "ikeid") != t(p1, "ikeid") or p2.find("disabled") is not None:
                continue
            rtype = t(p2, "remoteid/type", "network")
            if rtype == "network":
                nets.append(f"{t(p2, 'remoteid/address')}/{t(p2, 'remoteid/netbits', '32')}")
            elif rtype == "address":
                nets.append(t(p2, "remoteid/address"))
            for opt in p2.findall("encryption-algorithm-option"):
                e = _pf_enc(t(opt, "name"), t(opt, "keylen").replace("auto", ""))
                if e and e not in esp_encs:
                    esp_encs.append(e)
            for h in p2.findall("hash-algorithm-option"):
                h = (h.text or "").replace("hmac_", "").strip()
                if h in dict(INTEGRITIES) and h not in esp_auth:
                    esp_auth.append(h)
            g = DH_BY_NUMBER.get(t(p2, "pfsgroup", "0"))
            if g and g not in pfs:
                pfs.append(g)
            life = life or t(p2, "lifetime")
        set_proposals(values, (_rows(encs, integs), dhs), (_rows(esp_encs, esp_auth), pfs[:1]))
        values["child_rekey_time"] = f"{life}s" if life.isdigit() else ""
        try:
            values["remote_ts"] = nets_text(nets)
        except ValueError:
            warnings.append(tr("«{0}»: redes remotas no válidas; se usa todo el tráfico.").format(name))
        result.append((values, {"description": t(p1, "descr")}))
    if not result:
        raise ValueError(tr("No hay túneles activos que importar (los de clientes móviles no se importan)."))
    return result, warnings


# --- Windows (PowerShell VpnClient module) ------------------------------------

WIN_ENC = {"aes128": "AES128", "aes192": "AES192", "aes256": "AES256", "aes128gcm16": "GCMAES128",
           "aes256gcm16": "GCMAES256", "3des": "DES3"}
WIN_INTEG = {"md5": "MD5", "sha1": "SHA196", "sha256": "SHA256", "sha384": "SHA384"}
WIN_DH = {"modp768": "Group1", "modp1024": "Group2", "modp2048": "Group14", "ecp256": "ECP256",
          "ecp384": "ECP384"}
WIN_PFS = {"modp768": "PFS1", "modp1024": "PFS2", "modp2048": "PFS2048", "ecp256": "ECP256", "ecp384": "ECP384"}
WIN_ESP_AUTH = {"md5": "MD596", "sha1": "SHA196", "sha256": "SHA256128"}


def _ps_q(value):
    return "'" + str(value).replace("'", "''") + "'"


def windows_export(items):
    out = [f"# SwanGUI {time.strftime('%Y-%m-%d %H:%M')} — Windows 10/11 (PowerShell)",
           "# " + tr("Ejecútalo en PowerShell con tu usuario. Las contraseñas no se guardan: Windows las pide al conectar.")]
    warnings = []
    for view, secrets, meta in items:
        name = view["name"]
        user, user_on = view_user(view)
        if view["version"] != "2":
            warnings.append(tr("«{0}»: Windows solo admite IKEv1 con L2TP; no se puede exportar.").format(name))
            continue
        if view["local_auth"] == "psk":
            warnings.append(tr("«{0}»: el cliente IKEv2 de Windows no admite clave precompartida; se exporta "
                               "con certificado de equipo.").format(name))
        method = "Eap" if user_on else "MachineCertificate"
        split = not is_full_tunnel(view)
        out += ["", f"# {name}",
                f"Add-VpnConnection -Name {_ps_q(name)} -ServerAddress {_ps_q(view['remote_addrs'].split(',')[0].strip())} "
                f"-TunnelType Ikev2 -AuthenticationMethod {method} -EncryptionLevel Required "
                f"{'-SplitTunneling ' if split else ''}-RememberCredential -Force"]
        (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
        ike = next(((e, i) for e, i in ike_rows if e in WIN_ENC and i in WIN_INTEG), None)
        dh = next((WIN_DH[d] for d in dhs if d in WIN_DH), None)
        esp = next(((e, i) for e, i in esp_rows if e in WIN_ENC and (e in AEAD or i in WIN_ESP_AUTH)), None)
        if ike and dh and esp:
            auth = WIN_ENC[esp[0]] if esp[0] in AEAD else WIN_ESP_AUTH[esp[1]]
            out.append(f"Set-VpnConnectionIPsecConfiguration -ConnectionName {_ps_q(name)} "
                       f"-EncryptionMethod {WIN_ENC[ike[0]]} -IntegrityCheckMethod {WIN_INTEG[ike[1]]} "
                       f"-DHGroup {dh} -CipherTransformConstants {WIN_ENC[esp[0]]} "
                       f"-AuthenticationTransformConstants {auth} "
                       f"-PfsGroup {WIN_PFS.get(pfs[0], 'None') if pfs else 'None'} -Force")
        else:
            warnings.append(tr("«{0}»: las propuestas no tienen equivalente en Windows; se usan las de Windows "
                               "por defecto.").format(name))
        if split:
            for net in view_nets(view, None):
                out.append(f"Add-VpnConnectionRoute -ConnectionName {_ps_q(name)} -DestinationPrefix {_ps_q(str(net))}")
    return "\n".join(out) + "\n", warnings


def _ps_params(line):
    params = {}
    for m in re.finditer(r"-(\w+)(?::(\$\w+)|\s+('(?:[^']|'')*'|\"[^\"]*\"|[^\s-][^\s]*))?", line):
        key, colon, val = m.group(1).lower(), m.group(2), m.group(3)
        if colon:
            params[key] = colon.lower() != "$false"
        elif val is None:
            params[key] = True
        else:
            params[key] = val[1:-1].replace("''", "'") if val[0] in "'\"" else val
    return params


def windows_import(text):
    text = re.sub(r"`\r?\n\s*", " ", text)
    conns, warnings = {}, []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("add-vpnconnection "):
            p = _ps_params(line[len("Add-VpnConnection "):])
            if p.get("name") and p.get("serveraddress"):
                conns[p["name"]] = {"add": p, "ipsec": {}, "routes": []}
        elif line.lower().startswith("set-vpnconnectionipsecconfiguration "):
            p = _ps_params(line.split(" ", 1)[1])
            if p.get("connectionname") in conns:
                conns[p["connectionname"]]["ipsec"] = p
        elif line.lower().startswith("add-vpnconnectionroute "):
            p = _ps_params(line.split(" ", 1)[1])
            if p.get("connectionname") in conns and p.get("destinationprefix"):
                conns[p["connectionname"]]["routes"].append(p["destinationprefix"])
    if not conns:
        raise ValueError(tr("El archivo no contiene conexiones VPN de Windows (Add-VpnConnection)."))
    rev = lambda d, v: next((k for k, x in d.items() if x.lower() == str(v).lower()), None)
    result = []
    for name, c in conns.items():
        add = c["add"]
        if str(add.get("tunneltype", "Ikev2")).lower() not in ("ikev2", "automatic"):
            warnings.append(tr("«{0}»: tipo de túnel «{1}» no es IPsec IKEv2; se omite.").format(name, add.get("tunneltype")))
            continue
        values = blank_values(name, add["serveraddress"])
        method = str(add.get("authenticationmethod", "Eap")).lower()
        if method in ("eap", "mschapv2"):
            set_user_auth(values, "", "", cert=True)
            values.update(local_auth="eap-mschapv2", user_mode="prompt", clear_user_secret=True)
        else:
            set_user_auth(values, "", "", cert=True)
        ip = c["ipsec"]
        if ip:
            e, i = rev(WIN_ENC, ip.get("encryptionmethod")), rev(WIN_INTEG, ip.get("integritycheckmethod"))
            dh = rev(WIN_DH, ip.get("dhgroup"))
            ce = rev(WIN_ENC, ip.get("ciphertransformconstants"))
            ca = rev(WIN_ESP_AUTH, ip.get("authenticationtransformconstants"))
            pfs = rev(WIN_PFS, ip.get("pfsgroup"))
            set_proposals(values, (_rows([e] if e else [], [i] if i else []), [dh] if dh else []),
                          (_rows([ce] if ce else [], [ca] if ca else []), [pfs] if pfs else []))
        if add.get("splittunneling") and c["routes"]:
            try:
                values["remote_ts"] = nets_text(c["routes"])
            except ValueError:
                warnings.append(tr("«{0}»: redes remotas no válidas; se usa todo el tráfico.").format(name))
        warnings.append(tr("«{0}»: Windows no exporta el usuario ni la contraseña; se pedirán al conectar.").format(name))
        result.append((values, {"description": ""}))
    return result, warnings


# --- Cisco IOS / IOS-XE --------------------------------------------------------

CISCO_IKE_ENC = {"aes128": "aes-cbc-128", "aes192": "aes-cbc-192", "aes256": "aes-cbc-256",
                 "aes128gcm16": "aes-gcm-128", "aes256gcm16": "aes-gcm-256", "3des": "3des"}
CISCO_V1_ENC = {"aes128": "aes 128", "aes192": "aes 192", "aes256": "aes 256", "3des": "3des"}
CISCO_ESP_ENC = {"aes128": "esp-aes 128", "aes192": "esp-aes 192", "aes256": "esp-aes 256",
                 "aes128gcm16": "esp-gcm 128", "aes256gcm16": "esp-gcm 256", "3des": "esp-3des"}
CISCO_ESP_AUTH = {"sha1": "esp-sha-hmac", "sha256": "esp-sha256-hmac", "sha384": "esp-sha384-hmac",
                  "sha512": "esp-sha512-hmac", "md5": "esp-md5-hmac"}


def _wildcard(net):
    return "any" if net.prefixlen == 0 else (f"host {net.network_address}" if net.prefixlen == 32
                                              else f"{net.network_address} {net.hostmask}")


def cisco_export(items):
    out = [f"! SwanGUI {time.strftime('%Y-%m-%d %H:%M')} — Cisco IOS / IOS-XE", "!"]
    warnings = []
    for n, (view, secrets, meta) in enumerate(items, 1):
        secrets = secrets or {}
        name = re.sub(r"[^\w-]", "_", view["name"])
        server = view["remote_addrs"].split(",")[0].strip()
        (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
        user, user_on = view_user(view)
        groups = " ".join(NUMBER_BY_DH[d] for d in dhs if d in NUMBER_BY_DH) or "14"
        life = parse_seconds(view.get("rekey_time")) or 86400
        if not _is_ip(server):
            warnings.append(tr("«{0}»: Cisco necesita la IP del gateway; sustituye «{1}» por su dirección.").format(view["name"], server))
        out.append(f"! {view['name']}")
        if view["version"] == "2":
            encs = " ".join(dict.fromkeys(CISCO_IKE_ENC[e] for e, _ in ike_rows if e in CISCO_IKE_ENC))
            integ = " ".join(dict.fromkeys(i for e, i in ike_rows if e not in AEAD))
            prf = " ".join(dict.fromkeys(i for e, i in ike_rows if e in AEAD))
            out += [f"crypto ikev2 proposal {name}", f" encryption {encs or 'aes-cbc-256'}"]
            if integ:
                out.append(f" integrity {integ}")
            if prf:
                out.append(f" prf {prf}")
            out += [f" group {groups}", f"crypto ikev2 policy {name}", f" proposal {name}",
                    f"crypto ikev2 keyring {name}", f" peer {name}", f"  address {server}"]
            if secrets.get("psk"):
                out.append(f"  pre-shared-key {secrets['psk']}")
            out += [f"crypto ikev2 profile {name}",
                    " match identity remote " + (f"address {server} 255.255.255.255" if view.get("remote_id") in ("", "%any")
                                                 else ("address " + view["remote_id"] if _is_ip(view["remote_id"])
                                                       else "fqdn " + view["remote_id"]))]
            lid = view.get("local_id", "")
            if lid:
                out.append(f" identity local {'address' if _is_ip(lid) else ('email' if '@' in lid else 'fqdn')} {lid}")
            cert = view["remote_auth"] != "psk"
            out.append(f" authentication remote {'rsa-sig' if cert else 'pre-share'}")
            if user_on:
                pw = secrets.get("password", "")
                out.append(f" authentication local eap mschapv2 username {user}" + (f" password {pw}" if pw else ""))
            else:
                out.append(f" authentication local {'rsa-sig' if cert else 'pre-share'}")
            out += [f" keyring local {name}", f" lifetime {life}"]
            if view.get("dpd_delay"):
                out.append(f" dpd {parse_seconds(view['dpd_delay']) or 30} 5 on-demand")
            if view["vips"]:
                out.append(" config-exchange request")
        else:
            row = next(((e, i) for e, i in ike_rows if e in CISCO_V1_ENC), ("aes256", "sha256"))
            out += [f"crypto isakmp policy {n * 10}", f" encryption {CISCO_V1_ENC[row[0]]}",
                    f" hash {'sha' if row[1] == 'sha1' else row[1]}",
                    f" authentication {'rsa-sig' if view['remote_auth'] != 'psk' else 'pre-share'}",
                    f" group {groups.split()[0]}", f" lifetime {life}"]
            if secrets.get("psk"):
                if re.search(r"\s", secrets["psk"]):
                    warnings.append(tr("«{0}»: Cisco IKEv1 no admite espacios en la clave precompartida.").format(view["name"]))
                out.append(f"crypto isakmp key {secrets['psk']} address {server}")
            if user_on:
                warnings.append(tr("«{0}»: XAuth como cliente (Easy VPN) no se exporta; configúralo a mano.").format(view["name"]))
            if view.get("aggressive"):
                out.append(f"crypto isakmp peer address {server}\n set aggressive-mode client-endpoint fqdn "
                           f"{view.get('local_id') or name}")
        esp = next(((e, i) for e, i in esp_rows if e in CISCO_ESP_ENC), ("aes256", "sha256"))
        ts = f"crypto ipsec transform-set {name} {CISCO_ESP_ENC[esp[0]]}"
        if esp[0] not in AEAD:
            ts += f" {CISCO_ESP_AUTH.get(esp[1], 'esp-sha256-hmac')}"
        out += [ts, " mode tunnel", f"ip access-list extended {name}-vpn"]
        for net in view_nets(view):
            out.append(f" permit ip any {_wildcard(net)}")
        out += [f"crypto map {name}-map {n * 10} ipsec-isakmp", f" set peer {server}",
                f" set transform-set {name}"]
        if pfs:
            out.append(f" set pfs group{NUMBER_BY_DH.get(pfs[0], '14')}")
        if view["version"] == "2":
            out.append(f" set ikev2-profile {name}")
        out += [f" set security-association lifetime seconds {parse_seconds(view.get('child_rekey_time')) or 3600}",
                f" match address {name}-vpn", "!",
                "! " + tr("aplica el crypto map a la interfaz de salida: interface X / crypto map {0}-map").format(name), "!"]
    return "\n".join(out) + "\n", warnings


def _cisco_blocks(text):
    blocks, cur = [], None
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("!"):
            continue
        if raw[0] not in " \t":
            cur = [raw.strip(), []]
            blocks.append(cur)
        elif cur is not None:
            cur[1].append(raw.strip())
    return blocks


def _cisco_net(tokens):
    if not tokens:
        return None, []
    if tokens[0] == "any":
        return ipaddress.ip_network("0.0.0.0/0"), tokens[1:]
    if tokens[0] == "host" and len(tokens) > 1:
        return ipaddress.ip_network(f"{tokens[1]}/32"), tokens[2:]
    if len(tokens) > 1:
        try:
            return ipaddress.ip_network(f"{tokens[0]}/{tokens[1]}", strict=False), tokens[2:]
        except ValueError:
            pass
    return None, tokens[1:]


def cisco_import(text):
    blocks = _cisco_blocks(text)
    rev_ike = {v: k for k, v in CISCO_IKE_ENC.items()}
    rev_v1 = {v: k for k, v in CISCO_V1_ENC.items()}
    rev_esp = {v: k for k, v in CISCO_ESP_ENC.items()}
    rev_auth = {v: k for k, v in CISCO_ESP_AUTH.items()}
    proposals, keyring_psk, profiles, tsets, acls, v1 = {}, {}, {}, {}, {}, {"rows": [], "dh": [], "psk": {}}
    maps = []
    for head, body in blocks:
        w = head.split()
        if head.startswith("crypto ikev2 proposal ") and len(w) > 3:
            p = {"enc": [], "integ": [], "dh": []}
            for line in body:
                t = line.split()
                if t[0] == "encryption":
                    p["enc"] += [rev_ike[x] for x in t[1:] if x in rev_ike]
                elif t[0] in ("integrity", "prf"):
                    p["integ"] += [x for x in t[1:] if x in dict(INTEGRITIES)]
                elif t[0] == "group":
                    p["dh"] += [DH_BY_NUMBER[x] for x in t[1:] if x in DH_BY_NUMBER]
            proposals[w[3]] = p
        elif head.startswith("crypto ikev2 keyring "):
            peer_addr = None
            for line in body:
                t = line.split()
                if t[0] == "address" and len(t) > 1:
                    peer_addr = t[1]
                elif t[0] == "pre-shared-key" and peer_addr:
                    # pre-shared-key [local|remote] [0|6] KEY — the key runs to the end of the line
                    rest = line.split(None, 1)[1] if len(t) > 1 else ""
                    rest = re.sub(r"^(local|remote)\s+", "", rest)
                    keyring_psk[peer_addr] = re.sub(r"^0\s+", "", rest)
        elif head.startswith("crypto ikev2 profile ") and len(w) > 3:
            pr = {"local_id": "", "remote_id": "", "life": "", "eap_user": "", "eap_pw": "", "cert": False,
                  "vips": False, "dpd": ""}
            for line in body:
                t = line.split()
                if t[:2] == ["identity", "local"] and len(t) > 3:
                    pr["local_id"] = t[3]
                elif t[:3] == ["match", "identity", "remote"] and len(t) > 4 and t[3] in ("fqdn", "email"):
                    pr["remote_id"] = t[4]
                elif t[:3] == ["authentication", "remote", "rsa-sig"]:
                    pr["cert"] = True
                elif t[:3] == ["authentication", "local", "eap"]:
                    if "username" in t:
                        pr["eap_user"] = t[t.index("username") + 1]
                    if "password" in t:
                        pr["eap_pw"] = t[t.index("password") + 1]
                elif t[0] == "lifetime" and len(t) > 1:
                    pr["life"] = t[1]
                elif t[0] == "dpd" and len(t) > 1:
                    pr["dpd"] = t[1]
                elif t[:2] == ["config-exchange", "request"]:
                    pr["vips"] = True
            profiles[w[3]] = pr
        elif head.startswith("crypto ipsec transform-set ") and len(w) > 4:
            t = " ".join(w[4:])
            enc = next((k for v, k in rev_esp.items() if t.startswith(v) or f" {v}" in t), None)
            auth = next((k for v, k in rev_auth.items() if v in w), None)
            tsets[w[3]] = (enc, auth)
        elif head.startswith("ip access-list extended ") and len(w) > 3:
            nets = []
            for line in body:
                t = line.split()
                if t[:2] == ["permit", "ip"]:
                    _, rest = _cisco_net(t[2:])
                    dst, _ = _cisco_net(rest)
                    if dst:
                        nets.append(dst)
            acls[w[3]] = nets
        elif head.startswith("crypto isakmp policy "):
            enc, h, dh = None, "sha1", None
            for line in body:
                t = line.split()
                if t[0] == "encryption":
                    enc = rev_v1.get(" ".join(t[1:3])) or rev_v1.get(t[1])
                elif t[0] == "hash" and len(t) > 1:
                    h = "sha1" if t[1] == "sha" else t[1]
                elif t[0] == "group" and len(t) > 1:
                    dh = DH_BY_NUMBER.get(t[1])
            if enc:
                v1["rows"].append((enc, h))
            if dh:
                v1["dh"].append(dh)
        elif head.startswith("crypto isakmp key ") and "address" in w:
            v1["psk"][w[w.index("address") + 1]] = w[3]
        elif head.startswith("crypto map ") and "ipsec-isakmp" in w:
            m = {"name": w[2]}
            for line in body:
                t = line.split()
                if t[:2] == ["set", "peer"] and len(t) > 2:
                    m["peer"] = t[2]
                elif t[:2] == ["set", "transform-set"] and len(t) > 2:
                    m["tset"] = t[2]
                elif t[:2] == ["set", "pfs"] and len(t) > 2:
                    m["pfs"] = DH_BY_NUMBER.get(t[2].replace("group", ""))
                elif t[:2] == ["set", "ikev2-profile"] and len(t) > 2:
                    m["profile"] = t[2]
                elif t[:2] == ["match", "address"] and len(t) > 2:
                    m["acl"] = t[2]
                elif t[:4] == ["set", "security-association", "lifetime", "seconds"] and len(t) > 4:
                    m["life"] = t[4]
            maps.append(m)
    maps = [m for m in maps if m.get("peer")]
    if not maps:
        raise ValueError(tr("El archivo no contiene crypto maps de Cisco con «set peer»."))
    result, warnings = [], []
    for m in maps:
        name = m.get("profile") or m["name"]
        values = blank_values(name, m["peer"])
        pr = profiles.get(m.get("profile"))
        if pr is not None:
            p = proposals.get(name) or (next(iter(proposals.values())) if proposals else {"enc": [], "integ": [], "dh": []})
            ike = (_rows(p["enc"], p["integ"]), p["dh"])
            values["psk"] = keyring_psk.get(m["peer"], "")
            set_user_auth(values, pr["eap_user"], pr["eap_pw"], cert=pr["cert"])
            values.update(local_id=pr["local_id"], remote_id=pr["remote_id"] or "%any", vips=pr["vips"],
                          rekey_time=f"{pr['life']}s" if pr["life"] else "",
                          dpd_delay=f"{pr['dpd']}s" if pr["dpd"] else "")
        else:
            values["version"] = "1"
            ike = (v1["rows"], v1["dh"])
            values["psk"] = v1["psk"].get(m["peer"], "")
            values["vips"] = False
            set_user_auth(values, "", "")
        if not values["psk"] and values["remote_auth"] == "psk":
            warnings.append(tr("«{0}»: no se encontró la clave precompartida; escríbela al editar.").format(name))
        enc, auth = tsets.get(m.get("tset"), (None, None))
        set_proposals(values, ike, (_rows([enc] if enc else [], [auth] if auth else []),
                                    [m["pfs"]] if m.get("pfs") else []))
        values["child_rekey_time"] = f"{m['life']}s" if m.get("life") else ""
        values["remote_ts"] = nets_text(acls.get(m.get("acl"), []))
        result.append((values, {"description": ""}))
    return result, warnings


# --- Apple macOS / iOS (.mobileconfig) ----------------------------------------

APPLE_ENC = {"aes128": "AES-128", "aes256": "AES-256", "aes128gcm16": "AES-128-GCM", "aes256gcm16": "AES-256-GCM",
             "chacha20poly1305": "ChaCha20Poly1305", "3des": "3DES"}
APPLE_INTEG = {"sha1": "SHA1-160", "sha256": "SHA2-256", "sha384": "SHA2-384", "sha512": "SHA2-512"}


def _apple_sa(rows, dhs, life):
    row = next(((e, i) for e, i in rows if e in APPLE_ENC and (e in AEAD or i in APPLE_INTEG)), ("aes256", "sha256"))
    sa = {"EncryptionAlgorithm": APPLE_ENC[row[0]], "IntegrityAlgorithm": APPLE_INTEG.get(row[1], "SHA2-256"),
          "LifeTimeInMinutes": max(10, min(1440, (life or 86400) // 60))}
    dh = next((d for d in dhs if d in NUMBER_BY_DH), None)
    if dh:
        sa["DiffieHellmanGroup"] = int(NUMBER_BY_DH[dh])
    return sa


def apple_export(items):
    import plistlib
    import uuid
    payloads, warnings = [], []
    for view, secrets, meta in items:
        secrets = secrets or {}
        name = view["name"]
        user, user_on = view_user(view)
        (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
        server = view["remote_addrs"].split(",")[0].strip()
        p = {"PayloadType": "com.apple.vpn.managed", "PayloadVersion": 1, "PayloadUUID": str(uuid.uuid4()).upper(),
             "PayloadIdentifier": f"org.swangui.vpn.{name}", "PayloadDisplayName": name,
             "UserDefinedName": name}
        if view["version"] == "2":
            ike = {"RemoteAddress": server,
                   "RemoteIdentifier": view["remote_id"] if view.get("remote_id") not in ("", "%any") else server,
                   "LocalIdentifier": view.get("local_id") or user or name,
                   "AuthenticationMethod": "Certificate" if view["remote_auth"] != "psk" and not user_on
                   else ("SharedSecret" if view["remote_auth"] == "psk" else "None"),
                   "ExtendedAuthEnabled": 1 if user_on else 0,
                   "DeadPeerDetectionRate": "Medium" if view.get("dpd_delay") else "None",
                   "DisableMOBIKE": 0 if view.get("mobike", True) else 1,
                   "EnablePFS": 1 if pfs else 0,
                   "IKESecurityAssociationParameters": _apple_sa(ike_rows, dhs, parse_seconds(view.get("rekey_time"))),
                   "ChildSecurityAssociationParameters": _apple_sa(esp_rows, pfs or dhs,
                                                                   parse_seconds(view.get("child_rekey_time")) or 3600)}
            if view["remote_auth"] == "psk" and secrets.get("psk"):
                ike["SharedSecret"] = secrets["psk"]
            if user_on:
                ike["AuthName"] = user
                if secrets.get("password"):
                    ike["AuthPassword"] = secrets["password"]
            p.update(VPNType="IKEv2", IKEv2=ike)
        else:
            ips = {"RemoteAddress": server, "AuthenticationMethod": "SharedSecret",
                   "LocalIdentifier": view.get("local_id") or name, "LocalIdentifierType": "KeyID",
                   "XAuthEnabled": 1 if user_on else 0}
            if secrets.get("psk"):
                ips["SharedSecret"] = secrets["psk"].encode()
            if user_on:
                ips["XAuthName"] = user
                if secrets.get("password"):
                    ips["XAuthPassword"] = secrets["password"]
            p.update(VPNType="IPSec", IPSec=ips)
        if not is_full_tunnel(view):
            warnings.append(tr("«{0}»: en Apple las rutas las envía el servidor; las redes remotas no se exportan.").format(name))
        payloads.append(p)
    profile = {"PayloadContent": payloads, "PayloadType": "Configuration", "PayloadVersion": 1,
               "PayloadIdentifier": "org.swangui.profile", "PayloadUUID": str(uuid.uuid4()).upper(),
               "PayloadDisplayName": "VPN (SwanGUI)", "PayloadRemovalDisallowed": False}
    return plistlib.dumps(profile).decode(), warnings


def apple_import(text):
    import plistlib
    try:
        plist = plistlib.loads(text.encode())
    except Exception as e:  # plistlib raises several types for malformed input
        raise ValueError(tr("No es un perfil de Apple válido: {0}").format(e))
    vpns = [p for p in plist.get("PayloadContent", []) if isinstance(p, dict) and p.get("VPNType")]
    if not vpns:
        raise ValueError(tr("El perfil no contiene VPN IKEv2 ni IPSec."))
    rev_enc = {v: k for k, v in APPLE_ENC.items()}
    rev_int = {v: k for k, v in APPLE_INTEG.items()}

    def sa_rows(sa):
        e, i = rev_enc.get(sa.get("EncryptionAlgorithm", "AES-256"), "aes256"), rev_int.get(sa.get("IntegrityAlgorithm", "SHA2-256"), "sha256")
        dh = DH_BY_NUMBER.get(str(sa.get("DiffieHellmanGroup", 14)))
        return _rows([e], [i]), ([dh] if dh else []), int(sa.get("LifeTimeInMinutes", 0)) * 60

    result, warnings = [], []
    for p in vpns:
        name = p.get("UserDefinedName") or p.get("PayloadDisplayName") or "vpn"
        if p["VPNType"] == "IKEv2":
            k = p.get("IKEv2", {})
            values = blank_values(name, k.get("RemoteAddress", ""))
            r1, d1, l1 = sa_rows(k.get("IKESecurityAssociationParameters", {}))
            r2, d2, l2 = sa_rows(k.get("ChildSecurityAssociationParameters", {}))
            set_proposals(values, (r1, d1), (r2, d2 if k.get("EnablePFS") else []))
            values["rekey_time"] = f"{l1}s" if l1 else ""
            values["child_rekey_time"] = f"{l2}s" if l2 else ""
            cert = k.get("AuthenticationMethod") in ("Certificate", "None")
            values["psk"] = k.get("SharedSecret", "") if not cert else ""
            if k.get("ExtendedAuthEnabled"):
                set_user_auth(values, k.get("AuthName", ""), k.get("AuthPassword", ""), cert=cert)
            else:
                set_user_auth(values, "", "", cert=cert)
            rid = k.get("RemoteIdentifier", "")
            values["remote_id"] = rid if rid and rid != values["remote_addrs"] else "%any"
            lid = k.get("LocalIdentifier", "")
            values["local_id"] = "" if k.get("ExtendedAuthEnabled") and lid == k.get("AuthName") else lid
            values["mobike"] = not k.get("DisableMOBIKE")
        elif p["VPNType"] == "IPSec":
            k = p.get("IPSec", {})
            values = blank_values(name, k.get("RemoteAddress", ""))
            values["version"] = "1"
            values["aggressive"] = True
            secret = k.get("SharedSecret", b"")
            values["psk"] = secret.decode(errors="replace") if isinstance(secret, bytes) else str(secret)
            if k.get("XAuthEnabled"):
                set_user_auth(values, k.get("XAuthName", ""), k.get("XAuthPassword", ""))
            values["local_id"] = k.get("LocalIdentifier", "")
        else:
            warnings.append(tr("«{0}»: tipo de VPN «{1}» no es IPsec; se omite.").format(name, p["VPNType"]))
            continue
        if not values["remote_addrs"]:
            warnings.append(tr("«{0}»: no tiene gateway remoto; se omite.").format(name))
            continue
        result.append((values, {"description": ""}))
    return result, warnings


# --- strongSwan for Android (.sswan) ------------------------------------------

def sswan_export(items):
    import uuid
    warnings = []
    view, secrets, meta = items[0]
    if len(items) > 1:
        warnings.append(tr("Un archivo .sswan solo admite un perfil; se exportó «{0}».").format(view["name"]))
    user, user_on = view_user(view)
    if view["version"] != "2":
        warnings.append(tr("«{0}»: la app de Android solo admite IKEv2.").format(view["name"]))
    if view["remote_auth"] == "psk":
        warnings.append(tr("«{0}»: la app de Android no admite clave precompartida; el servidor debe "
                           "presentar un certificado.").format(view["name"]))
    if user_on:
        kind = "ikev2-eap"
    elif view["local_auth"] == "eap-tls":
        kind = "ikev2-eap-tls"
    else:
        kind = "ikev2-cert"
    (ike_rows, dhs), (esp_rows, pfs) = view_proposals(view)
    prof = {"uuid": str(uuid.uuid4()), "name": view["name"], "type": kind,
            "remote": {"addr": view["remote_addrs"].split(",")[0].strip()},
            "local": {}, "ike-proposal": build_proposals(ike_rows, dhs, True),
            "esp-proposal": build_proposals(esp_rows, pfs, False)}
    if view.get("remote_id") not in ("", "%any"):
        prof["remote"]["id"] = view["remote_id"]
    if user_on:
        prof["local"]["eap_id"] = user
    if view.get("local_id"):
        prof["local"]["id"] = view["local_id"]
    if not is_full_tunnel(view):
        prof["split-tunneling"] = {"subnets": " ".join(str(n) for n in view_nets(view, None))}
    return json.dumps(prof, indent=2, ensure_ascii=False) + "\n", warnings


def sswan_import(text):
    try:
        prof = json.loads(text)
    except ValueError as e:
        raise ValueError(tr("No es un perfil .sswan válido: {0}").format(e))
    if not isinstance(prof, dict) or "remote" not in prof:
        raise ValueError(tr("No es un perfil .sswan válido: {0}").format("remote"))
    remote, local = prof.get("remote") or {}, prof.get("local") or {}
    name = prof.get("name") or remote.get("addr", "vpn")
    values = blank_values(name, remote.get("addr", ""))
    kind = prof.get("type", "ikev2-eap")
    if "eap" in kind and kind != "ikev2-eap-tls":
        set_user_auth(values, local.get("eap_id", ""), "", cert=True)
    elif kind == "ikev2-eap-tls":
        set_user_auth(values, "", "", cert=True)
        values["local_auth"] = "eap-tls"
    else:
        set_user_auth(values, "", "", cert=True)
    values["local_id"] = local.get("id", "")
    values["remote_id"] = remote.get("id", "") or "%any"
    if prof.get("ike-proposal"):
        values["proposals"] = prof["ike-proposal"]
    if prof.get("esp-proposal"):
        values["esp_proposals"] = prof["esp-proposal"]
    subnets = (prof.get("split-tunneling") or {}).get("subnets", "")
    warnings = []
    if subnets:
        try:
            values["remote_ts"] = nets_text(subnets.split())
        except ValueError:
            warnings.append(tr("«{0}»: redes remotas no válidas; se usa todo el tráfico.").format(name))
    if not values["remote_addrs"]:
        raise ValueError(tr("«{0}»: no tiene gateway remoto; se omite.").format(name))
    return [(values, {"description": ""})], warnings


# --- format registry ----------------------------------------------------------

def _forti_export(items):
    return forti_export(items), []


EXPORT_FORMATS = [
    ("forti", "FortiClient (.conf XML)", "-forticlient.conf", _forti_export),
    ("swanctl", "strongSwan swanctl (.conf)", ".swanctl.conf", None),
    ("mikrotik", "MikroTik RouterOS 7 (.rsc)", "-mikrotik.rsc", mikrotik_export),
    ("pfsense", "pfSense / OPNsense (config.xml, IPsec)", "-pfsense.xml", pfsense_export),
    ("windows", "Windows 10/11 (PowerShell .ps1)", "-windows.ps1", windows_export),
    ("cisco", "Cisco IOS / IOS-XE", "-cisco.txt", cisco_export),
    ("apple", "Apple macOS / iOS (.mobileconfig)", ".mobileconfig", apple_export),
    ("sswan", "strongSwan Android (.sswan)", ".sswan", sswan_export),
]
IMPORTERS = {"forti": forti_import, "mikrotik": mikrotik_import, "pfsense": pfsense_import,
             "windows": windows_import, "cisco": cisco_import, "apple": apple_import, "sswan": sswan_import}


def detect_format(text):
    head = text.lstrip()[:4096]
    if head.startswith("<"):
        if "forticlient_configuration" in text:
            return "forti"
        if "<plist" in head or "PayloadContent" in text:
            return "apple"
        if "<phase1" in text:
            return "pfsense"
        return "forti"
    if head.startswith("{"):
        return "sswan"
    if re.search(r"(?m)^\s*/ip\s+ipsec\b", text):
        return "mikrotik"
    if re.search(r"(?im)^\s*Add-VpnConnection\b", text):
        return "windows"
    if re.search(r"(?m)^crypto (ikev2|isakmp|map|ipsec) ", text):
        return "cisco"
    return "swanctl"


# ---------------------------------------------------------------------------
# vendor templates
# ---------------------------------------------------------------------------

TEMPLATES = {
    "FortiGate — IKEv2 + EAP (usuario y contraseña)": {
        "version": "2", "auth": "psk", "user": "save", "eap": "eap-mschapv2",
        "ike": [("aes256", "sha256"), ("aes128", "sha256")], "dh": ["modp2048", "modp1536"],
        "esp": [("aes256", "sha256"), ("aes128", "sha256")], "pfs": "modp2048",
        "ike_life": 86400, "esp_life": 43200},
    "FortiGate — IKEv1 + XAuth (modo agresivo)": {
        "version": "1", "aggressive": True, "auth": "psk", "user": "save",
        "ike": [("aes256", "sha256"), ("aes128", "sha256"), ("aes128", "sha1")],
        "dh": ["modp2048", "modp1536"],
        "esp": [("aes256", "sha256"), ("aes128", "sha256"), ("aes128", "sha1")], "pfs": "modp1536",
        "ike_life": 86400, "esp_life": 43200},
    "pfSense / OPNsense — IKEv2 + EAP-MSCHAPv2": {
        "version": "2", "auth": "cert", "user": "save", "eap": "eap-mschapv2",
        "ike": [("aes256", "sha256"), ("aes256gcm16", "sha256")], "dh": ["modp2048", "ecp256"],
        "esp": [("aes256gcm16", "sha256"), ("aes256", "sha256")], "pfs": None,
        "ike_life": 28800, "esp_life": 3600},
    "MikroTik — IKEv2 con clave precompartida": {
        "version": "2", "auth": "psk", "user": "none",
        "ike": [("aes256", "sha256"), ("aes128", "sha1")], "dh": ["modp2048", "modp1024"],
        "esp": [("aes256", "sha256"), ("aes128", "sha1")], "pfs": "modp2048",
        "ike_life": 86400, "esp_life": 1800},
    "Windows Server RRAS — IKEv2 + EAP": {
        "version": "2", "auth": "cert", "user": "save", "eap": "eap-mschapv2",
        "ike": [("aes256", "sha256"), ("aes256", "sha1"), ("3des", "sha1")], "dh": ["modp2048", "modp1024"],
        "esp": [("aes256", "sha1"), ("aes128", "sha1")], "pfs": None,
        "ike_life": 28800, "esp_life": 3600},
    "Cisco ASA / FTD — IKEv2 con clave precompartida": {
        "version": "2", "auth": "psk", "user": "none",
        "ike": [("aes256", "sha256"), ("aes256gcm16", "sha384")], "dh": ["modp2048", "ecp256", "ecp384"],
        "esp": [("aes256", "sha256"), ("aes256gcm16", "sha256")], "pfs": "modp2048",
        "ike_life": 86400, "esp_life": 28800},
    "strongSwan / Linux — IKEv2 + EAP": {
        "version": "2", "auth": "cert", "user": "save", "eap": "eap-mschapv2", "default_props": True,
        "ike": [("aes256", "sha256")], "dh": ["modp2048"], "esp": [("aes256", "sha256")], "pfs": None,
        "ike_life": 0, "esp_life": 0},
}

OTP_MODES = [("none", tr("No")), ("append", tr("Código añadido a la contraseña (FortiToken)")),
             ("push", tr("Notificación push (aprobar en el móvil)"))]
DNS_MODES = [("none", tr("No usar el DNS de la VPN")),
             ("split", tr("Solo para los dominios de la empresa (DNS dividido)")),
             ("all", tr("Todo el DNS por la VPN"))]


# ---------------------------------------------------------------------------
# error explanations
# ---------------------------------------------------------------------------

ERROR_HINTS = [
    (r"IKE_SA_INIT response .*N\(NO_PROP\)|received NO_PROPOSAL_CHOSEN notify error|no IKE proposal",
     tr("Fase 1 no coincide: el cifrado, la autenticación o el grupo DH no son los que espera el servidor. Revisa «Fase 1» o prueba una plantilla del fabricante.")),
    (r"NO_PROPOSAL_CHOSEN notify, no CHILD_SA|no acceptable proposal found|IKE_AUTH response .*N\(NO_PROP\)",
     tr("Fase 2 no coincide: revisa las propuestas de la Fase 2 y el PFS/grupo DH.")),
    (r"EAP_FAILURE|EAP method \S+ failed|XAuth authentication .*failed|XAUTH.*fail",
     tr("Usuario o contraseña incorrectos (o el usuario no está en el grupo VPN del servidor).")),
    (r"AUTHENTICATION_FAILED|authentication of .* failed|MAC mismatched|tried \d+ shared key",
     tr("Autenticación rechazada: la clave precompartida o el ID local/remoto no coinciden con el servidor.")),
    (r"no shared key found",
     tr("Falta la clave precompartida: edita la conexión y escríbela.")),
    (r"no EAP key found|no XAuth secret found|no secret found",
     tr("Falta la contraseña del usuario: escríbela o usa «Pedir al iniciar sesión».")),
    (r"constraint check failed|identity '.*' not confirmed|no matching peer config",
     tr("El ID remoto no coincide con el que presenta el servidor: prueba con «%any» en «ID remoto».")),
    (r"no trusted .* public key found|no issuer certificate found|certificate status is not available",
     tr("No se puede verificar el certificado del servidor: copia el certificado de la CA a /etc/swanctl/x509ca.")),
    (r"certificate .*expired|expired certificate",
     tr("Un certificado está caducado.")),
    (r"TS_UNACCEPTABLE",
     tr("El servidor no acepta las redes indicadas: revisa «Redes remotas» y «Redes locales».")),
    (r"FAILED_CP_REQUIRED",
     tr("El servidor exige Mode Config: activa «Mode Config (recibir IP virtual)».")),
    (r"INTERNAL_ADDRESS_FAILURE",
     tr("El servidor no pudo asignarte una IP virtual (sin direcciones libres o Mode Config mal configurado).")),
    (r"giving up after \d+ retransmits|peer not responding|retransmit \d+ of request",
     tr("El servidor no responde: comprueba el gateway, que tu red permita UDP 500/4500 y que la VPN esté habilitada en el servidor.")),
    (r"unable to resolve|failed to resolve|resolving .* failed",
     tr("No se pudo resolver el nombre del gateway (problema de DNS o de internet).")),
    (r"INVALID_KE_PAYLOAD",
     tr("El servidor pidió otro grupo DH; si falla, marca en Fase 1 el grupo que usa el servidor.")),
    (r"unable to install|unable to allocate SPI|kernel.*not supported",
     tr("El sistema no pudo instalar el túnel (módulos del kernel o permisos).")),
    (r"Autorización denegada|Authorization denied",
     tr("pkexec rechazó la operación: revisa la regla de polkit de SwanGUI.")),
]


def explain(text):
    hints = []
    for pattern, hint in ERROR_HINTS:
        if re.search(pattern, text, re.I) and hint not in hints:
            hints.append(hint)
    return hints


# ---------------------------------------------------------------------------
# system keyring (Secret Service via secret-tool)
# ---------------------------------------------------------------------------

class Keyring:
    @staticmethod
    def available():
        return shutil.which("secret-tool") is not None

    @staticmethod
    def _attrs(name):
        return ["application", "swangui", "connection", name]

    @classmethod
    def get(cls, name):
        if not cls.available():
            return ""
        try:
            p = subprocess.run(["secret-tool", "lookup", *cls._attrs(name)],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return p.stdout if p.returncode == 0 else ""

    @classmethod
    def set(cls, name, password):
        p = subprocess.run(["secret-tool", "store", f"--label=SwanGUI VPN: {name}", *cls._attrs(name)],
                           input=password, capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise OSError(p.stderr.strip() or tr("secret-tool falló"))

    @classmethod
    def clear(cls, name):
        if cls.available():
            subprocess.run(["secret-tool", "clear", *cls._attrs(name)],
                           capture_output=True, text=True, timeout=10)


# ---------------------------------------------------------------------------
# connection history
# ---------------------------------------------------------------------------

XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
HISTORY_PATH = os.path.join(XDG_DATA, "swangui", "history.jsonl")


class History:
    @staticmethod
    def add(entry):
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def load(limit=500):
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return list(reversed(out))

    @staticmethod
    def clear():
        try:
            os.unlink(HISTORY_PATH)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# networks (NetworkManager) and keyboard shortcuts (XFCE)
# ---------------------------------------------------------------------------

def nm_connections(active_only=False):
    """[(name, type)] of NetworkManager profiles (physical networks only)."""
    args = ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]
    if active_only:
        args.append("--active")
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in p.stdout.splitlines():
        name, _, typ = line.rpartition(":")
        name = name.replace("\\:", ":")
        if typ in ("802-11-wireless", "802-3-ethernet", "gsm", "cdma", "bluetooth", "pppoe"):
            out.append((name, typ))
    return out


def nm_connectivity():
    """NetworkManager connectivity: full, limited, portal, none or unknown."""
    try:
        p = subprocess.run(["nmcli", "-t", "networking", "connectivity"], capture_output=True,
                           text=True, timeout=5)
        return p.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def xfce_shortcut_path(seq):
    """Qt 'Ctrl+Alt+V' -> '/commands/custom/<Primary><Alt>v'."""
    mods = {"Ctrl": "<Primary>", "Alt": "<Alt>", "Shift": "<Shift>", "Meta": "<Super>"}
    parts = seq.split("+")
    key = parts[-1]
    key = key.lower() if len(key) == 1 else key
    return "/commands/custom/" + "".join(mods.get(p, "") for p in parts[:-1]) + key


def xfce_set_shortcut(seq, command):
    subprocess.run(["xfconf-query", "-c", "xfce4-keyboard-shortcuts", "-p", xfce_shortcut_path(seq),
                    "-n", "-t", "string", "-s", command], check=True, timeout=10)


def xfce_remove_shortcut(seq):
    subprocess.run(["xfconf-query", "-c", "xfce4-keyboard-shortcuts", "-p", xfce_shortcut_path(seq),
                    "-r"], timeout=10)


# ---------------------------------------------------------------------------
# traffic / latency graph
# ---------------------------------------------------------------------------

class TrafficGraph(QWidget):
    """Download/upload rate and latency over the last minutes."""

    def __init__(self):
        super().__init__()
        self.samples = []  # (t, rx_bps, tx_bps, latency_ms or None)
        self.setMinimumHeight(110)

    def set_samples(self, samples):
        self.samples = samples
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(4, 4, -4, -18)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(r)
        if len(self.samples) < 2:
            p.setPen(pal.text().color())
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr("Sin datos todavía"))
            return
        n = len(self.samples)
        peak = max(max(s[1], s[2]) for s in self.samples) or 1
        lat_peak = max((s[3] or 0) for s in self.samples) or 1

        def line(idx, color, scale):
            pts = []
            for i, s in enumerate(self.samples):
                v = s[idx]
                if v is None:
                    continue
                x = r.left() + r.width() * i / (n - 1)
                y = r.bottom() - r.height() * (v / scale)
                pts.append(QPointF(x, y))
            if len(pts) > 1:
                p.setPen(QPen(QColor(color), 2))
                p.drawPolyline(pts)

        line(1, "#2f81f7", peak)
        line(2, "#2ea043", peak)
        line(3, "#d29922", lat_peak)
        last = self.samples[-1]
        p.setPen(pal.text().color())
        f = p.font()
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        lat = f"{last[3]:.0f} ms" if last[3] is not None else "—"
        p.drawText(4, self.height() - 4,
                   tr("↓ {0}/s   ↑ {1}/s   latencia {2}   (máx. {3}/s · {4:.0f} ms)").format(human_bytes(last[1]), human_bytes(last[2]), lat, human_bytes(peak), lat_peak))
        p.end()


# ---------------------------------------------------------------------------
# diagnostics dialog
# ---------------------------------------------------------------------------

class DiagnoseDialog(QDialog):
    def __init__(self, parent, app, name):
        super().__init__(parent)
        self.app, self.name = app, name
        self.setWindowTitle(tr("Probar conexión — {0}").format(name))
        self.setWindowIcon(shield_icon("connecting"))
        self.resize(720, 520)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("Comprobación"), tr("Resultado")])
        self.list.setRootIsDecorated(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.list.setWordWrap(True)
        self.hints = QLabel()
        self.hints.setWordWrap(True)
        self.hints.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFont("monospace", 9))
        self.log.setVisible(False)
        self.try_btn = QPushButton(tr("Intentar conexión de prueba"))
        self.try_btn.setToolTip(tr("Conecta con registro detallado y analiza el resultado. Si funciona, desconecta al terminar."))
        self.try_btn.clicked.connect(lambda: self.run(True))
        again = QPushButton(tr("Repetir"))
        again.clicked.connect(lambda: self.run(False))
        leak = QPushButton(tr("IP pública y fugas DNS…"))
        leak.clicked.connect(lambda: LeakTestDialog(self, self.app).exec())
        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.try_btn)
        row.addWidget(again)
        row.addWidget(leak)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 2)
        lay.addWidget(self.hints)
        lay.addWidget(self.log, 2)
        lay.addLayout(row)
        self.run(False)

    def run(self, try_connect):
        self.list.clear()
        self.hints.setText("<i>Comprobando…</i>")
        self.try_btn.setEnabled(False)
        opts = {"try": try_connect, "dns_servers": self.app.settings.conn(self.name).get("dns_servers", [])}
        if try_connect:
            pw = self.app.password_for(self.name, interactive=True, parent=self)
            if pw is False:
                self.try_btn.setEnabled(True)
                self.hints.setText("")
                return
            if pw:
                opts["password"] = pw
        self.app.helper.run(["diagnose", self.name], self._done, stdin=json.dumps(opts))

    def _done(self, data):
        self.try_btn.setEnabled(True)
        if "error" in data:
            self.hints.setText(f"<b>Error:</b> {data['error']}")
            return
        hints, logs = [], []
        for st in data.get("steps", []):
            item = QTreeWidgetItem([st["title"], st.get("detail", "") or (tr("Correcto") if st["ok"] else tr("Falló"))])
            item.setIcon(0, dot_icon("up" if st["ok"] else "error"))
            if st.get("hint"):
                item.setToolTip(1, st["hint"])
                hints.append(st["hint"])
            self.list.addTopLevelItem(item)
            if st.get("log"):
                logs.append(st["log"])
                if not st["ok"]:
                    hints += explain(st["log"]) or [tr("La conexión falló; revisa el registro de abajo.")]
                else:
                    item.setText(1, tr("Conectó correctamente (y se desconectó)"))
        self.log.setVisible(bool(logs))
        self.log.setPlainText("\n".join(logs))
        if hints:
            self.hints.setText(tr("<b>Qué revisar:</b><ul>") + "".join(f"<li>{h}</li>" for h in hints) + "</ul>")
        else:
            self.hints.setText(tr("<b>Todo en orden.</b>") if all(s["ok"] for s in data.get("steps", []))
                               else "")


class LeakTestDialog(QDialog):
    """Public IP and DNS leak check: which link answers each kind of name."""
    ready = pyqtSignal(list)

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle(tr("IP pública y fugas de DNS"))
        self.setWindowIcon(shield_icon("connecting"))
        self.resize(720, 420)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("Comprobación"), tr("Resultado")])
        self.list.setRootIsDecorated(False)
        self.list.setWordWrap(True)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.hints = QLabel("<i>Comprobando…</i>")
        self.hints.setWordWrap(True)
        self.hints.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        again = QPushButton(tr("Repetir"))
        again.clicked.connect(self.run)
        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(again)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        lay.addWidget(self.hints)
        lay.addLayout(row)
        self.ready.connect(self._show)
        self.run()

    def run(self):
        self.list.clear()
        self.hints.setText("<i>Comprobando…</i>")
        conns = []
        for c in self.app.status.get("connections", []):
            if self.app.state_of(c["name"]) == "up":
                conns.append((c["name"], self.app.settings.conn(c["name"]), c.get("remote_ts", ""),
                              c.get("remote", "")))
        threading.Thread(target=lambda: self.ready.emit(self._checks(conns)), daemon=True).start()

    @staticmethod
    def _query(name, rtype="A"):
        try:
            p = subprocess.run(["resolvectl", "query", "--cache=no", "-t", rtype, name],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            return None, str(e)
        m = re.search(r"-- link: (\S+)", p.stdout)
        if p.returncode != 0:
            return None, (p.stderr or p.stdout).strip().split("\n")[0]
        return (m.group(1) if m else "?"), ""

    def _checks(self, conns):
        rows = []
        try:
            req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": APP})
            with urllib.request.urlopen(req, timeout=10) as r:
                ip = r.read(64).decode().strip()
        except OSError as e:
            ip, err = "", str(e)
        full = [c for c in conns if any(t.strip() in ("0.0.0.0/0", "::/0") for t in c[2].split(","))]
        if ip:
            detail = f"{ip}"
            gws = set()
            for c in full:
                for host in c[3].split(","):
                    try:
                        gws |= {ai[4][0] for ai in socket.getaddrinfo(host.strip(), None)}
                    except OSError:
                        pass
            ok = True
            hint = ""
            if full:
                if ip in gws:
                    detail += tr(" — es la IP del gateway: internet sale por la VPN")
                else:
                    detail += tr(" — distinta de la del gateway (puede ser NAT de la empresa)")
                    hint = (tr("Con túnel completo tu IP pública debería ser la de la empresa. Si esta IP es la de tu proveedor de internet, el tráfico no está pasando por la VPN."))
            else:
                detail += tr(" — tu salida a internet normal (túnel dividido)")
            rows.append((tr("IP pública"), ok, detail, hint))
        else:
            rows.append((tr("IP pública"), False, tr("no se pudo consultar: {0}").format(err), ""))

        link, err = self._query("www.google.com")
        any_all = any(c[1].get("dns_mode") == "all" for c in conns)
        if link is None:
            rows.append((tr("Resolver un nombre de internet"), False, err, tr("No hay DNS funcionando.")))
        elif any_all:
            ok = link.startswith("swg-")
            rows.append((tr("DNS de internet"), ok, tr("respondido por {0}").format(link)
                         + ("" if ok else tr(" — FUGA: debería ir por la VPN")),
                         "" if ok else tr("Con «Todo el DNS por la VPN» las consultas no deberían salir por tu conexión local. Desconecta y vuelve a conectar.")))
        else:
            ok = not link.startswith("swg-")
            rows.append((tr("DNS de internet"), ok, tr("respondido por {0}").format(link)
                         + (tr(" (tu DNS local, correcto)") if ok else tr(" — va por la VPN")), ""))
        for name, meta, _ts, _r in conns:
            if meta.get("dns_mode") != "split":
                continue
            for dom in meta.get("dns_domains", []):
                link, err = self._query(dom, "SOA")
                if link is None:
                    rows.append((f"{name}: dominio {dom}", False, err,
                                 tr("El DNS de la VPN no conoce «{0}». Confirma el dominio interno con el administrador.").format(dom)))
                else:
                    ok = link.startswith("swg-")
                    rows.append((f"{name}: dominio {dom}", ok, tr("respondido por {0}").format(link)
                                 + ("" if ok else tr(" — FUGA: sale por tu DNS local")),
                                 "" if ok else tr("El DNS dividido no está aplicado. Desconecta y vuelve a conectar.")))
        if not conns:
            rows.append((tr("VPN"), True, tr("ninguna conexión activa: solo se comprobó tu salida normal"), ""))
        return rows

    def _show(self, rows):
        hints = []
        for title, ok, detail, hint in rows:
            item = QTreeWidgetItem([title, detail])
            item.setIcon(0, dot_icon("up" if ok else "error"))
            if hint:
                item.setToolTip(1, hint)
                hints.append(hint)
            self.list.addTopLevelItem(item)
        self.hints.setText(tr("<b>Qué revisar:</b><ul>") + "".join(f"<li>{h}</li>" for h in hints) + "</ul>"
                           if hints else tr("<b>Sin fugas detectadas.</b>"))


# ---------------------------------------------------------------------------
# editor dialog
# ---------------------------------------------------------------------------

USER_AUTH_MODES = [("prompt", tr("Pedir al iniciar sesión")), ("save", tr("Guardar credenciales")),
                   ("none", tr("Deshabilitada"))]


class EditorDialog(QDialog):
    def __init__(self, parent, data=None, meta=None, copy_secrets=None):
        """data: view from the helper (edit), or None (new).
        copy_secrets: when duplicating, data is a source and this holds its secrets."""
        super().__init__(parent)
        self.copying = copy_secrets is not None
        self.orig = {} if (data is None or self.copying) else data
        self.src = data or {}
        meta = meta or {}
        self.setWindowTitle(tr("Editar conexión VPN") if self.orig else tr("Nueva conexión VPN"))
        self.setWindowIcon(shield_icon("down"))
        self.resize(620, 700)

        # ================= basic =================
        head = QLabel("<span style='font-size:15pt; font-weight:600'>IPsec VPN</span>")
        self.template = combo([("", tr("(ninguna)"))] + [(k, tr(k)) for k in TEMPLATES])
        self.template.setToolTip(tr("Rellena las fases 1 y 2 con valores habituales de ese fabricante"))
        self.template.currentIndexChanged.connect(self._apply_template)
        self.name = QLineEdit()
        self.name.setPlaceholderText("oficina")
        self.description = QLineEdit()
        self.description.setPlaceholderText("opcional")
        self.remote_addrs = QLineEdit()
        self.remote_addrs.setPlaceholderText(tr("vpn.ejemplo.com o 203.0.113.10"))
        self.auth_method = combo([("psk", tr("Clave precompartida")), ("cert", tr("Certificado X.509"))])
        self.psk = QLineEdit()
        self.psk.setEchoMode(QLineEdit.EchoMode.Password)
        self.cert = QComboBox()
        self.cert.setEditable(True)
        self.cert.addItems(sorted(self._list_certs()))
        self.cert.setToolTip(tr("Certificado del cliente en /etc/swanctl/x509 (la clave privada va en /etc/swanctl/private)"))
        self.user_auth = combo(USER_AUTH_MODES)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        show = QCheckBox(tr("Mostrar"))
        show.toggled.connect(self._toggle_echo)
        self.pw_row = QHBoxLayout()
        self.pw_row.addWidget(self.password)
        self.pw_row.addWidget(show)
        self.all_traffic = QRadioButton(tr("Todo el tráfico por la VPN"))
        self.split = QRadioButton(tr("Solo estas redes:"))
        self.remote_ts = QLineEdit()
        self.remote_ts.setPlaceholderText("192.168.10.0/24, 10.0.0.0/8")
        self.split.toggled.connect(self.remote_ts.setEnabled)
        nets = QVBoxLayout()
        nets.setSpacing(2)
        nets.addWidget(self.all_traffic)
        r = QHBoxLayout()
        r.addWidget(self.split)
        r.addWidget(self.remote_ts, 1)
        nets.addLayout(r)
        self.otp = combo(OTP_MODES)
        self.otp.setToolTip(tr("FortiToken u otro segundo factor. Con OTP la contraseña se guarda en el llavero del sistema (o se pide), nunca en la configuración de strongSwan."))
        self.app_autoconnect = QCheckBox(tr("Conectar automáticamente al abrir SwanGUI"))
        self.always_up = QCheckBox(tr("Mantener siempre conectada (reconectar si se cae)"))
        self.untrusted_auto = QCheckBox(tr("Conectar sola fuera de mis redes de confianza (y desconectar al volver)"))
        self.untrusted_auto.setToolTip(tr("Las redes de confianza se eligen en Preferencias"))

        basic = QFormLayout()
        basic.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        if data is None:
            basic.addRow(tr("Plantilla:"), self.template)
        basic.addRow(tr("Nombre de la conexión:"), self.name)
        basic.addRow(tr("Descripción:"), self.description)
        basic.addRow(tr("Gateway remoto:"), self.remote_addrs)
        basic.addRow(tr("Método de autenticación:"), self.auth_method)
        basic.addRow(tr("Clave precompartida:"), self.psk)
        basic.addRow(tr("Certificado:"), self.cert)
        basic.addRow(tr("Autenticación (XAuth):"), self.user_auth)
        basic.addRow(tr("Usuario:"), self.username)
        basic.addRow(tr("Contraseña:"), self.pw_row)
        basic.addRow(tr("Token OTP:"), self.otp)
        basic.addRow(tr("Redes remotas:"), nets)
        basic.addRow(tr("Conexión automática:"), self.app_autoconnect)
        basic.addRow("", self.always_up)
        basic.addRow("", self.untrusted_auto)
        self.basic = basic

        # ================= advanced =================
        self.adv_btn = QToolButton()
        self.adv_btn.setText(tr("Configuración avanzada"))
        self.adv_btn.setCheckable(True)
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.adv_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.adv_btn.setStyleSheet("QToolButton { border: none; font-weight: 600; }")
        self.adv_btn.toggled.connect(self._toggle_advanced)

        # --- VPN settings
        vpn = Section(tr("Configuración VPN"))
        self.ike_v1 = QRadioButton("1")
        self.ike_v2 = QRadioButton("2")
        ver_group = QButtonGroup(self)
        ver_group.addButton(self.ike_v1)
        ver_group.addButton(self.ike_v2)
        vbox = QHBoxLayout()
        vbox.addWidget(self.ike_v1)
        vbox.addWidget(self.ike_v2)
        vbox.addStretch()
        self.ike_v2.toggled.connect(self._update_visibility)
        self.mode_main = QRadioButton(tr("Principal (ID protection)"))
        self.mode_aggr = QRadioButton(tr("Agresivo"))
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.mode_main)
        mode_group.addButton(self.mode_aggr)
        mbox = QHBoxLayout()
        mbox.addWidget(self.mode_main)
        mbox.addWidget(self.mode_aggr)
        mbox.addStretch()
        self.mode_box = QWidget()
        self.mode_box.setLayout(mbox)
        self.mode_cfg = QCheckBox(tr("Mode Config (recibir IP virtual y DNS del servidor)"))
        self.fragmentation = QCheckBox(tr("Habilitar fragmentación IKE"))
        self.local_ts = QLineEdit()
        self.local_ts.setPlaceholderText(tr("automático"))
        self.autostart = QCheckBox(tr("Conectar cuando arranque strongSwan (aunque no haya sesión)"))
        vpn.form.addRow(tr("IKE:"), vbox)
        vpn.form.addRow(tr("Modo:"), self.mode_box)
        vpn.form.addRow(tr("Opciones:"), self.mode_cfg)
        vpn.form.addRow("", self.fragmentation)
        vpn.form.addRow(tr("Redes locales:"), self.local_ts)
        vpn.form.addRow("", self.autostart)
        self.vpn_section = vpn

        # --- phase 1
        p1 = Section(tr("Fase 1"))
        self.ike_prop = ProposalEditor(ike=True)
        self.dh_checks = {}
        dh_grid = QGridLayout()
        dh_grid.setContentsMargins(0, 0, 0, 0)
        for n, (val, label) in enumerate(DH_GROUPS):
            cb = QCheckBox(label)
            cb.setToolTip(val)
            self.dh_checks[val] = cb
            dh_grid.addWidget(cb, n // 6, n % 6)
        self.dh_widget = QWidget()
        self.dh_widget.setLayout(dh_grid)
        self.ike_prop.default.toggled.connect(lambda on: self.dh_widget.setEnabled(not on))
        self.ike_life = spin(10 ** 7, " s")
        self.local_id = QLineEdit()
        self.local_id.setPlaceholderText(tr("automático (dirección IP)"))
        self.remote_id = QLineEdit()
        self.remote_id.setPlaceholderText("%any")
        self.eap_method = combo([("eap-mschapv2", tr("EAP-MSCHAPv2")), ("eap-md5", tr("EAP-MD5")),
                                 ("eap-gtc", tr("EAP-GTC")), ("eap-tls", tr("EAP-TLS"))])
        self.dpd = QCheckBox(tr("Detección de pares caídos (DPD)"))
        self.dpd_delay = spin(3600, " s", special="")
        self.dpd_delay.setMinimum(5)
        self.dpd.toggled.connect(self.dpd_delay.setEnabled)
        dpd_row = QHBoxLayout()
        dpd_row.addWidget(self.dpd)
        dpd_row.addWidget(QLabel("cada"))
        dpd_row.addWidget(self.dpd_delay)
        dpd_row.addStretch()
        self.dpd_action = combo([("restart", tr("Reconectar")), ("clear", tr("Cerrar")), ("trap", tr("Esperar tráfico"))])
        self.encap = QCheckBox(tr("Forzar NAT-Traversal (encapsulación UDP 4500)"))
        self.mobike = QCheckBox(tr("MOBIKE (mantener el túnel al cambiar de red)"))
        p1.form.addRow(tr("Propuesta IKE:"), self.ike_prop)
        p1.form.addRow(tr("Grupo DH:"), self.dh_widget)
        p1.form.addRow(tr("Vida de la clave:"), self.ike_life)
        p1.form.addRow(tr("ID local:"), self.local_id)
        p1.form.addRow(tr("ID remoto:"), self.remote_id)
        p1.form.addRow(tr("Método EAP:"), self.eap_method)
        p1.form.addRow("", dpd_row)
        p1.form.addRow(tr("Si el par no responde:"), self.dpd_action)
        p1.form.addRow("", self.encap)
        p1.form.addRow("", self.mobike)
        self.p1 = p1

        # --- phase 2
        p2 = Section(tr("Fase 2"))
        self.esp_prop = ProposalEditor(ike=False)
        self.esp_life = spin(10 ** 7, " s")
        self.esp_kb = spin(10 ** 9, " KB")
        life_row = QHBoxLayout()
        life_row.addWidget(self.esp_life)
        life_row.addWidget(QLabel("o"))
        life_row.addWidget(self.esp_kb)
        life_row.addStretch()
        self.replay = QCheckBox(tr("Habilitar detección de replay"))
        self.pfs = QCheckBox(tr("Habilitar Perfect Forward Secrecy (PFS)"))
        self.pfs_group = combo([(v, tr("Grupo {0}").format(l)) for v, l in DH_GROUPS])
        set_combo(self.pfs_group, "modp2048")
        self.pfs.toggled.connect(self.pfs_group.setEnabled)
        pfs_row = QHBoxLayout()
        pfs_row.addWidget(self.pfs)
        pfs_row.addWidget(self.pfs_group)
        pfs_row.addStretch()
        self.esp_prop.default.toggled.connect(self._esp_default_toggled)
        p2.form.addRow(tr("Propuesta IKE:"), self.esp_prop)
        p2.form.addRow(tr("Vida de la clave:"), life_row)
        p2.form.addRow("", self.replay)
        p2.form.addRow("", pfs_row)

        # --- DNS
        dns = Section(tr("DNS"))
        self.dns_mode = combo(DNS_MODES)
        self.dns_domains = QLineEdit()
        self.dns_domains.setPlaceholderText("empresa.local, intranet.empresa.com")
        self.dns_servers = QLineEdit()
        self.dns_servers.setPlaceholderText(tr("automático (los que envía el servidor)"))
        dns_note = QLabel(tr("<small>Con DNS dividido solo esos dominios se resuelven con el DNS de la empresa; el resto sigue usando tu DNS local y no pierdes la navegación.</small>"))
        dns_note.setWordWrap(True)
        dns_note.setStyleSheet("color: gray")
        dns.form.addRow(tr("DNS de la VPN:"), self.dns_mode)
        dns.form.addRow(tr("Dominios:"), self.dns_domains)
        dns.form.addRow(tr("Servidores DNS:"), self.dns_servers)
        dns.form.addRow("", dns_note)
        self.dns_section = dns
        self.dns_mode.currentIndexChanged.connect(self._update_visibility)

        # --- security
        sec = Section(tr("Seguridad"))
        self.killswitch = QCheckBox(tr("Kill switch: bloquear el tráfico si la VPN se cae"))
        self.killswitch.setToolTip(tr("Túnel completo: bloquea internet salvo el gateway y tu red local.\nTúnel dividido: bloquea las redes remotas para que no salgan sin cifrar."))
        self.ks_lan = QCheckBox(tr("Permitir mi red local (impresoras, NAS, router…)"))
        self.ks_lan.setChecked(True)
        self.ks_fwd = QCheckBox(tr("Aplicar también a contenedores y máquinas virtuales (Docker, libvirt…)"))
        self.ks_fwd.setChecked(True)
        self.ks_fwd.setToolTip(tr("Su tráfico no sale de tu equipo como tráfico propio y no queda cubierto por la regla normal.\nCon túnel completo y kill switch los contenedores no tendrán internet, porque su tráfico no viaja por el túnel."))
        self.killswitch.toggled.connect(self._update_visibility)
        sec.form.addRow("", self.killswitch)
        sec.form.addRow("", self.ks_lan)
        sec.form.addRow("", self.ks_fwd)
        self.sec_section = sec

        # --- monitoring
        mon = Section(tr("Supervisión"))
        self.latency_host = QLineEdit()
        self.latency_host.setPlaceholderText(tr("automático (primer equipo de la red remota)"))
        mon.form.addRow(tr("Medir latencia contra:"), self.latency_host)

        adv = QWidget()
        adv_lay = QVBoxLayout(adv)
        adv_lay.setContentsMargins(0, 0, 0, 0)
        for w in (vpn, p1, p2, dns, sec, mon):
            adv_lay.addWidget(w)
        self.adv = adv
        adv.setVisible(False)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.addWidget(head)
        body_lay.addLayout(basic)
        body_lay.addSpacing(8)
        body_lay.addWidget(self.adv_btn)
        body_lay.addWidget(adv)
        body_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(scroll)
        if self.orig:
            info = QLabel(f"<small>Archivo: {self.orig.get('file', '')}</small>")
            info.setStyleSheet("color: gray")
            lay.addWidget(info)
        lay.addWidget(buttons)

        self.auth_method.currentIndexChanged.connect(self._update_visibility)
        self.user_auth.currentIndexChanged.connect(self._update_visibility)
        self.eap_method.currentIndexChanged.connect(self._update_visibility)
        if data:
            self._load(data)
        else:
            self._defaults()
        self.keyring_has = bool(meta.get("_keyring"))
        if self.keyring_has and data and not self.copying:
            set_combo(self.user_auth, "save")
            self.password.setPlaceholderText(tr("•••• en el llavero (vacío = no cambiar)"))
        self.description.setText(meta.get("description", ""))
        self.app_autoconnect.setChecked(meta.get("autoconnect", False))
        self.always_up.setChecked(meta.get("always_up", False))
        self.untrusted_auto.setChecked(meta.get("untrusted_auto", False))
        set_combo(self.otp, meta.get("otp", "none"))
        set_combo(self.dns_mode, meta.get("dns_mode", "none"))
        self.dns_domains.setText(", ".join(meta.get("dns_domains", [])))
        self.dns_servers.setText(", ".join(meta.get("dns_servers", [])))
        self.killswitch.setChecked(meta.get("killswitch", False))
        self.ks_lan.setChecked(meta.get("killswitch_lan", True))
        self.ks_fwd.setChecked(meta.get("killswitch_fwd", True))
        self.latency_host.setText(meta.get("latency_host", ""))
        if self.copying:
            self.name.setText(f"{data['name']}-copia")
            self.psk.setText(copy_secrets.get("psk", ""))
            self.password.setText(copy_secrets.get("password", ""))
            self.psk.setPlaceholderText("")
            self.password.setPlaceholderText("")
        self._update_visibility()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _list_certs():
        try:
            return [f for f in os.listdir("/etc/swanctl/x509") if not f.startswith(".")]
        except OSError:
            return []

    def _toggle_echo(self, on):
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        self.password.setEchoMode(mode)
        self.psk.setEchoMode(mode)

    def _toggle_advanced(self, on):
        self.adv_btn.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.adv.setVisible(on)

    def _esp_default_toggled(self, on):
        self.pfs.setEnabled(not on)
        self.pfs_group.setEnabled(not on and self.pfs.isChecked())

    def _update_visibility(self):
        v2 = self.ike_v2.isChecked()
        cert = self.auth_method.currentData() == "cert"
        mode = self.user_auth.currentData()
        user = mode != "none"
        eap_tls = v2 and user and self.eap_method.currentData() == "eap-tls"
        b = self.basic
        b.setRowVisible(self.psk, not cert)
        # client cert: pure certificate auth, IKEv1 cert+XAuth, or EAP-TLS
        b.setRowVisible(self.cert, cert and (not user or not v2 or eap_tls))
        b.setRowVisible(self.username, user and not eap_tls)
        b.setRowVisible(self.pw_row, mode == "save" and not eap_tls)
        b.labelForField(self.user_auth).setText(tr("Autenticación (EAP):") if v2 else tr("Autenticación (XAuth):"))
        self.p1.form.setRowVisible(self.eap_method, v2 and user)
        self.p1.form.setRowVisible(self.mobike, v2)
        self.vpn_section.form.setRowVisible(self.mode_box, not v2)
        b.setRowVisible(self.otp, user and not eap_tls)
        dmode = self.dns_mode.currentData()
        self.dns_section.form.setRowVisible(self.dns_domains, dmode == "split")
        self.dns_section.form.setRowVisible(self.dns_servers, dmode != "none")
        self.sec_section.form.setRowVisible(self.ks_lan, self.killswitch.isChecked())
        self.sec_section.form.setRowVisible(self.ks_fwd, self.killswitch.isChecked())

    def _defaults(self):
        self.ike_v2.setChecked(True)
        self.mode_main.setChecked(True)
        set_combo(self.user_auth, "save")
        self.mode_cfg.setChecked(True)
        self.fragmentation.setChecked(True)
        self.all_traffic.setChecked(True)
        self.remote_ts.setEnabled(False)
        self.remote_id.setText("%any")
        self.ike_prop.set_rows([("aes256", "sha256"), ("aes128", "sha256")])
        self.dh_checks["modp2048"].setChecked(True)
        self.esp_prop.set_rows([("aes256", "sha256"), ("aes128", "sha256")])
        self.ike_life.setValue(86400)
        self.esp_life.setValue(43200)
        self.dpd.setChecked(True)
        self.dpd_delay.setValue(30)
        self.replay.setChecked(True)
        self.pfs.setChecked(True)
        self.mobike.setChecked(True)

    def _load(self, d):
        self.name.setText(d["name"])
        self.remote_addrs.setText(d["remote_addrs"])
        (self.ike_v1 if d["version"] == "1" else self.ike_v2).setChecked(True)
        (self.mode_aggr if d.get("aggressive") else self.mode_main).setChecked(True)
        set_combo(self.auth_method, "psk" if d["remote_auth"] == "psk" else "cert")
        eap = d["local_auth"].startswith("eap")
        user = eap or d.get("xauth")
        has_pw = d["has_eap"] if eap else d.get("has_xauth")
        if not user:
            set_combo(self.user_auth, "none")
        else:
            set_combo(self.user_auth, "save" if has_pw else "prompt")
        if eap:
            set_combo(self.eap_method, d["local_auth"])
            self.username.setText(d["eap_id"] or d["local_id"])
        else:
            self.username.setText(d.get("xauth_id", ""))
        keep = tr("•••• guardada (vacío = no cambiar)")
        self.password.setPlaceholderText(keep if has_pw else "")
        self.psk.setPlaceholderText(keep if d["has_psk"] else "")
        self.cert.setCurrentText(d["local_certs"])
        ts = d["remote_ts"].replace(" ", "")
        if ts in ("0.0.0.0/0", "0.0.0.0/0,::/0"):
            self.all_traffic.setChecked(True)
            self.remote_ts.setEnabled(False)
        else:
            self.split.setChecked(True)
            self.remote_ts.setText(d["remote_ts"])
        self.autostart.setChecked(d["start_action"] == "start")

        self.mode_cfg.setChecked(d["vips"])
        self.fragmentation.setChecked(d.get("fragmentation", True))
        self.local_ts.setText(d["local_ts"])
        self.local_id.setText(d["local_id"])
        self.remote_id.setText(d["remote_id"])

        for prop, editor, ike in ((d["proposals"], self.ike_prop, True),
                                  (d["esp_proposals"], self.esp_prop, False)):
            parsed = parse_proposals(prop)
            if not prop.strip() or prop.strip() == "default":
                editor.set_rows([("aes256", "sha256"), ("aes128", "sha256")], default=True)
            elif parsed is None:
                editor.set_raw(prop)
            else:
                editor.set_rows(parsed[0])
                if ike:
                    for dh in parsed[1]:
                        self.dh_checks[dh].setChecked(True)
                else:
                    self.pfs.setChecked(bool(parsed[1]))
                    if parsed[1]:
                        set_combo(self.pfs_group, parsed[1][0])
        if not any(cb.isChecked() for cb in self.dh_checks.values()):
            self.dh_checks["modp2048"].setChecked(True)
        self.pfs_group.setEnabled(self.pfs.isChecked())

        self.ike_life.setValue(parse_seconds(d.get("rekey_time")))
        self.esp_life.setValue(parse_seconds(d.get("child_rekey_time")))
        self.esp_kb.setValue(parse_bytes_kb(d.get("child_rekey_bytes")))
        delay = parse_seconds(d["dpd_delay"])
        self.dpd.setChecked(delay > 0)
        self.dpd_delay.setValue(delay or 30)
        self.dpd_delay.setEnabled(delay > 0)
        set_combo(self.dpd_action, d["dpd_action"] or "restart")
        self.encap.setChecked(d.get("encap", False))
        self.mobike.setChecked(d.get("mobike", True))
        self.replay.setChecked(d.get("replay_window", "") != "0")

    def _proposal_text(self, editor, ike):
        if editor.default.isChecked():
            return ""
        if editor.raw_mode:
            return editor.raw.text().strip()
        if ike:
            dhs = [v for v, cb in self.dh_checks.items() if cb.isChecked()]
        else:
            dhs = [self.pfs_group.currentData()] if self.pfs.isChecked() else []
        return build_proposals(editor.get_rows(), dhs, ike)

    def _accept(self):
        if not self.name.text().strip() or not self.remote_addrs.text().strip():
            QMessageBox.warning(self, APP, tr("El nombre de la conexión y el gateway remoto son obligatorios."))
            return
        if (not self.ike_prop.default.isChecked() and not self.ike_prop.raw_mode
                and not any(cb.isChecked() for cb in self.dh_checks.values())):
            QMessageBox.warning(self, APP, tr("Selecciona al menos un grupo DH en la Fase 1."))
            return
        if self.dns_mode.currentData() == "split" and not self.dns_domains.text().strip():
            QMessageBox.warning(self, APP, tr("Indica los dominios de la empresa para el DNS dividido (por ejemplo: empresa.local)."))
            return
        if self.split.isChecked() and not self.remote_ts.text().strip():
            QMessageBox.warning(self, APP, tr("Indica las redes remotas o elige «Todo el tráfico»."))
            return
        if (self.auth_method.currentData() == "psk" and not self.psk.text()
                and (self.copying or not self.src.get("has_psk"))):
            QMessageBox.warning(self, APP, tr("Escribe la clave precompartida."))
            return
        mode = self.user_auth.currentData()
        eap_tls = self.ike_v2.isChecked() and self.eap_method.currentData() == "eap-tls"
        if mode != "none" and not eap_tls and not self.username.text().strip():
            QMessageBox.warning(self, APP, tr("Escribe el usuario."))
            return
        if mode == "save" and not eap_tls and not self.password.text():
            had = self.src.get("has_eap") or self.src.get("has_xauth") or self.keyring_has
            if self.copying or not had:
                QMessageBox.warning(self, APP, tr("Escribe la contraseña, o elige «Pedir al iniciar sesión» si prefieres no guardarla."))
                return
        self.accept()

    def meta(self):
        def split_list(text):
            return [x.strip() for x in re.split(r"[,\s]+", text) if x.strip()]

        return {"description": self.description.text().strip(),
                "autoconnect": self.app_autoconnect.isChecked(),
                "always_up": self.always_up.isChecked(),
                "untrusted_auto": self.untrusted_auto.isChecked(),
                "otp": self.otp.currentData() if self.user_auth.currentData() != "none" else "none",
                "dns_mode": self.dns_mode.currentData(),
                "dns_domains": split_list(self.dns_domains.text()),
                "dns_servers": split_list(self.dns_servers.text()),
                "killswitch": self.killswitch.isChecked(),
                "killswitch_lan": self.ks_lan.isChecked(),
                "killswitch_fwd": self.ks_fwd.isChecked(),
                "latency_host": self.latency_host.text().strip()}

    def _apply_template(self):
        t = TEMPLATES.get(self.template.currentData())
        if not t:
            return
        (self.ike_v2 if t["version"] == "2" else self.ike_v1).setChecked(True)
        (self.mode_aggr if t.get("aggressive") else self.mode_main).setChecked(True)
        set_combo(self.auth_method, t["auth"])
        set_combo(self.user_auth, t["user"])
        if t.get("eap"):
            set_combo(self.eap_method, t["eap"])
        self.ike_prop.set_rows(t["ike"], default=t.get("default_props", False))
        for val, cb in self.dh_checks.items():
            cb.setChecked(val in t["dh"])
        self.esp_prop.set_rows(t["esp"], default=t.get("default_props", False))
        self.pfs.setChecked(bool(t.get("pfs")))
        if t.get("pfs"):
            set_combo(self.pfs_group, t["pfs"])
        self.ike_life.setValue(t.get("ike_life", 86400))
        self.esp_life.setValue(t.get("esp_life", 43200))
        self.mobike.setChecked(t.get("mobike", True))
        self.remote_id.setText("%any")
        self._update_visibility()

    def values(self):
        name = self.name.text().strip()
        v2 = self.ike_v2.isChecked()
        cert = self.auth_method.currentData() == "cert"
        mode = self.user_auth.currentData()
        user = mode != "none"
        username = self.username.text().strip()
        eap = v2 and user
        local_auth = self.eap_method.currentData() if eap else ("pubkey" if cert else "psk")
        password = self.password.text() if mode == "save" else ""
        remote_ts = "0.0.0.0/0" if self.all_traffic.isChecked() else \
            ",".join(p.strip() for p in self.remote_ts.text().split(",") if p.strip())
        return {
            "orig_name": self.orig.get("name", ""),
            "name": name,
            "remote_addrs": ",".join(p.strip() for p in self.remote_addrs.text().split(",") if p.strip()),
            "local_addrs": self.src.get("local_addrs", ""),
            "version": "2" if v2 else "1",
            "vips": self.mode_cfg.isChecked(),
            "aggressive": not v2 and self.mode_aggr.isChecked(),
            "fragmentation": self.fragmentation.isChecked(),
            "proposals": self._proposal_text(self.ike_prop, True),
            "esp_proposals": self._proposal_text(self.esp_prop, False),
            "rekey_time": f"{self.ike_life.value()}s" if self.ike_life.value() else "",
            "child_rekey_time": f"{self.esp_life.value()}s" if self.esp_life.value() else "",
            "child_rekey_bytes": f"{self.esp_kb.value()}k" if self.esp_kb.value() else "",
            "replay_window": "" if self.replay.isChecked() else "0",
            "dpd_delay": f"{self.dpd_delay.value()}s" if self.dpd.isChecked() else "",
            "dpd_action": self.dpd_action.currentData() if self.dpd.isChecked() else "",
            "encap": self.encap.isChecked(),
            "mobike": self.mobike.isChecked() or not v2,
            "local_auth": local_auth,
            "local_id": self.local_id.text().strip(),
            "eap_id": username if eap else "",
            "local_certs": self.cert.currentText().strip() if cert else "",
            "remote_auth": "pubkey" if cert else "psk",
            "remote_id": self.remote_id.text().strip(),
            "xauth": not v2 and user,
            "xauth_id": username if (not v2 and user) else "",
            "child_name": (self.orig.get("child_name") if self.orig else "") or name,
            "local_ts": self.local_ts.text().strip(),
            "remote_ts": remote_ts,
            "start_action": "start" if self.autostart.isChecked() else "",
            "psk": self.psk.text() if not cert else "",
            "eap_password": password if eap else "",
            "xauth_password": password if (not v2 and user) else "",
            "clear_user_secret": mode != "save",
            "user_mode": mode,
        }


class PasswordDialog(QDialog):
    def __init__(self, parent, name, user, need_password=True, otp="none"):
        super().__init__(parent)
        self.setWindowTitle(tr("Conectar {0}").format(name))
        self.setWindowIcon(shield_icon("connecting"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.code = QLineEdit()
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(12)
        self.remember = QCheckBox(tr("Recordar hasta cerrar SwanGUI (para reconectar)"))
        self.remember.setChecked(True)
        form = QFormLayout()
        form.addRow(QLabel(tr("<b>{0}</b> requiere autenticación.").format(name)))
        form.addRow(tr("Usuario:"), QLabel(user or tr("(sin usuario)")))
        if need_password:
            form.addRow(tr("Contraseña:"), self.password)
        if otp == "append":
            form.addRow(tr("Código OTP:"), self.code)
        if need_password and otp == "none":
            form.addRow("", self.remember)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Conectar"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.setMinimumWidth(380)
        (self.password if need_password else self.code).setFocus()


class ExportDialog(QDialog):
    def __init__(self, parent, names, selected):
        super().__init__(parent)
        self.setWindowTitle(tr("Exportar conexiones VPN"))
        self.list = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if (n == selected or not selected) else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.fmt = combo([(key, label) for key, label, _suffix, _fn in EXPORT_FORMATS])
        self.secrets = QCheckBox(tr("Incluir contraseñas y claves (en texto plano)"))
        warn = QLabel(tr("Si incluyes contraseñas, el archivo permitirá conectarse a la VPN: guárdalo en un lugar seguro."))
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #d29922")
        self.secrets.toggled.connect(warn.setVisible)
        warn.setVisible(False)
        self.encrypt = QCheckBox(tr("Proteger el archivo con una contraseña (cifrado)"))
        self.pass1 = QLineEdit()
        self.pass1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass1.setPlaceholderText(tr("mínimo 8 caracteres"))
        self.pass2 = QLineEdit()
        self.pass2.setEchoMode(QLineEdit.EchoMode.Password)
        if not crypto_available():
            self.encrypt.setEnabled(False)
            self.encrypt.setToolTip(tr("Instala python-cryptography para poder cifrar exportaciones"))
        self.form = form = QFormLayout()
        form.addRow(tr("Conexiones:"), self.list)
        form.addRow(tr("Formato:"), self.fmt)
        form.addRow("", self.secrets)
        form.addRow("", warn)
        form.addRow("", self.encrypt)
        form.addRow(tr("Contraseña del archivo:"), self.pass1)
        form.addRow(tr("Repetir contraseña:"), self.pass2)
        self.secrets.toggled.connect(lambda on: self.encrypt.setChecked(on and self.encrypt.isEnabled()))
        self.encrypt.toggled.connect(self._enc_visibility)
        self.encrypt.toggled.connect(lambda on: warn.setText(
            tr("El archivo va cifrado: sin su contraseña no sirve para nada. No la guardes junto al archivo.")
            if on else tr("Si incluyes contraseñas, el archivo permitirá conectarse a la VPN: guárdalo en un lugar seguro.")))
        self.fmt.currentIndexChanged.connect(self._enc_visibility)
        self._enc_visibility()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Exportar…"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.resize(460, 360)

    def _enc_visibility(self):
        on = self.encrypt.isChecked()
        self.form.setRowVisible(self.pass1, on)
        self.form.setRowVisible(self.pass2, on)

    def accept(self):
        if self.encrypt.isChecked():
            if len(self.pass1.text()) < 8:
                QMessageBox.warning(self, APP, tr("La contraseña del archivo debe tener al menos 8 caracteres."))
                return
            if self.pass1.text() != self.pass2.text():
                QMessageBox.warning(self, APP, tr("Las contraseñas no coinciden."))
                return
        super().accept()

    def passphrase(self):
        return self.pass1.text() if self.encrypt.isChecked() else ""

    def chosen(self):
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class PrefsDialog(QDialog):
    def __init__(self, parent, settings, conn_names):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("Preferencias de SwanGUI"))
        self.resize(560, 440)
        tabs = QTabWidget()

        # --- general
        self.autostart = QCheckBox(tr("Iniciar SwanGUI al iniciar sesión"))
        self.autostart.setChecked(settings.autostart)
        self.notify = QCheckBox(tr("Mostrar notificaciones al conectar y desconectar"))
        self.notify.setChecked(settings.data.get("notifications", True))
        self.start_hidden = QCheckBox(tr("Al iniciar, mostrar solo el icono de la bandeja"))
        self.start_hidden.setChecked(settings.data.get("start_hidden", True))
        self.keyring = QCheckBox(tr("Guardar contraseñas de usuario en el llavero del sistema (gnome-keyring)"))
        self.keyring.setChecked(settings.data.get("use_keyring", False))
        self.keyring.setEnabled(Keyring.available())
        self.keyring.setToolTip(tr("Las contraseñas nuevas o modificadas se guardan cifradas en tu llavero en vez de en /etc/swanctl. La clave precompartida sigue en strongSwan."))
        self.cert_days = QSpinBox()
        self.cert_days.setRange(1, 365)
        self.cert_days.setSuffix(tr(" días antes"))
        self.cert_days.setValue(settings.data.get("cert_warn_days", 30))
        g = QFormLayout()
        g.addRow("", self.autostart)
        g.addRow("", self.start_hidden)
        g.addRow("", self.notify)
        self.notify_style = combo([("balloon", tr("Globo de SwanGUI junto al icono")),
                                   ("system", tr("Notificaciones del escritorio"))])
        set_combo(self.notify_style, settings.data.get("notify_style", "balloon"))
        self.notify.toggled.connect(self.notify_style.setEnabled)
        self.notify_style.setEnabled(self.notify.isChecked())
        g.addRow(tr("Estilo de los avisos:"), self.notify_style)
        g.addRow("", self.keyring)
        g.addRow(tr("Avisar caducidad de certificados:"), self.cert_days)
        self.tray_theme = combo([("auto", tr("Automático (según el tema)")), ("light", tr("Panel claro (borde oscuro)")),
                                 ("dark", tr("Panel oscuro (borde claro)"))])
        set_combo(self.tray_theme, settings.data.get("tray_theme", "auto"))
        g.addRow(tr("Icono de la bandeja:"), self.tray_theme)
        self.language = combo(LANGUAGES)
        set_combo(self.language, settings.data.get("language", "auto"))
        g.addRow(tr("Idioma (al reiniciar SwanGUI):"), self.language)
        self.check_updates = QCheckBox(tr("Buscar actualizaciones en GitHub una vez al día"))
        self.check_updates.setChecked(settings.data.get("check_updates", True))
        g.addRow("", self.check_updates)
        w = QWidget()
        w.setLayout(g)
        tabs.addTab(w, tr("General"))

        # --- trusted networks
        self.trusted = QListWidget()
        active = {n for n, _ in nm_connections(active_only=True)}
        known = dict(nm_connections())
        for n in settings.data.get("trusted", []):
            known.setdefault(n, "")
        for n in sorted(known):
            label = f"{n}   {'(Wi-Fi)' if known[n] == '802-11-wireless' else ''}" \
                    f"{'  ← conectada ahora' if n in active else ''}"
            it = QListWidgetItem(label.strip())
            it.setData(Qt.ItemDataRole.UserRole, n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if n in settings.data.get("trusted", [])
                             else Qt.CheckState.Unchecked)
            self.trusted.addItem(it)
        t = QVBoxLayout()
        lbl = QLabel(tr("Marca las redes donde <b>no</b> necesitas VPN (por ejemplo, la red de la oficina). Las conexiones con «Conectar sola fuera de mis redes de confianza» se conectan al salir de ellas y se desconectan al volver."))
        lbl.setWordWrap(True)
        t.addWidget(lbl)
        t.addWidget(self.trusted)
        w = QWidget()
        w.setLayout(t)
        tabs.addTab(w, tr("Redes de confianza"))

        # --- shortcuts / CLI
        self.sc_conn = QComboBox()
        self.sc_conn.addItems(conn_names)
        self.sc_action = combo([("toggle", tr("Conectar / desconectar")), ("connect", tr("Conectar")),
                                ("disconnect", tr("Desconectar"))])
        self.sc_keys = QKeySequenceEdit()
        add = QPushButton(tr("Crear atajo"))
        add.clicked.connect(self._add_shortcut)
        self.sc_list = QListWidget()
        for sc in settings.data.get("shortcuts", []):
            self._list_shortcut(sc)
        rm = QPushButton(tr("Quitar atajo seleccionado"))
        rm.clicked.connect(self._remove_shortcut)
        s = QFormLayout()
        s.addRow(tr("Conexión:"), self.sc_conn)
        s.addRow(tr("Acción:"), self.sc_action)
        s.addRow(tr("Teclas:"), self.sc_keys)
        s.addRow("", add)
        s.addRow(tr("Atajos creados:"), self.sc_list)
        s.addRow("", rm)
        cli = QLabel(tr("<small>También desde la terminal: <code>swangui status</code>, <code>swangui connect NOMBRE</code>, <code>swangui disconnect NOMBRE</code>, <code>swangui toggle NOMBRE</code>, <code>swangui list</code>.</small>"))
        cli.setWordWrap(True)
        cli.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        s.addRow(cli)
        w = QWidget()
        w.setLayout(s)
        tabs.addTab(w, tr("Atajos y terminal"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

    def _list_shortcut(self, sc):
        it = QListWidgetItem(f"{sc['keys']}  →  swangui {sc['action']} {sc['conn']}")
        it.setData(Qt.ItemDataRole.UserRole, sc)
        self.sc_list.addItem(it)

    def _add_shortcut(self):
        keys = self.sc_keys.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if not keys or not self.sc_conn.currentText():
            QMessageBox.warning(self, APP, tr("Elige la conexión y pulsa la combinación de teclas."))
            return
        sc = {"keys": keys, "conn": self.sc_conn.currentText(), "action": self.sc_action.currentData()}
        try:
            xfce_set_shortcut(keys, f"swangui {sc['action']} {sc['conn']}")
        except (OSError, subprocess.SubprocessError) as e:
            QMessageBox.critical(self, APP, tr("No se pudo crear el atajo en XFCE:\n{0}").format(e))
            return
        self.settings.data.setdefault("shortcuts", []).append(sc)
        self.settings.save()
        self._list_shortcut(sc)
        self.sc_keys.clear()

    def _remove_shortcut(self):
        it = self.sc_list.currentItem()
        if not it:
            return
        sc = it.data(Qt.ItemDataRole.UserRole)
        try:
            xfce_remove_shortcut(sc["keys"])
        except (OSError, subprocess.SubprocessError):
            pass
        self.settings.data["shortcuts"] = [x for x in self.settings.data.get("shortcuts", []) if x != sc]
        self.settings.save()
        self.sc_list.takeItem(self.sc_list.row(it))

    def trusted_names(self):
        return [self.trusted.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.trusted.count())
                if self.trusted.item(i).checkState() == Qt.CheckState.Checked]


class RawEditor(QDialog):
    def __init__(self, parent, path, text):
        super().__init__(parent)
        self.setWindowTitle(tr("Editar archivo — {0}").format(path))
        self.resize(720, 560)
        self.edit = QPlainTextEdit(text)
        self.edit.setFont(QFont("monospace", 10))
        warn = QLabel(tr("Edición directa de swanctl.conf. Se guarda una copia de seguridad en /etc/swanctl/swangui-backup antes de escribir."))
        warn.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(warn)
        lay.addWidget(self.edit)
        lay.addWidget(buttons)


class LogWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("Registro — SwanGUI"))
        self.resize(900, 560)
        self.tabs = QTabWidget()
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFont("monospace", 9))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.app_log = QPlainTextEdit(readOnly=True)
        self.app_log.setFont(QFont("monospace", 9))
        self.tabs.addTab(self.text, "strongSwan")
        self.tabs.addTab(self.app_log, tr("Acciones de SwanGUI"))

        self.filter = QComboBox()
        self.filter.addItem(tr("Todas las conexiones"), "")
        self.filter.currentIndexChanged.connect(self.load)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar…"))
        self.search.textChanged.connect(self._render)
        self.since = combo([("1 hour ago", tr("Última hora")), ("today", tr("Hoy")),
                            ("yesterday", tr("Desde ayer")), ("", tr("Todo"))])
        self.since.currentIndexChanged.connect(self.load)
        self.live = QCheckBox(tr("En vivo"))
        self.live.setChecked(True)
        refresh = QPushButton(tr("Actualizar"))
        refresh.clicked.connect(self.load)
        save = QPushButton(tr("Guardar…"))
        save.clicked.connect(self._save)
        top = QHBoxLayout()
        top.addWidget(self.filter)
        top.addWidget(self.since)
        top.addWidget(self.search, 1)
        top.addWidget(self.live)
        top.addWidget(refresh)
        top.addWidget(save)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.tabs)
        self.lines = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.live.isChecked() and self.isVisible() and self.load())
        self.timer.start(3000)
        self.proc = None

    def set_connections(self, names):
        cur = self.filter.currentData()
        have = [self.filter.itemData(i) for i in range(1, self.filter.count())]
        if have == list(names):
            return
        self.filter.blockSignals(True)
        while self.filter.count() > 1:
            self.filter.removeItem(1)
        for n in names:
            self.filter.addItem(n, n)
        i = self.filter.findData(cur)
        self.filter.setCurrentIndex(max(i, 0))
        self.filter.blockSignals(False)

    def append(self, title, text):
        self.app_log.appendPlainText(f"── {time.strftime('%H:%M:%S')} {title} ──\n{text}\n")

    def load(self):
        if self.proc is not None:
            return
        args = ["-u", "strongswan", "--no-pager", "-o", "short-iso", "-n", "2000"]
        if self.since.currentData():
            args += ["--since", self.since.currentData()]
        self.proc = QProcess(self)
        self.proc.finished.connect(self._loaded)
        self.proc.start("journalctl", args)

    def _loaded(self, *_):
        proc, self.proc = self.proc, None
        out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        err = bytes(proc.readAllStandardError()).decode(errors="replace")
        proc.deleteLater()
        self.lines = (out or err).splitlines()
        self._render()

    def _render(self):
        name = self.filter.currentData()
        needle = self.search.text().lower()
        lines = self.lines
        if name:
            lines = [l for l in lines if f"<{name}|" in l or f" {name}[" in l or f"'{name}'" in l]
        if needle:
            lines = [l for l in lines if needle in l.lower()]
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        text = "\n".join(lines) or tr("(sin entradas)")
        if text != self.text.toPlainText():
            self.text.setPlainText(text)
            if at_bottom:
                bar.setValue(bar.maximum())

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar registro"),
                                              os.path.expanduser(f"~/swangui-{time.strftime('%Y%m%d-%H%M')}.log"),
                                              tr("Registro (*.log *.txt)"))
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText() + "\n\n== Acciones ==\n" + self.app_log.toPlainText())

    def showEvent(self, ev):
        super().showEvent(ev)
        self.load()


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# help menu: about, project links, bug reports, donations, updates
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
REPO_URL = "https://github.com/FrankMncrf/SwanGUI"
RELEASES_API = "https://api.github.com/repos/FrankMncrf/SwanGUI/releases/latest"
# donation targets; an empty value hides that option
DONATE = {
    "bmc": "https://buymeacoffee.com/FrankMncrf",
    "paypal": "",
    "btc": "bc1qm45kpx35uazdts3q8vc77edqwyajuxe9cexw0er5409dyevl3nysxhln9g",
}
THIRD_PARTY = [
    ("strongSwan", "GPL-2.0-or-later", "https://www.strongswan.org"),
    ("Qt 6", "LGPL-3.0 / GPL", "https://www.qt.io/licensing"),
    ("PyQt6", "GPL-3.0", "https://www.riverbankcomputing.com/software/pyqt/"),
    ("Python", "PSF-2.0", "https://www.python.org/psf/license/"),
    ("cryptography (python-cryptography)", "Apache-2.0 / BSD-3-Clause", "https://cryptography.io"),
    ("systemd-resolved, nftables, iproute2, NetworkManager, libsecret", "GPL / LGPL",
     "https://www.freedesktop.org"),
]


def open_url(url):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))


def _version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:3]) or (0,)


def system_info(app):
    """Versions and feature usage for bug reports. Never names, addresses, users or secrets."""
    def swan_version():
        try:
            p = subprocess.run(["swanctl", "--version"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return "?"
        m = re.search(r"strongSwan (\S+)", p.stdout + p.stderr)
        return m.group(1) if m else "?"

    distro = "?"
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            distro = dict(line.rstrip().split("=", 1) for line in f if "=" in line).get("PRETTY_NAME", "?").strip('"')
    except OSError:
        pass
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    conns = app.status.get("connections", [])
    metas = [app.settings.conn(c["name"]) for c in conns]
    return "\n".join([
        f"SwanGUI: {VERSION} ({UI_LANG})",
        f"strongSwan: {swan_version()}",
        f"Distro: {distro}",
        f"Kernel: {os.uname().release}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '?')} ({os.environ.get('XDG_SESSION_TYPE', '?')})",
        f"Python: {sys.version.split()[0]} · Qt: {QT_VERSION_STR} · PyQt: {PYQT_VERSION_STR}",
        f"charon: {'ok' if app.status.get('daemon', True) else 'no responde'}",
        f"Connections: {len(conns)} (IKEv1: {sum(c.get('version') == '1' for c in conns)})",
        "DNS modes: " + (", ".join(sorted({m.get('dns_mode', 'none') for m in metas})) or "-"),
        f"Kill switch: {sum(bool(m.get('killswitch')) for m in metas)} · "
        f"Keyring: {'yes' if app.settings.data.get('use_keyring') else 'no'} · "
        f"cryptography: {'yes' if crypto_available() else 'no'}",
    ])


class Balloon(QWidget):
    """SwanGUI's own notification bubble, drawn next to the tray icon (like FortiClient)."""
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.icon = QLabel()
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(260)
        self.text.setMaximumWidth(360)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(self.title)
        col.addWidget(self.text)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 16, 12)
        row.setSpacing(12)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(col)
        self.accent = QColor(COLORS["up"])
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.click_pos = None  # where the user last clicked the tray, when Qt can't tell us the icon's place

    def remember_click(self):
        from PyQt6.QtGui import QCursor
        self.click_pos = QCursor.pos()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.setPen(QPen(pal.mid().color(), 1))
        p.setBrush(pal.window().color())
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent)
        p.drawRoundedRect(3, 10, 4, self.height() - 20, 2, 2)
        p.end()

    def mousePressEvent(self, _event):
        self.hide()
        self.clicked.emit()

    def show_message(self, title, text, state, ms, anchor):
        self.accent = QColor(COLORS.get(state, COLORS["up"]))
        self.icon.setPixmap(shield_icon(state, 40).pixmap(40, 40))
        self.title.setText(title)
        self.text.setText(text)
        self.adjustSize()
        w, h = self.width(), self.height()
        screen = (QGuiApplication.screenAt(anchor.center()) if anchor.isValid() else None) \
            or QGuiApplication.primaryScreen()
        avail, full = screen.availableGeometry(), screen.geometry()
        margin = 8
        if not (anchor.isValid() and anchor.width() > 0) and self.click_pos is not None:
            from PyQt6.QtCore import QRect
            near = QGuiApplication.screenAt(self.click_pos)
            if near is not None:
                g = near.geometry()
                av = near.availableGeometry()
                # only trust the click if it was on the panel strip
                if self.click_pos.y() < av.top() or self.click_pos.y() > av.bottom():
                    anchor = QRect(self.click_pos.x() - 8, g.top() if self.click_pos.y() < av.top() else av.bottom(),
                                   16, (av.top() - g.top()) if self.click_pos.y() < av.top() else (g.bottom() - av.bottom()))
                    screen, avail, full = near, av, g
        if anchor.isValid() and anchor.width() > 0:
            x = anchor.center().x() - w // 2
            # panel at the bottom → above the icon, otherwise below it
            y = anchor.top() - h - margin if anchor.center().y() > full.center().y() else anchor.bottom() + margin
        else:
            # no icon geometry (StatusNotifierItem): corner of the panel side
            x = avail.right() - w - margin
            y = avail.top() + margin if avail.top() > full.top() or avail.bottom() == full.bottom() \
                else avail.bottom() - h - margin
        x = max(avail.left() + margin, min(x, avail.right() - w - margin))
        y = max(avail.top() + margin, min(y, avail.bottom() - h - margin))
        self.move(x, y)
        self.show()
        self.raise_()
        self.timer.start(max(2500, ms))


class TextDialog(QDialog):
    """Read-only rich/markdown text with optional extra buttons."""

    def __init__(self, parent, title, text, markdown=False, buttons=()):
        super().__init__(parent)
        from PyQt6.QtWidgets import QTextBrowser
        self.setWindowTitle(title)
        self.setWindowIcon(shield_icon("up"))
        self.resize(640, 520)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(text) if markdown else view.setHtml(text)
        row = QHBoxLayout()
        for label, fn in buttons:
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(view)
        lay.addLayout(row)


class Help(QObject):
    update_found = pyqtSignal(str, str)   # version, url

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.update_found.connect(self._update_found)

    def _owner(self):
        return self.app.window if self.app.window.isVisible() else None

    def about(self):
        box = QMessageBox(self._owner())
        box.setWindowTitle(tr("Acerca de SwanGUI"))
        box.setIconPixmap(shield_icon("up", 96).pixmap(96, 96))
        box.setText(f"<h2>SwanGUI {VERSION}</h2>"
                    + tr("<p>Cliente VPN IPsec para strongSwan, al estilo de FortiClient.</p>")
                    + f"<p>© 2026 Frank — <a href='{REPO_URL}'>{REPO_URL.replace('https://', '')}</a></p>"
                    + tr("<p>Software libre bajo la licencia GPL-3.0. Sin ninguna garantía.</p>"))
        box.setTextFormat(Qt.TextFormat.RichText)
        donate = box.addButton(tr("Donar…"), QMessageBox.ButtonRole.ActionRole)
        lic = box.addButton(tr("Licencias…"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is donate:
            self.donate()
        elif box.clickedButton() is lic:
            self.licenses()

    def repo(self):
        open_url(REPO_URL)

    def docs(self):
        open_url(REPO_URL + "#readme")

    def report_bug(self):
        from urllib.parse import urlencode
        q = urlencode({"template": "bug_report.yml", "environment": system_info(self.app)})
        open_url(f"{REPO_URL}/issues/new?{q}")

    def feedback(self):
        open_url(f"{REPO_URL}/discussions/new?category=ideas")

    def copy_info(self):
        QApplication.clipboard().setText(system_info(self.app))
        self.app._notify(tr("Información del sistema copiada (sin datos de tus conexiones)."), "up", 3000)

    def donate(self):
        dlg = QDialog(self._owner())
        dlg.setWindowTitle(tr("Apoyar SwanGUI"))
        dlg.setWindowIcon(shield_icon("up"))
        lay = QVBoxLayout(dlg)
        intro = QLabel(tr("SwanGUI es gratuito y libre. Si te resulta útil, puedes apoyar su desarrollo:"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        if DONATE.get("bmc"):
            b = QPushButton("☕  Buy Me a Coffee")
            b.clicked.connect(lambda: open_url(DONATE["bmc"]))
            lay.addWidget(b)
        if DONATE.get("paypal"):
            b = QPushButton("PayPal")
            b.clicked.connect(lambda: open_url(DONATE["paypal"]))
            lay.addWidget(b)
        if DONATE.get("btc"):
            row = QHBoxLayout()
            addr = QLineEdit(DONATE["btc"])
            addr.setReadOnly(True)
            copy = QPushButton(tr("Copiar"))
            copy.clicked.connect(lambda: QApplication.clipboard().setText(DONATE["btc"]))
            wallet = QPushButton(tr("Abrir en mi cartera"))
            wallet.clicked.connect(lambda: open_url(f"bitcoin:{DONATE['btc']}?message=Donaciones%20SwanGUI"))
            row.addWidget(QLabel("₿ Bitcoin:"))
            row.addWidget(addr, 1)
            row.addWidget(copy)
            row.addWidget(wallet)
            lay.addLayout(row)
        star = QPushButton(tr("★  Dar una estrella en GitHub"))
        star.clicked.connect(self.repo)
        lay.addWidget(star)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dlg.reject)
        lay.addWidget(close)
        dlg.setMinimumWidth(460)
        dlg.exec()

    def shortcuts(self):
        w = self.app.window
        rows = "".join(f"<tr><td><b>{a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)}</b></td>"
                       f"<td>{a.text().replace('&', '')}</td></tr>"
                       for a in w.findChildren(QAction) if not a.shortcut().isEmpty())
        cli = tr("Uso: swangui [comando]\n\n  (sin comando)          abre SwanGUI en la bandeja\n  --show                 abre SwanGUI y muestra la ventana\n  status                 estado de las conexiones\n  list                   nombres de las conexiones\n  connect NOMBRE         conecta\n  disconnect NOMBRE      desconecta\n  toggle NOMBRE          conecta o desconecta según el estado\n")
        TextDialog(self._owner(), tr("Atajos de teclado"),
                   f"<h3>{tr('Ventana principal')}</h3><table cellpadding=4>{rows}</table>"
                   f"<h3>{tr('Terminal')}</h3><pre>{cli}</pre>"
                   f"<p>{tr('Puedes crear atajos globales en Preferencias → Atajos y terminal.')}</p>").exec()

    def changelog(self):
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "CHANGELOG.md")
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = f"[{REPO_URL}/releases]({REPO_URL}/releases)"
        TextDialog(self._owner(), tr("Novedades"), text, markdown=True).exec()

    def licenses(self):
        rows = "".join(f"<tr><td><a href='{url}'>{name}</a></td><td>{lic}</td></tr>" for name, lic, url in THIRD_PARTY)
        TextDialog(self._owner(), tr("Licencias"),
                   f"<h3>SwanGUI</h3><p>GPL-3.0-or-later — "
                   f"<a href='https://www.gnu.org/licenses/gpl-3.0.html'>gnu.org/licenses/gpl-3.0</a></p>"
                   f"<h3>{tr('Componentes de terceros')}</h3><table cellpadding=4>{rows}</table>").exec()

    # --- updates
    def check_updates(self, manual=True):
        def worker():
            try:
                req = urllib.request.Request(RELEASES_API, headers={"User-Agent": f"SwanGUI/{VERSION}",
                                                                    "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read(200000))
                tag, url = data.get("tag_name", ""), data.get("html_url", REPO_URL + "/releases")
            except (OSError, ValueError):
                tag, url = "", ""
            self.update_found.emit(tag if manual or tag else "", url if tag else ("" if not manual else "error"))

        self.app.settings.data["update_checked"] = time.strftime("%Y-%m-%d")
        self.app.settings.save()
        threading.Thread(target=worker, daemon=True).start()
        self._manual = manual

    def _update_found(self, tag, url):
        manual = getattr(self, "_manual", False)
        if tag and _version_tuple(tag) > _version_tuple(VERSION):
            msg = tr("Hay una versión nueva de SwanGUI: {0} (tienes {1}).").format(tag, VERSION)
            self.app.log.append(tr("Actualización disponible"), url)
            if manual:
                box = QMessageBox(self._owner())
                box.setWindowTitle(APP)
                box.setText(msg + "\n\n" + tr("Actualízala con el gestor de paquetes de tu distribución o descárgala desde GitHub."))
                see = box.addButton(tr("Ver la versión"), QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Close)
                box.exec()
                if box.clickedButton() is see:
                    open_url(url)
            else:
                self.app._notify(msg, "connecting", 10000)
        elif manual:
            if url == "error":
                QMessageBox.warning(self._owner(), APP, tr("No se pudo consultar GitHub. Revisa tu conexión a internet."))
            else:
                QMessageBox.information(self._owner(), APP, tr("Tienes la última versión ({0}).").format(VERSION))

    def maybe_auto_check(self):
        if self.app.settings.data.get("check_updates", True) and \
                self.app.settings.data.get("update_checked") != time.strftime("%Y-%m-%d"):
            self.check_updates(manual=False)

    def fill_menu(self, m):
        for item in ((tr("Documentación"), self.docs), (tr("Atajos de teclado"), self.shortcuts),
                          (tr("Novedades"), self.changelog), None,
                          (tr("Reportar un error…"), self.report_bug), (tr("Enviar sugerencias…"), self.feedback),
                          (tr("Copiar información del sistema"), self.copy_info), None,
                          (tr("Buscar actualizaciones"), self.check_updates),
                          (tr("Repositorio en GitHub"), self.repo), (tr("Donar…"), self.donate), None,
                          (tr("Licencias de terceros"), self.licenses), (tr("Acerca de SwanGUI"), self.about)):
            if item is None:
                m.addSeparator()
            else:
                m.addAction(item[0], (lambda f=item[1]: f()))


STATE_LABELS = {"up": tr("Conectado"), "connecting": tr("Conectando…"), "down": tr("Desconectado"),
                "error": tr("Error")}


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle(APP)
        self.setWindowIcon(shield_icon("up"))
        self.resize(900, 620)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Conexión"), tr("Estado"), tr("Gateway"), tr("IP virtual"), tr("Tiempo"),
                                   tr("Tráfico ↓ / ↑"), tr("Descripción")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIconSize(QSize(14, 14))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self.update_actions)
        self.tree.itemSelectionChanged.connect(self.update_details)
        self.tree.itemDoubleClicked.connect(lambda *_: self.toggle_selected())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        A = self._action
        self.a_connect = A(tr("Conectar"), lambda: self.app.connect_vpn(self.selected()))
        self.a_disconnect = A(tr("Desconectar"), lambda: self.app.disconnect_vpn(self.selected()))
        self.a_new = A(tr("Nueva conexión…"), self.app.new_connection, "Ctrl+N")
        self.a_edit = A(tr("Editar…"), lambda: self.app.edit_connection(self.selected()), "Ctrl+E")
        self.a_dup = A(tr("Duplicar…"), lambda: self.app.duplicate_connection(self.selected()), "Ctrl+D")
        self.a_raw = A(tr("Editar archivo de configuración…"), lambda: self.app.edit_raw(self.selected()))
        self.a_delete = A(tr("Eliminar"), lambda: self.app.delete_connection(self.selected()), "Del")
        self.a_diag = A(tr("Probar conexión…"), lambda: self.app.diagnose(self.selected()), "Ctrl+T")
        self.a_import = A(tr("Importar…"), self.app.import_file, "Ctrl+I")
        self.a_export = A(tr("Exportar…"), lambda: self.app.export_connections(self.selected()))
        self.a_prefs = A(tr("Preferencias…"), self.app.show_prefs, "Ctrl+,")
        self.a_log = A(tr("Registro"), self.app.show_log, "Ctrl+L")
        self.a_ks_off = A(tr("Desactivar kill switch"), self.app.killswitch_off)
        self.a_quit = A(tr("Salir"), self.app.qapp.quit, "Ctrl+Q")

        mb = self.menuBar()
        m = mb.addMenu(tr("&Archivo"))
        for a in (self.a_new, self.a_import, self.a_export, None, self.a_prefs, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&Conexión"))
        for a in (self.a_connect, self.a_disconnect, None, self.a_diag, None, self.a_edit, self.a_dup,
                  self.a_raw, None, self.a_delete, None, self.a_ks_off):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&Ver"))
        m.addAction(self.a_log)
        self.app.help.fill_menu(mb.addMenu(tr("Ay&uda")))

        def btn(action, label=None):
            b = QPushButton(label or action.text().rstrip("…").replace(tr(" conexión"), ""))
            b.clicked.connect(action.trigger)
            action.changed.connect(lambda: b.setEnabled(action.isEnabled()))
            return b

        row = QHBoxLayout()
        for a in (self.a_connect, self.a_disconnect):
            row.addWidget(btn(a))
        row.addSpacing(12)
        for a in (self.a_new, self.a_edit, self.a_delete):
            row.addWidget(btn(a))
        row.addSpacing(12)
        row.addWidget(btn(self.a_diag, tr("Probar")))
        for a in (self.a_import, self.a_export):
            row.addWidget(btn(a))
        row.addStretch()
        row.addWidget(btn(self.a_log))

        # --- details panel
        self.d = {}
        form = QFormLayout()
        for key, label in (("state", tr("Estado")), ("vip", tr("IP virtual")), ("server", tr("Servidor")),
                           ("since", tr("Conectado desde")), ("total", tr("Tráfico total")),
                           ("dns", "DNS"), ("ks", "Kill switch"), ("cert", tr("Certificado"))):
            self.d[key] = QLabel("—")
            self.d[key].setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(f"{label}:", self.d[key])
        self.graph = TrafficGraph()
        details = QGroupBox(tr("Detalles"))
        dl = QHBoxLayout(details)
        dl.addLayout(form, 2)
        dl.addWidget(self.graph, 3)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(details)
        split.setSizes([260, 200])
        conns = QWidget()
        cl = QVBoxLayout(conns)
        cl.addWidget(split)
        cl.addLayout(row)

        # --- history tab
        self.hist = QTreeWidget()
        self.hist.setHeaderLabels([tr("Conexión"), tr("Inicio"), tr("Fin"), tr("Duración"), tr("Descarga"), tr("Subida"),
                                   tr("IP virtual"), tr("Motivo")])
        self.hist.setRootIsDecorated(False)
        self.hist.setAlternatingRowColors(True)
        self.hist.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.hist_summary = QLabel()
        h_refresh = QPushButton(tr("Actualizar"))
        h_refresh.clicked.connect(self.load_history)
        h_csv = QPushButton(tr("Exportar CSV…"))
        h_csv.clicked.connect(self._history_csv)
        h_clear = QPushButton(tr("Borrar historial"))
        h_clear.clicked.connect(self._history_clear)
        hrow = QHBoxLayout()
        hrow.addWidget(self.hist_summary)
        hrow.addStretch()
        for b in (h_refresh, h_csv, h_clear):
            hrow.addWidget(b)
        hist = QWidget()
        hl = QVBoxLayout(hist)
        hl.addWidget(self.hist)
        hl.addLayout(hrow)

        self.tabs = QTabWidget()
        self.tabs.addTab(conns, tr("Conexiones"))
        self.tabs.addTab(hist, tr("Historial"))
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.load_history())

        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(self.tabs)
        lay.addWidget(self.status)
        self.setCentralWidget(central)
        self.update_actions()

    def _action(self, text, slot, shortcut=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(shortcut)
        self.addAction(a)
        return a

    def selected(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def toggle_selected(self):
        name = self.selected()
        if name:
            if self.app.state_of(name) in ("up", "connecting"):
                self.app.disconnect_vpn(name)
            else:
                self.app.connect_vpn(name)

    def _context_menu(self, pos):
        if self.tree.itemAt(pos) is None:
            return
        m = QMenu(self)
        for a in (self.a_connect, self.a_disconnect, None, self.a_diag, None, self.a_edit, self.a_dup,
                  self.a_raw, self.a_export, None, self.a_delete):
            m.addSeparator() if a is None else m.addAction(a)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def update_actions(self):
        name = self.selected()
        st = self.app.state_of(name) if name else None
        self.a_connect.setEnabled(bool(name) and st in ("down", "error"))
        self.a_disconnect.setEnabled(bool(name) and st in ("up", "connecting"))
        for a in (self.a_edit, self.a_dup, self.a_raw, self.a_delete, self.a_diag):
            a.setEnabled(bool(name))
        self.a_export.setEnabled(bool(self.app.status.get("connections")))
        self.a_ks_off.setEnabled(bool(self.app.status.get("killswitch")))

    def update_details(self):
        name = self.selected()
        if not name:
            for lbl in self.d.values():
                lbl.setText("—")
            self.graph.set_samples([])
            return
        st = self.app.state_of(name)
        sa = self.app.status.get("sas", {}).get(name, {})
        meta = self.app.settings.conn(name)
        info = self.app.conn_info(name)
        self.d["state"].setText(STATE_LABELS[st])
        self.d["vip"].setText(", ".join(sa.get("vips", [])) or "—")
        self.d["server"].setText(f"{info.get('remote', '')}  ({sa['remote_host']})" if sa.get("remote_host")
                                 else info.get("remote", ""))
        self.d["since"].setText(time.strftime("%d/%m %H:%M:%S", time.localtime(time.time() - sa["established"]))
                                + f"  ({human_time(sa['established'])})" if st == "up" else "—")
        self.d["total"].setText(f"↓ {human_bytes(sa['bytes_in'])}   ↑ {human_bytes(sa['bytes_out'])}"
                                if st == "up" else "—")
        mode = dict(DNS_MODES).get(meta.get("dns_mode", "none"))
        active = name in self.app.status.get("dns", [])
        doms = ", ".join(meta.get("dns_domains", []))
        self.d["dns"].setText(f"{mode}{' — ' + doms if doms and meta.get('dns_mode') == 'split' else ''}"
                              f"{'  ✓ aplicado' if active else ''}")
        ks = name in self.app.status.get("killswitch", [])
        self.d["ks"].setText((tr("ACTIVO — bloqueando tráfico") if ks and st != "up" else "activo")
                             if ks else (tr("configurado (se activa al conectar)") if meta.get("killswitch") else "no"))
        self.d["cert"].setText(self.app.cert_text(info.get("certs", "")) or "—")
        self.graph.set_samples(list(self.app.samples.get(name, [])))

    def refresh(self, status):
        current = self.selected()
        self.tree.blockSignals(True)
        self.tree.clear()
        for c in status.get("connections", []):
            name = c["name"]
            st = self.app.state_of(name)
            sa = status.get("sas", {}).get(name, {})
            meta = self.app.settings.conn(name)
            label = STATE_LABELS[st]
            if st == "connecting" and sa:
                label += f" ({sa.get('state', '').lower()})"
            flags = []
            if meta.get("always_up"):
                flags.append("siempre")
            if meta.get("autoconnect"):
                flags.append("auto")
            if meta.get("untrusted_auto"):
                flags.append(tr("fuera de confianza"))
            if name in status.get("killswitch", []):
                flags.append("🔒 kill switch")
            warn = self.app.cert_warning(c.get("certs", ""))
            if warn:
                flags.append(f"⚠ {warn}")
            if flags:
                label += f"  [{', '.join(flags)}]"
            item = QTreeWidgetItem([
                name, label, c.get("remote", ""),
                ", ".join(sa.get("vips", [])) if st == "up" else "",
                human_time(sa["established"]) if st == "up" else "",
                f"{human_bytes(sa['bytes_in'])} / {human_bytes(sa['bytes_out'])}" if st == "up" else "",
                meta.get("description", ""),
            ])
            item.setIcon(0, dot_icon(st))
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            tip = [f"Gateway: {c.get('remote', '')}"]
            if c.get("user"):
                tip.append(tr("Usuario: {0}").format(c['user']) + (tr("  (contraseña al conectar)") if c.get("prompt") else ""))
            item.setToolTip(0, "\n".join(tip))
            if st == "up":
                f = item.font(0)
                f.setBold(True)
                item.setFont(0, f)
            self.tree.addTopLevelItem(item)
            if name == current:
                item.setSelected(True)
        if self.tree.topLevelItemCount() and not self.tree.selectedItems():
            self.tree.topLevelItem(0).setSelected(True)
        self.tree.blockSignals(False)
        msgs = []
        if not status.get("daemon", True):
            msgs.append(tr("⚠ strongSwan (charon) no responde: ") + status.get("daemon_msg", ""))
        blocked = [n for n in status.get("killswitch", []) if self.app.state_of(n) != "up"]
        if blocked:
            msgs.append(tr("🔒 Kill switch bloqueando tráfico ({0}) — Conexión › Desactivar kill switch para liberar").format(', '.join(blocked)))
        if self.app.on_trusted_network():
            msgs.append(tr("Red de confianza"))
        msgs.append(tr("Actualizado {0}").format(time.strftime('%H:%M:%S')))
        self.status.setText("   ·   ".join(msgs))
        self.update_actions()
        self.update_details()

    def load_history(self):
        self.hist.clear()
        total_time = total_in = total_out = 0
        for e in History.load():
            dur = e.get("end", 0) - e.get("start", 0)
            total_time += dur
            total_in += e.get("bytes_in", 0)
            total_out += e.get("bytes_out", 0)
            it = QTreeWidgetItem([
                e.get("name", ""),
                time.strftime("%d/%m/%Y %H:%M", time.localtime(e.get("start", 0))),
                time.strftime("%d/%m/%Y %H:%M", time.localtime(e.get("end", 0))),
                human_time(dur), human_bytes(e.get("bytes_in", 0)), human_bytes(e.get("bytes_out", 0)),
                e.get("vip", ""), e.get("reason", ""),
            ])
            self.hist.addTopLevelItem(it)
        self.hist_summary.setText(f"{self.hist.topLevelItemCount()} sesiones · {human_time(total_time)} · "
                                  f"↓ {human_bytes(total_in)} · ↑ {human_bytes(total_out)}")

    def _history_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar historial"),
                                              os.path.expanduser("~/swangui-historial.csv"), tr("CSV (*.csv)"))
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["conexion", "inicio", "fin", "segundos", "bytes_descarga", "bytes_subida",
                        "ip_virtual", "motivo"])
            for e in History.load(limit=100000):
                w.writerow([e.get("name"), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("start", 0))),
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("end", 0))),
                            e.get("end", 0) - e.get("start", 0), e.get("bytes_in", 0),
                            e.get("bytes_out", 0), e.get("vip", ""), e.get("reason", "")])

    def _history_clear(self):
        if QMessageBox.question(self, APP, "¿Borrar todo el historial de conexiones?") \
                == QMessageBox.StandardButton.Yes:
            History.clear()
            self.load_history()

    def closeEvent(self, ev):
        ev.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# application / tray
# ---------------------------------------------------------------------------

RETRY_DELAYS = [10, 20, 40, 60, 120, 300]
GRAPH_POINTS = 90


class SwanGUI(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.settings = Settings()
        self.helper = Helper(self)
        self.status = {"connections": [], "sas": {}, "daemon": True, "dns": [], "killswitch": []}
        self.busy = {}             # name -> "connecting" | "disconnecting"
        self.errors = set()        # names whose last connect failed
        self.prev_states = {}
        self.session_pw = {}       # name -> password typed at "prompt on login"
        self.manual_down = set()   # disconnected by the user: don't auto-reconnect
        self.retry = {}            # name -> (next attempt timestamp, attempt number)
        self.auto_by_rule = set()  # connected because we left the trusted networks
        self.dns_pending = set()
        self.dns_retry = {}        # name -> earliest time to retry a failed DNS apply
        self.dns_warned = set()    # DNS problem already notified during this session of the tunnel
        self.clock = None          # (wall, monotonic) at the last poll, to detect suspend/resume
        self.resume_queue = set()
        self.resume_deadline = 0
        self.connectivity = "unknown"
        self.portal_warned = False
        self.samples = {}          # name -> deque of (t, rx/s, tx/s, latency)
        self.last_bytes = {}       # name -> (t, bytes_in, bytes_out)
        self.latency = {}          # name -> ms
        self.last_sa = {}          # name -> last seen SA (for history)
        self.certs = []
        self.trusted_now = False
        self.polling = False
        self.first_poll = True

        ICON_THEME["mode"] = self.settings.data.get("tray_theme", "auto")
        self.help = Help(self)
        self.window = MainWindow(self)
        self.log = LogWindow(self.window)

        self.tray = QSystemTrayIcon(shield_icon("down"))
        self.tray.setToolTip(APP)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.balloon = Balloon()
        self.balloon.clicked.connect(self.show_window)
        self.tray.activated.connect(lambda _reason: self.balloon.remember_click())
        self.menu.aboutToShow.connect(self.balloon.remember_click)
        self.tray.show()
        self._build_menu()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(POLL_MS)
        self.cert_timer = QTimer(self)
        self.cert_timer.timeout.connect(self.check_certs)
        self.cert_timer.start(3600 * 1000)
        self.poll()
        QTimer.singleShot(5000, self.check_certs)
        QTimer.singleShot(30000, self.help.maybe_auto_check)

    # --- state
    def conn_info(self, name):
        return next((c for c in self.status.get("connections", []) if c["name"] == name), {})

    def state_of(self, name):
        if self.busy.get(name) == "connecting":
            return "connecting"
        sa = self.status.get("sas", {}).get(name)
        if sa:
            if sa["state"] == "ESTABLISHED" and sa["child_up"]:
                return "up"
            return "connecting"
        if name in self.errors:
            return "error"
        return "down"

    def on_trusted_network(self):
        return self.trusted_now

    def poll(self):
        self._check_resume()
        if self.polling:
            return
        self.polling = True
        self.helper.run(["status"], self._polled)

    def _polled(self, data):
        self.polling = False
        if "error" in data:
            self.status["daemon"] = False
            self.status["daemon_msg"] = data["error"]
        else:
            self.status = data
        self._sample_traffic()
        self._history()
        self._notify_changes()
        trusted = set(self.settings.data.get("trusted", []))
        self.trusted_now = bool(trusted & {n for n, _ in nm_connections(active_only=True)}) if trusted else False
        self._check_portal()
        if self.first_poll:
            self.first_poll = False
            self._autoconnect()
        self._network_rules()
        self._keep_up()
        self._maintain_dns()
        self._refresh_now()
        self.log.set_connections([c["name"] for c in self.status.get("connections", [])])

    # --- monitoring
    def _sample_traffic(self):
        now = time.time()
        for name, sa in self.status.get("sas", {}).items():
            if self.state_of(name) != "up":
                continue
            prev = self.last_bytes.get(name)
            self.last_bytes[name] = (now, sa["bytes_in"], sa["bytes_out"])
            if prev and now > prev[0] and sa["bytes_in"] >= prev[1]:
                dt = now - prev[0]
                q = self.samples.setdefault(name, deque(maxlen=GRAPH_POINTS))
                q.append((now, (sa["bytes_in"] - prev[1]) / dt, (sa["bytes_out"] - prev[2]) / dt,
                          self.latency.get(name)))
            self._ping(name)
        for name in list(self.last_bytes):
            if self.state_of(name) != "up":
                self.last_bytes.pop(name, None)
                self.latency.pop(name, None)

    def _latency_target(self, name):
        meta = self.settings.conn(name)
        if meta.get("latency_host"):
            return meta["latency_host"]
        if meta.get("dns_servers"):
            return meta["dns_servers"][0]
        for t in self.conn_info(name).get("remote_ts", "").split(","):
            try:
                net = ipaddress.ip_network(t.strip(), strict=False)
            except ValueError:
                continue
            if net.prefixlen and net.version == 4:
                return str(next(net.hosts()))
        return self.status.get("sas", {}).get(name, {}).get("remote_host", "")

    def _ping(self, name):
        target = self._latency_target(name)
        if not target:
            return
        proc = QProcess(self)

        def done(*_):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
            m = re.search(r"time=([\d.]+)", out)
            self.latency[name] = float(m.group(1)) if m else None
            proc.deleteLater()

        proc.finished.connect(done)
        proc.start("ping", ["-c", "1", "-W", "2", "-n", target])

    def _history(self):
        sas = self.status.get("sas", {})
        for c in self.status.get("connections", []):
            name = c["name"]
            sa = sas.get(name)
            if sa and self.state_of(name) == "up":
                self.last_sa[name] = dict(sa, seen=time.time())
            elif name in self.last_sa:
                last = self.last_sa.pop(name)
                History.add({
                    "name": name, "start": int(last["seen"] - last["established"]), "end": int(time.time()),
                    "bytes_in": last["bytes_in"], "bytes_out": last["bytes_out"],
                    "vip": ", ".join(last.get("vips", [])),
                    "reason": tr("desconexión manual") if name in self.manual_down else tr("conexión perdida"),
                })

    def cert_warning(self, certs):
        days = self._cert_days(certs)
        if days is None:
            return ""
        if days < 0:
            return "certificado caducado"
        if days <= self.settings.data.get("cert_warn_days", 30):
            return tr("certificado caduca en {0} días").format(days)
        return ""

    def cert_text(self, certs):
        days = self._cert_days(certs)
        if days is None:
            return ""
        return "caducado" if days < 0 else tr("válido {0} días más").format(days)

    def _cert_days(self, certs):
        files = {f.strip() for f in certs.split(",") if f.strip()}
        found = [c for c in self.certs if c["file"] in files and c["not_after"]]
        if not found:
            return None
        return int((min(c["not_after"] for c in found) - time.time()) // 86400)

    def check_certs(self):
        def done(data):
            if "error" in data:
                return
            self.certs = data.get("certs", [])
            today = time.strftime("%Y-%m-%d")
            if self.settings.data.get("cert_warned") == today:
                return
            limit = self.settings.data.get("cert_warn_days", 30)
            bad = []
            for c in self.certs:
                if not c["not_after"]:
                    continue
                days = int((c["not_after"] - time.time()) // 86400)
                if days <= limit:
                    bad.append(f"{c['file']} ({c['kind']}): " + (tr("CADUCADO") if days < 0 else tr("caduca en {0} días").format(days)))
            if bad:
                self._notify(tr("Certificados por caducar:\n") + "\n".join(bad), "error", 10000)
                self.log.append(tr("Certificados"), "\n".join(bad))
                self.settings.data["cert_warned"] = today
                self.settings.save()

        self.helper.run(["certs"], done)

    # --- automation
    def _notify(self, text, state, ms=4000):
        if not self.settings.data.get("notifications", True):
            return
        if self.settings.data.get("notify_style", "balloon") == "balloon":
            self.balloon.show_message(APP, text, state, ms, self.tray.geometry())
        else:
            self.tray.showMessage(APP, text, shield_icon(state), ms)

    def _notify_changes(self):
        for c in self.status.get("connections", []):
            name = c["name"]
            st = self.state_of(name)
            prev = self.prev_states.get(name)
            if not self.first_poll and prev != st:
                if st == "up":
                    vips = ", ".join(self.status["sas"][name].get("vips", []))
                    self._notify(tr("Conectado a {0}").format(name) + (f"\nIP: {vips}" if vips else ""), "up")
                    self.log.append(f"{name}: conectado", tr("IP virtual: {0}").format(vips or '-'))
                    self.retry.pop(name, None)
                elif prev == "up" and st in ("down", "error"):
                    ks = name in self.status.get("killswitch", [])
                    self._notify(tr("Desconectado de {0}").format(name) + (tr("\nKill switch activo: tráfico bloqueado") if ks else ""),
                                 "error" if ks else "down")
                    self.log.append(f"{name}: desconectado", "")
            self.prev_states[name] = st

    def _autoconnect(self):
        if self.connectivity == "portal":
            self.first_poll = True  # try again once the portal is done
            return
        for c in self.status.get("connections", []):
            meta = self.settings.conn(c["name"])
            if self.state_of(c["name"]) != "down":
                continue
            if meta.get("autoconnect") or meta.get("always_up") or \
                    (meta.get("untrusted_auto") and not self.trusted_now):
                if meta.get("untrusted_auto") and not self.trusted_now:
                    self.auto_by_rule.add(c["name"])
                self.connect_vpn(c["name"], auto=True)

    def _network_rules(self):
        """Connect outside trusted networks, disconnect when back inside."""
        if not self.settings.data.get("trusted"):
            return
        for c in self.status.get("connections", []):
            name = c["name"]
            if not self.settings.conn(name).get("untrusted_auto") or name in self.busy:
                continue
            st = self.state_of(name)
            if self.trusted_now and st == "up" and name in self.auto_by_rule:
                self.log.append(tr("{0}: red de confianza detectada").format(name), "desconectando")
                self.auto_by_rule.discard(name)
                self.disconnect_vpn(name, manual=False)
            elif not self.trusted_now and st in ("down", "error") and name not in self.manual_down:
                if name not in self.auto_by_rule:
                    self.auto_by_rule.add(name)
                    self.log.append(tr("{0}: fuera de las redes de confianza").format(name), "conectando")
                    self._try_reconnect(name, c)

    # --- suspend / resume
    def _check_resume(self):
        """The monotonic clock stops while suspended and the wall clock doesn't."""
        now = (time.time(), time.monotonic())
        last, self.clock = self.clock, now
        if not last or (now[0] - last[0]) - (now[1] - last[1]) < 20:
            return
        was_up = {n for n, st in self.prev_states.items() if st == "up" and n not in self.manual_down}
        self.log.append(tr("Equipo reanudado"), f"suspendido {human_time(int(now[0] - last[0]))}"
                        + (tr("; se reconectará: {0}").format(', '.join(sorted(was_up))) if was_up else ""))
        if was_up:
            self.resume_queue |= was_up
            self.resume_deadline = time.time() + 90
            QTimer.singleShot(2000, self._resume_step)

    def _resume_step(self):
        if not self.resume_queue:
            return
        # wait for the network to come back before touching the tunnels
        if nm_connectivity() not in ("full", "limited", "portal") and time.time() < self.resume_deadline:
            QTimer.singleShot(3000, self._resume_step)
            return
        names, self.resume_queue = self.resume_queue, set()
        for name in names:
            self._restart_tunnel(name)

    def _restart_tunnel(self, name):
        """After a suspend the SA is usually dead even if charon still lists it: start over."""
        if name in self.busy or name in self.manual_down:
            return
        self.busy[name] = "connecting"
        self._refresh_now()

        def done(_data):
            self.busy.pop(name, None)
            self.retry.pop(name, None)
            self.connect_vpn(name, auto=True)

        self.helper.run(["down", name], done, stdin=json.dumps({"keep_killswitch": True}))

    # --- captive portals
    def _check_portal(self):
        self.connectivity = nm_connectivity()
        if self.connectivity == "portal":
            if not self.portal_warned:
                self.portal_warned = True
                self._notify(tr("Esta red pide iniciar sesión (portal cautivo).\nAbre el navegador y acepta las condiciones; la VPN se conectará después."), "connecting", 12000)
                self.log.append(tr("Portal cautivo detectado"), tr("la conexión automática espera al inicio de sesión"))
        elif self.portal_warned and self.connectivity == "full":
            self.portal_warned = False
            self.log.append(tr("Portal cautivo superado"), "")
            for c in self.status.get("connections", []):
                self.retry.pop(c["name"], None)

    def _keep_up(self):
        """Always Up: reconnect dropped tunnels with exponential back-off."""
        if not self.status.get("daemon", True):
            return
        for c in self.status.get("connections", []):
            name = c["name"]
            meta = self.settings.conn(name)
            wanted = meta.get("always_up") or (meta.get("untrusted_auto") and name in self.auto_by_rule
                                               and not self.trusted_now)
            if not wanted or name in self.manual_down:
                continue
            if self.state_of(name) not in ("down", "error") or name in self.busy:
                continue
            self._try_reconnect(name, c)

    def _try_reconnect(self, name, info):
        if self.connectivity == "portal":
            return  # the gateway is unreachable until the user logs in to the portal
        meta = self.settings.conn(name)
        if meta.get("otp", "none") == "append":
            return  # needs a fresh code from the user
        if info.get("prompt") and name not in self.session_pw and not Keyring.get(name):
            return  # can't reconnect without asking
        now = time.time()
        when, attempt = self.retry.get(name, (0, 0))
        if now < when:
            return
        self.retry[name] = (now + RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)], attempt + 1)
        self.log.append(tr("{0}: conexión automática (intento {1})").format(name, attempt + 1), "")
        self.connect_vpn(name, auto=True)

    def _maintain_dns(self):
        """Re-apply split DNS after charon reconnects on its own; drop it when down."""
        active = set(self.status.get("dns", []))
        for c in self.status.get("connections", []):
            name = c["name"]
            if name in self.busy or name in self.dns_pending:
                continue
            mode = self.settings.conn(name).get("dns_mode", "none")
            st = self.state_of(name)
            if st in ("down", "error"):
                self.dns_retry.pop(name, None)
                self.dns_warned.discard(name)
            if st == "up" and mode != "none" and name not in active:
                if time.time() >= self.dns_retry.get(name, 0):
                    self._dns_call(name, ["dns-apply", name], self._dns_cfg(name))
            elif st in ("down", "error") and name in active:
                self._dns_call(name, ["dns-clear", name], None)

    def _dns_call(self, name, args, cfg):
        self.dns_pending.add(name)

        def done(data):
            self.dns_pending.discard(name)
            if "error" in data:
                self._dns_problem(name, data["error"])
            elif data.get("dns"):
                self._dns_applied(name, data["dns"])

        self.helper.run(args, done, stdin=json.dumps(cfg) if cfg else None)

    def _dns_problem(self, name, error):
        self.dns_retry[name] = time.time() + 120
        self.log.append(f"{name}: DNS", error)
        if name not in self.dns_warned:
            self.dns_warned.add(name)
            self._notify(tr("{0}: conectado, pero el DNS de la VPN no se aplicó:\n{1}").format(name, error), "error", 10000)

    def _dns_applied(self, name, dns):
        self.log.append(f"{name}: DNS aplicado", json.dumps(dns, ensure_ascii=False))
        if dns.get("dead") and name not in self.dns_warned:
            self.dns_warned.add(name)
            self._notify(tr("{0}: el DNS {1} de la VPN no responde; se usa {2}").format(name, ', '.join(dns['dead']), ', '.join(dns['servers'])), "connecting", 8000)

    def _dns_cfg(self, name):
        meta = self.settings.conn(name)
        return {"mode": meta.get("dns_mode", "none"), "domains": meta.get("dns_domains", []),
                "servers": meta.get("dns_servers", [])}

    # --- tray
    def _update_tray(self):
        states = {c["name"]: self.state_of(c["name"]) for c in self.status.get("connections", [])}
        up = [n for n, s in states.items() if s == "up"]
        blocked = [n for n in self.status.get("killswitch", []) if states.get(n) != "up"]
        if not self.status.get("daemon", True) or blocked:
            overall = "error"
        elif up:
            overall = "up"
        elif any(s == "connecting" for s in states.values()):
            overall = "connecting"
        elif any(s == "error" for s in states.values()):
            overall = "error"
        else:
            overall = "down"
        self.tray.setIcon(shield_icon(overall))
        lines = [APP]
        if not self.status.get("daemon", True):
            lines.append(tr("strongSwan no responde"))
        if blocked:
            lines.append(tr("🔒 Kill switch bloqueando tráfico"))
        for n, s in states.items():
            sa = self.status["sas"].get(n, {})
            extra = f" — {', '.join(sa.get('vips', []))}" if s == "up" and sa.get("vips") else ""
            lat = self.latency.get(n)
            extra += f" · {lat:.0f} ms" if s == "up" and lat is not None else ""
            lines.append(f"{'●' if s == 'up' else '○'} {n}: {STATE_LABELS[s].lower()}{extra}")
        self.tray.setToolTip("\n".join(lines))

    def _build_menu(self):
        m = self.menu
        m.clear()
        conns = self.status.get("connections", [])
        if not conns:
            a = m.addAction(tr("Sin conexiones configuradas"))
            a.setEnabled(False)
        for c in conns:
            name = c["name"]
            st = self.state_of(name)
            if st in ("up", "connecting"):
                a = m.addAction(dot_icon(st), tr("Desconectar {0}").format(name))
                a.triggered.connect(lambda _=False, n=name: self.disconnect_vpn(n))
            else:
                a = m.addAction(dot_icon(st), tr("Conectar {0}").format(name))
                a.triggered.connect(lambda _=False, n=name: self.connect_vpn(n))
        if self.status.get("killswitch"):
            m.addSeparator()
            m.addAction(shield_icon("error"), tr("Desactivar kill switch"), self.killswitch_off)
        m.addSeparator()
        m.addAction(tr("Administrar conexiones…"), self.show_window)
        m.addAction(tr("Nueva conexión…"), self.new_connection)
        m.addAction(tr("Importar…"), self.import_file)
        m.addAction(tr("Ver registro"), self.show_log)
        m.addAction(tr("Preferencias…"), self.show_prefs)
        self.help.fill_menu(m.addMenu(tr("Ayuda")))
        m.addSeparator()
        m.addAction(tr("Salir"), self.qapp.quit)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.window.isVisible() and self.window.isActiveWindow():
                self.window.hide()
            else:
                self.show_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_log(self):
        self.log.show()
        self.log.raise_()

    def show_prefs(self):
        names = [c["name"] for c in self.status.get("connections", [])]
        dlg = PrefsDialog(self.window if self.window.isVisible() else None, self.settings, names)
        if dlg.exec():
            try:
                self.settings.autostart = dlg.autostart.isChecked()
            except OSError as e:
                QMessageBox.warning(self.window, APP, tr("No se pudo cambiar el inicio automático:\n{0}").format(e))
            d = self.settings.data
            d["notifications"] = dlg.notify.isChecked()
            d["notify_style"] = dlg.notify_style.currentData()
            d["start_hidden"] = dlg.start_hidden.isChecked()
            d["use_keyring"] = dlg.keyring.isChecked()
            d["cert_warn_days"] = dlg.cert_days.value()
            d["tray_theme"] = ICON_THEME["mode"] = dlg.tray_theme.currentData()
            d["check_updates"] = dlg.check_updates.isChecked()
            if d.get("language", "auto") != dlg.language.currentData():
                d["language"] = dlg.language.currentData()
                QMessageBox.information(self.window, APP, tr("El idioma cambiará la próxima vez que abras SwanGUI."))
            d["trusted"] = dlg.trusted_names()
            d.pop("cert_warned", None)
            self.settings.save()
            self.poll()

    def _error(self, title, msg, auto):
        hints = explain(msg)
        self._notify(f"{title}\n{(hints[0] if hints else msg)[:220]}", "error", 8000)
        self.log.append(title, msg + (tr("\n\nQué revisar:\n- ") + "\n- ".join(hints) if hints else ""))
        return hints

    def _refresh_now(self):
        self._update_tray()
        self.window.refresh(self.status)
        self._build_menu()

    # --- passwords
    def password_for(self, name, interactive=True, parent=None):
        """Password to send at connect time: str, '' if the config has it, False if cancelled."""
        info = self.conn_info(name)
        otp = self.settings.conn(name).get("otp", "none")
        if not info.get("prompt") and otp == "none":
            return ""
        base = self.session_pw.get(name) or Keyring.get(name)
        if base and otp != "append":
            return base
        if not interactive:
            return False
        dlg = PasswordDialog(parent or (self.window if self.window.isVisible() else None),
                             name, info.get("user", ""), need_password=not base, otp=otp)
        dlg.raise_()
        dlg.activateWindow()
        if not dlg.exec():
            return False
        pw = base or dlg.password.text()
        if not pw:
            return False
        if not base and otp == "none" and dlg.remember.isChecked():
            self.session_pw[name] = pw
        if otp == "append":
            if not dlg.code.text().strip():
                return False
            pw += dlg.code.text().strip()
        return pw

    # --- connect / disconnect
    def connect_vpn(self, name, auto=False):
        if not name or name in self.busy:
            return
        meta = self.settings.conn(name)
        pw = self.password_for(name, interactive=True)
        if pw is False:
            return
        opts = {}
        if pw:
            opts["password"] = pw
        if meta.get("otp") == "push":
            opts["timeout"] = 120
            self._notify(tr("{0}: aprueba la conexión en tu móvil (FortiToken)").format(name), "connecting", 15000)
        if meta.get("dns_mode", "none") != "none":
            opts["dns"] = self._dns_cfg(name)
        if meta.get("killswitch"):
            opts["killswitch"] = {"allow_lan": meta.get("killswitch_lan", True),
                                  "forward": meta.get("killswitch_fwd", True)}
        self.manual_down.discard(name)
        self.busy[name] = "connecting"
        self.errors.discard(name)
        self._refresh_now()

        def done(data):
            self.busy.pop(name, None)
            if "error" in data:
                self.errors.add(name)
                if pw and re.search(r"EAP_FAILURE|XAuth.*fail|AUTHENTICATION_FAILED", data["error"]):
                    self.session_pw.pop(name, None)  # wrong password: ask again next time
                hints = self._error(tr("No se pudo conectar {0}").format(name), data["error"], auto)
                if not auto:
                    self._connect_failed_dialog(name, data["error"], hints)
            else:
                self.log.append(tr("Conectar {0}").format(name), data.get("log", ""))
                if data.get("dns_error"):
                    self._dns_problem(name, data["dns_error"])
                elif data.get("dns"):
                    self._dns_applied(name, data["dns"])
            self.poll()

        self.helper.run(["up", name], done, stdin=json.dumps(opts))

    def _connect_failed_dialog(self, name, error, hints):
        box = QMessageBox(self.window if self.window.isVisible() else None)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP)
        box.setText(tr("No se pudo conectar <b>{0}</b>.").format(name))
        box.setInformativeText(tr("<b>Qué revisar:</b><ul>") + "".join(f"<li>{h}</li>" for h in hints) + "</ul>"
                               if hints else tr("Revisa los detalles o usa «Probar conexión»."))
        box.setDetailedText(error)
        test = box.addButton(tr("Probar conexión…"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is test:
            self.diagnose(name)

    def disconnect_vpn(self, name, manual=True):
        if not name or self.busy.get(name) == "disconnecting":
            return
        if manual:
            self.manual_down.add(name)
            self.auto_by_rule.discard(name)
        self.retry.pop(name, None)
        self.busy[name] = "disconnecting"
        self._refresh_now()

        def done(data):
            self.busy.pop(name, None)
            if "error" in data:
                self._error(tr("No se pudo desconectar {0}").format(name), data["error"], True)
            else:
                self.log.append(tr("Desconectar {0}").format(name), data.get("log", ""))
            self.poll()

        self.helper.run(["down", name], done, stdin="{}")

    def killswitch_off(self):
        def done(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, data["error"])
            else:
                self._notify(tr("Kill switch desactivado: tráfico liberado"), "down")
                self.log.append(tr("Kill switch"), "desactivado manualmente")
            self.poll()

        self.helper.run(["killswitch-off"], done)

    def diagnose(self, name):
        if name:
            self.show_window()
            DiagnoseDialog(self.window, self, name).exec()
            self.poll()

    # --- create / edit / delete
    def _editor_meta(self, name):
        meta = dict(self.settings.conn(name))
        meta["_keyring"] = bool(Keyring.get(name))
        return meta

    def new_connection(self):
        self.show_window()
        dlg = EditorDialog(self.window)
        if dlg.exec():
            self._save(dlg.values(), dlg.meta())

    def edit_connection(self, name):
        if not name:
            return

        def loaded(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, data["error"])
                return
            dlg = EditorDialog(self.window, data, self._editor_meta(name))
            if dlg.exec():
                self._save(dlg.values(), dlg.meta())

        self.helper.run(["read", name], loaded)

    def duplicate_connection(self, name):
        if not name:
            return

        def loaded(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, data["error"])
                return
            secrets = data.get("secrets", {})
            if not secrets.get("password"):
                secrets["password"] = Keyring.get(name)
            dlg = EditorDialog(self.window, data["view"], self.settings.conn(name), copy_secrets=secrets)
            if dlg.exec():
                self._save(dlg.values(), dlg.meta())

        self.helper.run(["export", name, "secrets"], loaded)

    def _save(self, values, meta, quiet=False, then=None):
        orig = values.get("orig_name")
        name = values["name"]
        mode = values.get("user_mode", "save" if not values.get("clear_user_secret") else "prompt")
        typed = values.get("eap_password") or values.get("xauth_password")
        use_keyring = Keyring.available() and (self.settings.data.get("use_keyring") or meta.get("otp", "none") != "none")
        keyring_pw = None
        if mode == "save" and use_keyring:
            if typed:
                keyring_pw = typed
            elif orig and Keyring.get(orig):
                keyring_pw = Keyring.get(orig)
            if keyring_pw is not None:
                values["eap_password"] = values["xauth_password"] = ""
                values["clear_user_secret"] = True
        elif mode == "save" and meta.get("otp", "none") != "none":
            # OTP without a keyring: never store the password in strongSwan
            values["eap_password"] = values["xauth_password"] = ""
            values["clear_user_secret"] = True
        meta = {k: v for k, v in meta.items() if not k.startswith("_")}

        def done(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, tr("No se pudo guardar «{0}»:\n\n").format(name) + data["error"])
            else:
                saved = data.get("name", name)
                self.settings.set_conn(saved, meta, old_name=orig)
                try:
                    if keyring_pw is not None:
                        Keyring.set(saved, keyring_pw)
                    elif mode != "save" or (typed and not use_keyring):
                        Keyring.clear(saved)
                    if orig and orig != saved:
                        Keyring.clear(orig)
                except (OSError, subprocess.SubprocessError) as e:
                    QMessageBox.warning(self.window, APP, tr("No se pudo guardar la contraseña en el llavero:\n{0}\n\nSe pedirá al conectar.").format(e))
                for n in {orig, saved} - {None, ""}:
                    self.session_pw.pop(n, None)
                self.log.append(tr("Guardado {0}").format(saved), data.get("load", ""))
                if not quiet:
                    self._notify(tr("Conexión «{0}» guardada").format(saved), "up", 3000)
            if then:
                then(data)
            self.poll()

        self.helper.run(["write"], done, stdin=json.dumps(values))

    def edit_raw(self, name):
        if not name:
            return

        def loaded(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, data["error"])
                return
            dlg = RawEditor(self.window, data["file"], data["text"])
            if dlg.exec():
                self.helper.run(["write-raw", name], self._raw_saved, stdin=dlg.edit.toPlainText())

        self.helper.run(["read-raw", name], loaded)

    def _raw_saved(self, data):
        if "error" in data:
            QMessageBox.critical(self.window, APP, tr("No se pudo guardar:\n\n") + data["error"])
        else:
            self.log.append(tr("Archivo guardado"), data.get("load", ""))
        self.poll()

    def delete_connection(self, name):
        if not name:
            return
        r = QMessageBox.question(self.window, APP,
                                 tr("¿Eliminar la conexión «{0}»?\n\nSe desconectará si está activa. Se guarda una copia en /etc/swanctl/swangui-backup.").format(name))
        if r != QMessageBox.StandardButton.Yes:
            return

        def done(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, data["error"])
            else:
                self.settings.remove(name)
                Keyring.clear(name)
                self.session_pw.pop(name, None)
                self.manual_down.discard(name)
            self.poll()

        self.manual_down.add(name)
        self.helper.run(["delete", name], done)

    # --- import / export
    def import_file(self):
        self.show_window()
        path, _ = QFileDialog.getOpenFileName(
            self.window, tr("Importar conexiones VPN"), os.path.expanduser("~"),
            tr("Configuraciones VPN (*.conf *.xml *.swanctl *.swgenc *.rsc *.ps1 *.txt *.mobileconfig *.sswan);;Todos los archivos (*)"))
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.critical(self.window, APP, str(e))
            return
        if is_encrypted_export(text):
            if not crypto_available():
                QMessageBox.critical(self.window, APP, tr("El archivo está cifrado y falta python-cryptography."))
                return
            label = tr("Este archivo está protegido. Contraseña del archivo:")
            while True:
                pw, ok = QInputDialog.getText(self.window, tr("Importar archivo cifrado"), label,
                                              QLineEdit.EchoMode.Password)
                if not ok:
                    return
                try:
                    text = decrypt_export(text, pw)
                    break
                except ValueError as e:
                    label = tr("{0}. Inténtalo de nuevo:").format(e)
        fmt = detect_format(text)
        if fmt == "swanctl":
            self._import_swanctl(text)
        else:
            self._import_vendor(IMPORTERS[fmt], text)

    def _import_vendor(self, importer, text):
        try:
            items, warnings = importer(text)
        except ValueError as e:
            QMessageBox.critical(self.window, APP, str(e))
            return
        if not items:
            QMessageBox.warning(self.window, APP, tr("No se importó nada.\n\n") + "\n".join(warnings))
            return
        imported = []

        def next_one(_data=None):
            if _data is not None and "error" not in _data:
                imported.append(_data.get("name"))
            if items:
                values, meta = items.pop(0)
                self._save(values, meta, quiet=True, then=next_one)
            else:
                self._import_report(imported, warnings)

        next_one()

    def _import_swanctl(self, text):
        def done(data):
            if "error" in data:
                QMessageBox.critical(self.window, APP, tr("No se pudo importar:\n\n") + data["error"])
                return
            names = []
            for it in data.get("imported", []):
                names.append(it["name"] + (f" (antes «{it['from']}»)" if it["name"] != it["from"] else ""))
            self._import_report(names, [])
            self.poll()

        self.helper.run(["import"], done, stdin=text)

    def _import_report(self, names, warnings):
        msg = tr("Se importaron {0} conexión(es):\n  • ").format(len(names)) + "\n  • ".join(names) if names \
            else tr("No se importó ninguna conexión.")
        if warnings:
            msg += "\n\nAvisos:\n  • " + "\n  • ".join(warnings)
        self.log.append(tr("Importación"), msg)
        (QMessageBox.warning if warnings else QMessageBox.information)(self.window, APP, msg)

    def export_connections(self, selected=None):
        names = [c["name"] for c in self.status.get("connections", [])]
        if not names:
            return
        dlg = ExportDialog(self.window, names, selected)
        if not dlg.exec():
            return
        chosen = dlg.chosen()
        if not chosen:
            return
        fmt = dlg.fmt.currentData()
        with_secrets = dlg.secrets.isChecked()
        passphrase = dlg.passphrase()
        base = chosen[0] if len(chosen) == 1 else "vpn-swangui"
        _key, _label, suffix, exporter = next(f for f in EXPORT_FORMATS if f[0] == fmt)
        if passphrase:
            suffix += ".swgenc"
        path, _ = QFileDialog.getSaveFileName(self.window, tr("Guardar exportación"),
                                              os.path.expanduser(f"~/{base}{suffix}"),
                                              tr("Todos los archivos (*)"))
        if not path:
            return
        results, pending = [], list(chosen)

        def next_one(data=None):
            if data is not None:
                if "error" in data:
                    QMessageBox.critical(self.window, APP, data["error"])
                    return
                if with_secrets and not data.get("secrets", {}).get("password"):
                    pw = Keyring.get(data["view"]["name"])
                    if pw:
                        data.setdefault("secrets", {})["password"] = pw
                results.append(data)
            if pending:
                self.helper.run(["export", pending.pop(0), "secrets" if with_secrets else "nosecrets"],
                                next_one)
                return
            warnings = []
            if exporter:
                text, warnings = exporter([(r["view"], r.get("secrets"), self.settings.conn(r["view"]["name"]))
                                           for r in results])
            else:
                text = (tr("# Exportado por SwanGUI {0}{1}\n\n").format(time.strftime('%Y-%m-%d %H:%M'), tr(' — CONTIENE CONTRASEÑAS') if with_secrets else '')
                        + "\n".join(r["swanctl"] for r in results))
            if passphrase:
                text = encrypt_export(text, passphrase)
            try:
                mode = 0o600 if with_secrets else 0o644
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
                os.fchmod(fd, mode)  # also when overwriting an existing file
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                QMessageBox.critical(self.window, APP, str(e))
                return
            self.log.append(tr("Exportación"), tr("{0} conexión(es) → {1}").format(len(results), path)
                            + (" (cifrado)" if passphrase else ""))
            self._notify(tr("Exportadas {0} conexión(es)\n{1}").format(len(results), path), "up", 4000)
            if warnings:
                QMessageBox.warning(self.window, APP, tr("Exportado con avisos:") + "\n\n  • " + "\n  • ".join(warnings))

        next_one()

    # --- remote commands (second instance / CLI / keyboard shortcuts)
    def remote_command(self, text):
        parts = text.strip().split()
        if not parts or parts[0] == "show":
            self.show_window()
            return
        action, name = parts[0], (parts[1] if len(parts) > 1 else "")
        if name and not self.conn_info(name):
            self._notify(tr("No existe la conexión «{0}»").format(name), "error")
            return
        if action == "connect":
            self.connect_vpn(name)
        elif action == "disconnect":
            self.disconnect_vpn(name)
        elif action == "toggle":
            if self.state_of(name) in ("up", "connecting"):
                self.disconnect_vpn(name)
            else:
                self.connect_vpn(name)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

CLI_HELP = tr("Uso: swangui [comando]\n\n  (sin comando)          abre SwanGUI en la bandeja\n  --show                 abre SwanGUI y muestra la ventana\n  status                 estado de las conexiones\n  list                   nombres de las conexiones\n  connect NOMBRE         conecta\n  disconnect NOMBRE      desconecta\n  toggle NOMBRE          conecta o desconecta según el estado\n")


def _cli_helper(args, stdin=None):
    p = subprocess.run(["pkexec", HELPER, *args], input=stdin, capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stdout + p.stderr).strip() or tr("código {0}").format(p.returncode)}


def cli(args):
    cmd = args[0]
    if cmd in ("help", "-h", "--help"):
        print(CLI_HELP)
        return 0
    if cmd in ("status", "list"):
        data = _cli_helper(["status"])
        if "error" in data:
            print("Error:", data["error"], file=sys.stderr)
            return 1
        for c in data["connections"]:
            if cmd == "list":
                print(c["name"])
                continue
            sa = data["sas"].get(c["name"])
            st = "conectado" if sa and sa["state"] == "ESTABLISHED" and sa["child_up"] else \
                ("conectando" if sa else "desconectado")
            vip = ", ".join(sa.get("vips", [])) if sa else ""
            extra = f"  IP {vip}  {human_time(sa['established'])}  ↓{human_bytes(sa['bytes_in'])} " \
                    f"↑{human_bytes(sa['bytes_out'])}" if st == "conectado" else ""
            print(f"{'●' if st == 'conectado' else '○'} {c['name']:<20} {st:<13} {c['remote']}{extra}")
        return 0
    if cmd in ("connect", "disconnect", "toggle"):
        if len(args) < 2:
            print(CLI_HELP, file=sys.stderr)
            return 2
        name = args[1]
        # hand over to the running tray app (it knows passwords, DNS and kill switch settings)
        sock = QLocalSocket()
        sock.connectToServer(f"swangui-{os.getuid()}")
        if sock.waitForConnected(500):
            sock.write(f"{cmd} {name}".encode())
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            print(f"{cmd} {name}: enviado a SwanGUI")
            return 0
        settings = Settings()
        meta = settings.conn(name)
        if cmd == "toggle":
            data = _cli_helper(["status"])
            sa = data.get("sas", {}).get(name)
            cmd = "disconnect" if sa else "connect"
        if cmd == "disconnect":
            data = _cli_helper(["down", name], "{}")
        else:
            opts = {}
            status = _cli_helper(["status"])
            info = next((c for c in status.get("connections", []) if c["name"] == name), {})
            if info.get("prompt") or meta.get("otp", "none") != "none":
                import getpass
                pw = Keyring.get(name) or getpass.getpass(tr("Contraseña de {0}: ").format(info.get('user') or name))
                if meta.get("otp") == "append":
                    pw += input(tr("Código OTP: ")).strip()
                opts["password"] = pw
            if meta.get("dns_mode", "none") != "none":
                opts["dns"] = {"mode": meta["dns_mode"], "domains": meta.get("dns_domains", []),
                               "servers": meta.get("dns_servers", [])}
            if meta.get("killswitch"):
                opts["killswitch"] = {"allow_lan": meta.get("killswitch_lan", True),
                                  "forward": meta.get("killswitch_fwd", True)}
            if meta.get("otp") == "push":
                opts["timeout"] = 120
                print(tr("Aprueba la conexión en tu móvil…"))
            data = _cli_helper(["up", name], json.dumps(opts))
        if "error" in data:
            print("Error:", data["error"], file=sys.stderr)
            for h in explain(data["error"]):
                print("  →", h, file=sys.stderr)
            return 1
        print(f"{cmd} {name}: correcto")
        return 0
    print(CLI_HELP, file=sys.stderr)
    return 2


def main():
    # settings, history and exports describe the user's VPNs: keep them private
    os.umask(0o077)
    for path in (SETTINGS_PATH, HISTORY_PATH):
        for target in (os.path.dirname(path), path):
            try:
                os.chmod(target, 0o700 if target != path else 0o600)
            except OSError:
                pass
    if len(sys.argv) > 1 and sys.argv[1] in ("status", "list", "connect", "disconnect", "toggle",
                                             "help", "-h", "--help"):
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication(sys.argv[:1])
        sys.exit(cli(sys.argv[1:]))

    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP)
    qapp.setDesktopFileName("swangui")
    qapp.setQuitOnLastWindowClosed(False)
    translator = QTranslator(qapp)
    if UI_LANG != "en" and translator.load(QLocale(UI_LANG), "qtbase", "_",
                                           QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        qapp.installTranslator(translator)

    # single instance: a second launch just asks the running one to show itself
    sock_name = f"swangui-{os.getuid()}"
    probe = QLocalSocket()
    probe.connectToServer(sock_name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(sock_name)
    server = QLocalServer()
    # only this user may send commands (connect/disconnect) to the running instance
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    server.listen(sock_name)

    for _ in range(20):
        if QSystemTrayIcon.isSystemTrayAvailable():
            break
        time.sleep(0.5)  # the panel may still be starting at login
    app = SwanGUI(qapp)

    def incoming():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def read():
            app.remote_command(bytes(conn.readAll()).decode(errors="replace"))

        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
        if conn.bytesAvailable():
            read()

    server.newConnection.connect(incoming)
    if "--show" in sys.argv or not app.settings.data.get("start_hidden", True):
        app.show_window()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
