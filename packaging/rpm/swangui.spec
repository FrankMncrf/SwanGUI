Name:           swangui
Version:        1.0.10
Release:        1%{?dist}
Summary:        FortiClient-style IPsec VPN client for strongSwan
License:        GPL-3.0-or-later
URL:            https://github.com/FrankMncrf/SwanGUI
Source0:        %{url}/archive/refs/tags/v%{version}/SwanGUI-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  make
Requires:       python3 >= 3.10
Requires:       python3-pyqt6
Requires:       strongswan
Requires:       polkit
Requires:       nftables
Requires:       iproute
Requires:       openssl
Requires:       iputils
Requires:       python3-cryptography
Requires:       libsecret
Requires:       NetworkManager
Requires:       systemd-resolved

%description
Tray application to create, edit, import/export, connect and monitor
strongSwan IPsec VPNs (IKEv1/IKEv2, PSK, certificates, EAP, XAuth) with
split DNS, kill switch, trusted networks, diagnostics and vendor
import/export (FortiClient, MikroTik, pfSense, Windows, Cisco, Apple).

%prep
%autosetup -n SwanGUI-%{version}

%build

%install
make install DESTDIR=%{buildroot} PREFIX=%{_prefix} LIBDIR=%{_prefix}/lib/swangui \
    DOCDIR=%{_docdir}/%{name} LICENSEDIR=%{_licensedir}/%{name}

%files
%{_prefix}/lib/swangui/
%{_bindir}/swangui
%{_datadir}/polkit-1/actions/org.swangui.helper.policy
%{_datadir}/polkit-1/rules.d/49-swangui.rules
%{_datadir}/applications/swangui.desktop
%{_datadir}/icons/hicolor/scalable/apps/swangui.svg
%{_docdir}/%{name}/
%{_licensedir}/%{name}/

%changelog
* Thu Oct 29 2026 FrankMncrf <frank@swangui.invalid> - 1.0.10-1
- FortiClient 7.4 import: encrypted EncX keys are no longer saved as the PSK
- New PSKs without IDs are bound to the gateway IP; per-connection VPN DNS
- Hints for strongSwan without IKEv1 and for IKEv1 Main/Aggressive Mode
- Resizable columns, remote networks in Details, list shows five connections

* Wed Oct 28 2026 FrankMncrf <frank@swangui.invalid> - 1.0.9-1
- Author shown as FrankMncrf in About and in the packages

* Tue Oct 27 2026 FrankMncrf <frank@swangui.invalid> - 1.0.8-1
- Offer to install and enable systemd-resolved when the VPN DNS needs it

* Tue Oct 27 2026 FrankMncrf <frank@swangui.invalid> - 1.0.7-1
- All dependencies are required: python3-cryptography, libsecret, NetworkManager, systemd-resolved

* Tue Oct 27 2026 FrankMncrf <frank@swangui.invalid> - 1.0.6-1
- TCP MSS clamping for traffic through the tunnel (fixes stalled connections)
- Total traffic no longer resets when the CHILD_SA is rekeyed

* Tue Oct 27 2026 FrankMncrf <frank@swangui.invalid> - 1.0.5-1
- Narrower main window, traffic graph with time ranges, axes and grid
- Graph data of the current session survives restarts (last hour only)
- Latency measured against the VPN DNS when the first remote host does not answer ping

* Mon Oct 26 2026 FrankMncrf <frank@swangui.invalid> - 1.0.4-1
- Helper errors, including DNS failures while connecting, go to the system journal

* Mon Oct 26 2026 FrankMncrf <frank@swangui.invalid> - 1.0.3-1
- No password prompt after suspend; new Buy Me a Coffee link

* Sun Oct 25 2026 FrankMncrf <frank@swangui.invalid> - 1.0.2-1
- Connection details card when hovering over the tray icon

* Sun Oct 25 2026 FrankMncrf <frank@swangui.invalid> - 1.0.1-1
- Own notification bubble next to the tray icon

* Sun Oct 25 2026 FrankMncrf <frank@swangui.invalid> - 1.0.0-1
- First public release
