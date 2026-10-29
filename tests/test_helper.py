"""Tests for the privileged helper's parsing and input validation (no root needed)."""
import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("swangui_helper", os.path.join(HERE, "..", "swangui-helper"))
_spec = importlib.util.spec_from_loader("swangui_helper", _loader)
h = importlib.util.module_from_spec(_spec)
_loader.exec_module(h)

SAMPLE = """
connections {
    office {
        version = 2
        remote_addrs = vpn.example.com
        vips = 0.0.0.0
        proposals = aes256-sha256-modp2048
        local {
            auth = psk
            id = user@example.com
        }
        remote {
            auth = psk
        }
        children {
            office {
                remote_ts = 10.0.0.0/8,192.168.10.0/24
                esp_proposals = aes256-sha256
                start_action = none
            }
        }
    }
}
secrets {
    ike-office {
        id = user@example.com
        secret = "s3cr#t {x}"
    }
}
"""


class ParserTests(unittest.TestCase):
    def test_roundtrip(self):
        tree = h.parse(SAMPLE)
        self.assertEqual(h.parse(h.dump_text(tree)), tree)
        self.assertEqual(tree["secrets"]["ike-office"]["secret"], "s3cr#t {x}")

    def test_quote_escapes_everything(self):
        for value in ['pa"ss', "back\\slash", "new\nline", "tab\there", " lead", "trail ", "#hash", "{}", ""]:
            self.assertEqual(h.parse("k = " + h._quote(value))["k"], value.replace("\r", ""), value)

    def test_unbalanced(self):
        with self.assertRaises(h.HelperError):
            h.parse("a {\n b = 1\n")
        with self.assertRaises(h.HelperError):
            h.parse("}")

    def test_view(self):
        tree = h.parse(SAMPLE)
        v = h.conn_to_view("office", "", tree)
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")
        self.assertTrue(v["has_psk"])
        self.assertTrue(v["vips"])

    def test_apply_view_keeps_unknown_keys(self):
        tree = h.parse(SAMPLE)
        conn = tree["connections"]["office"]
        conn["children"]["office"]["mark_in"] = "42"
        v = h.conn_to_view("office", "", tree)
        v["remote_addrs"] = "other.example.com"
        h.apply_view(conn, v)
        self.assertEqual(conn["remote_addrs"], "other.example.com")
        self.assertEqual(conn["children"]["office"]["mark_in"], "42")


class VpnDnsServersTests(unittest.TestCase):
    def test_new_ikev1_sa_resets_servers(self):
        log = ("initiating Aggressive Mode IKE_SA office[1] to 1.2.3.4\ninstalling DNS server 9.9.9.9 via resolvconf\n"
               "initiating Aggressive Mode IKE_SA office[2] to 1.2.3.4\ninstalling DNS server 10.8.0.1 via resolvconf\n")
        self.assertEqual(h.vpn_dns_servers("office", log), ["10.8.0.1"])


class PskOwnerTests(unittest.TestCase):
    def test_owner(self):
        self.assertEqual(h.psk_owner("me@x", "gw", "1.2.3.4"), "me@x")
        self.assertEqual(h.psk_owner("", "gw.example.com", "1.2.3.4"), "gw.example.com")
        self.assertEqual(h.psk_owner("", "%any", "203.0.113.7"), "203.0.113.7")
        self.assertEqual(h.psk_owner("", "%any", "vpn.example.com"), "")


class SecurityTests(unittest.TestCase):
    def test_names(self):
        for ok in ("office", "a", "vpn-2.lab_x"):
            self.assertEqual(h.check_name(ok), ok)
        for bad in ("", "..", ".hidden", "-opt", "a/b", "a b", "x" * 65, None, 5, "ñ"):
            with self.assertRaises(h.HelperError, msg=repr(bad)):
                h.check_name(bad)

    def test_tree_rejects_code_execution_and_paths(self):
        bad = [
            "connections { x { children { c { updown = /tmp/evil.sh } } } }",
            "include /etc/shadow",
            "connections { x { include /etc/shadow } }",
            "connections { x { local { certs = ../../etc/shadow } } }",
            "connections { x { local { certs = /etc/ssl/key.pem } } }",
            "secrets { private-a { file = /root/key.pem } }",
            "authorities { a { cacert = .hidden } }",
            "connections { -x { } }",
            "connections { x { children { ../c { } } } }",
        ]
        for text in bad:
            with self.assertRaises(h.HelperError, msg=text):
                h.check_tree(h.parse(text))

    def test_tree_accepts_normal_config(self):
        h.check_tree(h.parse(SAMPLE))
        h.check_tree(h.parse("connections { x { local { certs = me.pem } remote { cacerts = ca.pem } } }"))

    def test_control_characters(self):
        with self.assertRaises(h.HelperError):
            h.check_text("a = b\x00")
        self.assertEqual(h.check_text("a = b\n\tc = d\n"), "a = b\n\tc = d\n")

    def test_opts_must_be_object(self):
        for bad in ([], "x", 1, None):
            with self.assertRaises(h.HelperError):
                h.check_opts(bad)

    def test_domains(self):
        for ok in ("corp.local", "a.b-c.example.com", "intranet", "_ldap.example.com"):
            self.assertTrue(h.DOMAIN_RE.match(ok), ok)
        for bad in ("a b", "x;rm -rf", "-a.com", "a..b", "a.-b", "", "a" * 64 + ".com"):
            self.assertFalse(h.DOMAIN_RE.match(bad), bad)

    def test_ifnames_are_safe(self):
        self.assertEqual(h.dns_ifname("office.vpn"), "swg-office_vpn")
        self.assertLessEqual(len(h.dns_ifname("x" * 64)), 15)  # IFNAMSIZ - 1
        self.assertRegex(h.ks_table("a.b-c"), r"^swangui_ks_[A-Za-z0-9_]+$")


class KillSwitchRuleTests(unittest.TestCase):
    def test_split_tunnel_rules(self):
        rules = h._ks_rules(["1.2.3.4"], {4: {"192.168.1.0/24"}, 6: set()},
                            {4: ["10.0.0.0/8"], 6: []}, False, True)
        text = "\n".join(rules)
        self.assertIn("rt ipsec exists accept", rules[0])
        self.assertIn("ip daddr 1.2.3.4 accept", text)
        self.assertIn("ip daddr { 10.0.0.0/8 } counter reject", text)
        self.assertNotIn("comment", text)

    def test_full_tunnel_without_lan(self):
        rules = h._ks_rules(["1.2.3.4"], {4: set(), 6: set()}, {4: ["0.0.0.0/0"], 6: []}, True, False)
        self.assertTrue(rules[-1].strip().startswith("counter reject"))
        self.assertFalse(any("fe80" in r for r in rules))


class LogErrorTests(unittest.TestCase):
    def test_failures_reach_the_journal(self):
        from unittest import mock
        with mock.patch.object(h.syslog, "openlog"), mock.patch.object(h.syslog, "syslog") as log:
            h.log_error(["up", "office", "dns"], h.HelperError("DNS 10.0.0.53\nno responde"))
            h.log_error(["status"], "polled, would flood")
            h.log_error(["certs"], "polled, would flood")
        self.assertEqual(log.call_count, 1)
        prio, msg = log.call_args.args
        self.assertEqual(prio, h.syslog.LOG_WARNING)
        self.assertEqual(msg, "up office dns: DNS 10.0.0.53 no responde")


class MssTests(unittest.TestCase):
    def test_rules_only_touch_ipsec_syns(self):
        from unittest import mock
        with mock.patch.object(h.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            h.mss_clamp_on()
        script = run.call_args_list[-1].kwargs["input"]
        self.assertIn("table inet swangui_mss {", script)
        self.assertIn("rt ipsec exists meta nfproto ipv4 tcp flags & (syn|rst) == syn "
                      "tcp option maxseg size > 1360 tcp option maxseg size set 1360", script)
        self.assertIn("meta ipsec exists meta nfproto ipv6", script)
        self.assertEqual(script.count("maxseg size set"), 4)  # in and out, IPv4 and IPv6

    def test_removed_with_the_last_tunnel(self):
        from unittest import mock
        with mock.patch.object(h, "run_swanctl", return_value=(0, "office: #1, ESTABLISHED, IKEv2, a_i* b_r")):
            self.assertTrue(h.active_sas())
        with mock.patch.object(h, "run_swanctl", return_value=(0, "")):
            self.assertFalse(h.active_sas())


class ResolvedTests(unittest.TestCase):
    def test_resolv_conf_detection(self):
        import tempfile
        d = tempfile.mkdtemp()
        stub, plain, nm = (os.path.join(d, n) for n in ("stub", "plain", "nm"))
        with open(plain, "w") as f:
            f.write("nameserver 192.168.1.1\n")
        with open(nm, "w") as f:
            f.write("# Generated by NetworkManager\nnameserver 127.0.0.53\noptions edns0\n")
        self.assertFalse(h.resolv_conf_uses_resolved(plain))
        self.assertTrue(h.resolv_conf_uses_resolved(nm))
        self.assertFalse(h.resolv_conf_uses_resolved(stub))  # missing
        with open(plain, "a") as f:
            f.write("nameserver 127.0.0.53\nnameserver fe80::1%eth0\nnameserver 192.168.1.1\nnameserver junk\n")
        self.assertEqual(h.resolv_conf_servers(plain), ["192.168.1.1", "fe80::1%eth0"])

    def test_setup_is_admin_only(self):
        rules = open(os.path.join(HERE, "..", "data", "49-swangui.rules")).read()
        routine = rules[rules.index("var routine"):rules.index("];", rules.index("var routine"))]
        self.assertNotIn("dns-setup", routine)  # installs packages: always asks for the password


if __name__ == "__main__":
    unittest.main()
