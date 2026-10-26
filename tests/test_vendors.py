"""Round trips and real-world samples for the vendor formats."""
import unittest

from test_gui import g
from test_helper import h

EAP_CONF = """
connections {
    office {
        version = 2
        remote_addrs = 203.0.113.10
        vips = 0.0.0.0
        proposals = aes256-sha256-modp2048,aes128-sha256-modp2048
        local {
            auth = eap-mschapv2
            eap_id = alice
        }
        remote {
            auth = psk
            id = vpn.example.com
        }
        children {
            office {
                remote_ts = 10.0.0.0/8,192.168.10.0/24
                esp_proposals = aes256-sha256-modp2048
            }
        }
    }
}
"""


def view_of(text, name="office"):
    return h.conn_to_view(name, "", h.parse(text))


SECRETS = {"psk": "Pre$hared \"key\"", "password": "pa'ss"}


class RoundTrips(unittest.TestCase):
    def roundtrip(self, fmt, view=None, secrets=SECRETS):
        view = view or view_of(EAP_CONF)
        _k, _l, _s, exporter = next(f for f in g.EXPORT_FORMATS if f[0] == fmt)
        text, warnings = exporter([(view, secrets, {"description": "Oficina"})])
        self.assertEqual(g.detect_format(text), fmt, text[:200])
        items, warnings2 = g.IMPORTERS[fmt](text)
        self.assertEqual(len(items), 1)
        return items[0][0], text

    def test_mikrotik(self):
        v, text = self.roundtrip("mikrotik")
        self.assertEqual(v["remote_addrs"], "203.0.113.10")
        self.assertEqual(v["version"], "2")
        self.assertEqual(v["eap_id"], "alice")
        self.assertEqual(v["eap_password"], "pa'ss")
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")
        self.assertEqual(v["remote_id"], "vpn.example.com")
        self.assertIn("aes256-sha256", v["proposals"])
        self.assertTrue(v["vips"])

    def test_mikrotik_psk_ikev1_xauth(self):
        conf = EAP_CONF.replace("version = 2", "version = 1\n        aggressive = yes").replace(
            "auth = eap-mschapv2\n            eap_id = alice", "auth = psk").replace(
            "remote {", "local-xauth {\n            auth = xauth\n            xauth_id = bob\n        }\n        remote {")
        v, text = self.roundtrip("mikrotik", view_of(conf))
        self.assertTrue(v["aggressive"])
        self.assertTrue(v["xauth"])
        self.assertEqual(v["xauth_id"], "bob")
        self.assertEqual(v["psk"], SECRETS["psk"])

    def test_pfsense(self):
        conf = EAP_CONF.replace("auth = eap-mschapv2\n            eap_id = alice", "auth = psk")
        v, _ = self.roundtrip("pfsense", view_of(conf))
        self.assertEqual(v["psk"], SECRETS["psk"])
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")
        self.assertIn("modp2048", v["esp_proposals"])

    def test_windows(self):
        v, text = self.roundtrip("windows")
        self.assertIn("-SplitTunneling", text)
        self.assertEqual(v["local_auth"], "eap-mschapv2")
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")
        self.assertIn("aes256", v["proposals"])

    def test_cisco(self):
        conf = EAP_CONF.replace("auth = eap-mschapv2\n            eap_id = alice", "auth = psk")
        v, text = self.roundtrip("cisco", view_of(conf))
        self.assertEqual(v["psk"], SECRETS["psk"])
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")
        self.assertEqual(v["remote_id"], "vpn.example.com")

    def test_apple(self):
        v, _ = self.roundtrip("apple")
        self.assertEqual(v["eap_id"], "alice")
        self.assertEqual(v["eap_password"], "pa'ss")
        self.assertEqual(v["psk"], SECRETS["psk"])

    def test_sswan(self):
        v, _ = self.roundtrip("sswan")
        self.assertEqual(v["eap_id"], "alice")
        self.assertEqual(v["remote_ts"], "10.0.0.0/8,192.168.10.0/24")

    def test_imported_values_pass_helper_validation(self):
        for fmt in g.IMPORTERS:
            if fmt == "forti":
                continue
            v, _ = self.roundtrip(fmt)
            tree = {"connections": {}}
            conn = tree["connections"].setdefault(v["name"], {})
            h.apply_view(conn, v)
            h.check_tree(tree)


class RealSamples(unittest.TestCase):
    def test_routeros_export(self):
        text = r"""# 2024-05-01 by RouterOS 7.14
/ip ipsec mode-config
add name=cfg1 responder=no
/ip ipsec policy group
add name=grp1
/ip ipsec profile
add dh-group=modp2048 enc-algorithm=aes-256,aes-128 hash-algorithm=sha256 \
    lifetime=8h name=prof1
/ip ipsec peer
add address=198.51.100.7/32 exchange-mode=ike2 name=hq profile=prof1
/ip ipsec proposal
set [ find default=yes ] enc-algorithms=aes-256-cbc pfs-group=none
/ip ipsec identity
add auth-method=pre-shared-key generate-policy=port-strict mode-config=cfg1 \
    peer=hq policy-template-group=grp1 secret="s3cret key"
/ip ipsec policy
add dst-address=172.16.0.0/12 group=grp1 src-address=0.0.0.0/0 template=yes
"""
        (v, meta), = g.mikrotik_import(text)[0]
        self.assertEqual(v["remote_addrs"], "198.51.100.7")
        self.assertEqual(v["psk"], "s3cret key")
        self.assertEqual(v["remote_ts"], "172.16.0.0/12")
        self.assertEqual(v["rekey_time"], "28800s")
        self.assertEqual(v["esp_proposals"], "aes256-sha1")

    def test_cisco_running_config(self):
        text = """
crypto isakmp policy 10
 encr aes 256
 encryption aes 256
 hash sha256
 authentication pre-share
 group 14
crypto isakmp key MyKey address 192.0.2.1
crypto ipsec transform-set TS esp-aes 256 esp-sha-hmac
 mode tunnel
ip access-list extended VPN
 permit ip 192.168.1.0 0.0.0.255 10.10.0.0 0.0.255.255
crypto map CMAP 10 ipsec-isakmp
 set peer 192.0.2.1
 set transform-set TS
 match address VPN
"""
        (v, _), = g.cisco_import(text)[0]
        self.assertEqual(v["version"], "1")
        self.assertEqual(v["psk"], "MyKey")
        self.assertEqual(v["remote_ts"], "10.10.0.0/16")
        self.assertEqual(v["proposals"], "aes256-sha256-modp2048")
        self.assertEqual(v["esp_proposals"], "aes256-sha1")

    def test_detect(self):
        self.assertEqual(g.detect_format("connections { }"), "swanctl")
        self.assertEqual(g.detect_format("<forticlient_configuration/>"), "forti")
        self.assertEqual(g.detect_format("<pfsense><ipsec><phase1/></ipsec></pfsense>"), "pfsense")

    def test_garbage(self):
        for fmt, imp in g.IMPORTERS.items():
            with self.assertRaises(ValueError, msg=fmt):
                imp("this is not a config")


if __name__ == "__main__":
    unittest.main()
