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
        app.timer.stop()
        app.cert_timer.stop()


if __name__ == "__main__":
    unittest.main()
