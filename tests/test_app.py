"""Start the whole application offscreen against a fake helper."""
import json
import os
import stat
import tempfile
import unittest

from test_gui import g

STATUS = {"daemon": True, "daemon_msg": "", "sas": {}, "dns": [], "killswitch": [],
          "connections": [{"name": "office", "file": "/etc/swanctl/conf.d/office.conf", "remote": "vpn.example.com",
                           "child": "office", "remote_ts": "10.0.0.0/8", "certs": "", "version": "2",
                           "user": "alice", "prompt": True}]}


class AppStartTests(unittest.TestCase):
    def test_start_and_open_dialogs(self):
        from PyQt6.QtWidgets import QApplication
        tmp = tempfile.mkdtemp()
        fake = os.path.join(tmp, "helper")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho '" + json.dumps(STATUS) + "'\n")
        os.chmod(fake, stat.S_IRWXU)
        os.environ["XDG_CONFIG_HOME"] = os.environ["XDG_DATA_HOME"] = tmp
        g.SETTINGS_PATH = os.path.join(tmp, "swangui", "settings.json")
        g.HISTORY_PATH = os.path.join(tmp, "swangui", "history.jsonl")
        qapp = QApplication.instance() or QApplication([])
        g.Helper.run = lambda self, args, cb, stdin=None: cb(STATUS if args[0] == "status" else {"ok": True})
        app = g.SwanGUI(qapp)
        app._polled(STATUS)
        app.window.refresh(app.status)
        app._build_menu()
        self.assertTrue(app.menu.actions())
        g.ExportDialog(None, ["office"], "office")
        g.PrefsDialog(None, app.settings, ["office"])
        g.TextDialog(None, "x", "# y", markdown=True)
        self.assertIn("SwanGUI", g.system_info(app))
        app.settings.data["notify_style"] = "balloon"
        app._notify("Conectado a office", "up")
        self.assertTrue(app.balloon.isVisible())
        self.assertIn("office", app.balloon.text.text())
        up = dict(STATUS, dns=["office"], sas={"office": {
            "state": "ESTABLISHED", "child_up": True, "vips": ["10.9.0.2"], "remote_host": "203.0.113.5",
            "local_host": "", "established": 3725, "bytes_in": 5 << 20, "bytes_out": 1 << 20,
            "rekey_in": 7200, "child_rekey_in": 600}})
        app.dns_info["office"] = {"servers": ["10.0.0.53"], "domains": ["~corp.example"], "dead": ["10.0.0.54"]}
        app.samples["office"] = g.deque([(i, 1000, 500, 20.0) for i in range(10)], maxlen=g.GRAPH_POINTS)
        app._ping = lambda name: None
        app._polled(up)
        state, head, conns = app._card_content()
        self.assertEqual(state, "up")
        name, st, text = conns[0]
        self.assertEqual((name, st), ("office", "up"))
        for part in ("10.9.0.2", "203.0.113.5", "1:02:05", "5.0 MiB", "10.0.0.53", "corp.example", "10.0.0.54",
                     "2:00:00", "0:10:00", g.tr("Buena"), g.COLORS["up"]):
            self.assertIn(part, text)
        g.TrayPopup.spot = None
        self.assertNotEqual(app.tray.toolTip(), "")
        app.card.refresh(g.QRect(100, 0, 24, 24))
        self.assertTrue(app.card.isVisible())
        sec = app.card.sections["office"]
        self.assertEqual(sec.button.text(), g.tr("Desconectar"))
        self.assertFalse(sec.graph.isHidden())
        app.card.mute()
        self.assertFalse(app.card.isVisible())
        app.timer.stop()
        app.cert_timer.stop()
        app.card.timer.stop()

    def test_quality(self):
        app = g.SwanGUI.__new__(g.SwanGUI)
        app.samples = {"a": [(i, 0, 0, 300.0 if i % 2 else None) for i in range(15)],
                       "b": [(i, 0, 0, None) for i in range(15)],
                       "c": [(i, 0, 0, 120.0) for i in range(15)]}
        self.assertEqual(g.SwanGUI._quality(app, "a")[0], "error")
        self.assertIsNone(g.SwanGUI._quality(app, "b"))
        self.assertEqual(g.SwanGUI._quality(app, "c")[0], "connecting")


class FindIconTests(unittest.TestCase):
    """Locating the tray icon in a strip grabbed at a screen edge."""

    def strip(self, w, h, pos, size=22, state="up", panel="#2b2b2b"):
        from PyQt6.QtGui import QImage, QPainter
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(g.QColor(panel))
        p = QPainter(img)
        p.drawPixmap(pos[0], pos[1], g.shield_icon(state, size).pixmap(size, size))
        p.end()
        return img

    def test_top_bottom_side(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        for side, img, center in (
                ("top", self.strip(1200, 64, (700, 2)), (711, 13)),
                ("bottom", self.strip(1200, 64, (300, 64 - 24)), (311, 51)),
                ("left", self.strip(64, 900, (3, 400)), (14, 411)),
                ("right", self.strip(64, 900, (64 - 25, 120), state="down"), (50, 131))):
            found = g.find_icon(img, "down" if side == "right" else "up", side)
            self.assertIsNotNone(found, side)
            c = found[1].center()
            self.assertLessEqual(abs(c.x() - center[0]), 3, side)
            self.assertLessEqual(abs(c.y() - center[1]), 3, side)

    def test_badge_and_wrong_place(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        img = self.strip(1200, 64, (500, 30))  # far from the edge: a window, not the panel
        self.assertIsNone(g.find_icon(img, "up", "top"))
        self.assertIsNone(g.find_icon(self.strip(1200, 64, (500, 2)), "error", "top"))
        from PyQt6.QtGui import QPainter
        img = self.strip(1200, 64, (10, 2), size=1)
        p = QPainter(img)
        p.drawPixmap(900, 2, g.shield_icon("up", 22, count=3).pixmap(22, 22))
        p.end()
        found = g.find_icon(img, "up", "top")
        self.assertIsNotNone(found)
        self.assertLess(abs(found[1].center().x() - 911), 4)


if __name__ == "__main__":
    unittest.main()
