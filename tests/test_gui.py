"""Tests for the GUI's pure logic: proposals, FortiClient files, encrypted exports."""
import importlib.machinery
import importlib.util
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("swangui_gui", os.path.join(HERE, "..", "swangui.py"))
_spec = importlib.util.spec_from_loader("swangui_gui", _loader)
g = importlib.util.module_from_spec(_spec)
_loader.exec_module(g)

from test_helper import SAMPLE, h  # noqa: E402


class ProposalTests(unittest.TestCase):
    def test_roundtrip(self):
        for text, ike in (("aes256-sha256-modp2048", True), ("aes128gcm16-prfsha256-ecp256", True),
                          ("aes256-sha256", False)):
            rows, dhs = g.parse_proposals(text)
            self.assertEqual(g.build_proposals(rows, dhs, ike), text)

    def test_unknown_algorithm(self):
        self.assertIsNone(g.parse_proposals("aes256-whirlpool-modp2048"))


class FortiTests(unittest.TestCase):
    def test_export_import(self):
        tree = h.parse(SAMPLE)
        view = h.conn_to_view("office", "", tree)
        xml = g.forti_export([(view, {"psk": "s3cr#t {x}", "password": "pw"}, {"description": "d"})])
        items, warnings = g.forti_import(xml)
        self.assertEqual(len(items), 1)
        values, meta = items[0]
        self.assertEqual(values["remote_addrs"], "vpn.example.com")
        self.assertEqual(values["psk"], "s3cr#t {x}")
        self.assertIn("10.0.0.0/8", values["remote_ts"])

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            g.forti_import("<not-forti/>")
        with self.assertRaises(ValueError):
            g.forti_import("<<<")


@unittest.skipUnless(g.crypto_available(), "python-cryptography not installed")
class FortiSecretTests(unittest.TestCase):
    def test_encrypted_secrets_dropped(self):
        self.assertEqual(g._forti_secret("EncX 1A2B"), "")
        self.assertEqual(g._forti_secret("Enc 1A2B"), "")
        self.assertEqual(g._forti_secret("Encore123"), "Encore123")


class EncryptedExportTests(unittest.TestCase):
    def test_roundtrip(self):
        enc = g.encrypt_export(SAMPLE, "correct horse")
        self.assertTrue(g.is_encrypted_export(enc))
        self.assertNotIn("s3cr#t", enc)
        self.assertEqual(g.decrypt_export(enc, "correct horse"), SAMPLE)

    def test_wrong_password_and_tampering(self):
        enc = g.encrypt_export(SAMPLE, "correct horse")
        with self.assertRaisesRegex(ValueError, "incorrecta"):
            g.decrypt_export(enc, "wrong")
        lines = enc.splitlines()
        i = next(n for n, l in enumerate(lines) if l and not l.startswith(("#", "-----")))
        lines[i] = ("B" if lines[i][0] != "B" else "C") + lines[i][1:]
        with self.assertRaises(ValueError):
            g.decrypt_export("\n".join(lines), "correct horse")

    def test_unique_salt(self):
        self.assertNotEqual(g.encrypt_export("x", "p" * 8), g.encrypt_export("x", "p" * 8))


class ExplainTests(unittest.TestCase):
    def test_phase_hints(self):
        self.assertTrue(g.explain("received NO_PROPOSAL_CHOSEN notify error"))
        self.assertTrue(g.explain("IKE version 1 not supported"))
        self.assertTrue(g.explain("initiating Main Mode IKE_SA x[1] to 1.2.3.4\n"
                                  "sending retransmit 1 of request message ID 0, seq 1"))
        self.assertEqual(g.explain("nothing interesting"), [])



class CatalogTests(unittest.TestCase):
    """Every tr() string has an English entry with the same placeholders."""

    def test_english_catalog_complete(self):
        import ast
        import json
        import re
        with open(os.path.join(HERE, "..", "i18n", "en.json"), encoding="utf-8") as f:
            en = json.load(f)
        ph = re.compile(r"\{\d+[^}]*\}")
        for name in ("swangui.py", "swangui-helper"):
            with open(os.path.join(HERE, "..", name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" \
                        and node.args and isinstance(node.args[0], ast.Constant):
                    key = node.args[0].value
                    self.assertIn(key, en, f"{name}:{node.lineno}")
                    self.assertEqual(sorted(ph.findall(key)), sorted(ph.findall(en[key])), key)


if __name__ == "__main__":
    unittest.main()
