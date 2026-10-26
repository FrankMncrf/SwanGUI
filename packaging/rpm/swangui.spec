Name:           swangui
Version:        1.0.0
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
Recommends:     python3-cryptography
Recommends:     libsecret
Recommends:     NetworkManager
Recommends:     systemd-resolved

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
* Sun Oct 25 2026 Frank <FrankMncrf@users.noreply.github.com> - 1.0.0-1
- First public release
