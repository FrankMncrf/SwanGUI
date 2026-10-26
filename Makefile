# SwanGUI — install layout shared by install.sh and the distro packages.
#   make install DESTDIR=/tmp/pkg PREFIX=/usr
PREFIX ?= /usr/local
LIBDIR ?= $(PREFIX)/lib/swangui
BINDIR ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share
DOCDIR ?= $(DATADIR)/doc/swangui
LICENSEDIR ?= $(DATADIR)/licenses/swangui
# polkit only reads these locations, whatever the prefix
POLKIT_ACTIONDIR ?= /usr/share/polkit-1/actions
POLKIT_RULESDIR ?= /usr/share/polkit-1/rules.d

.PHONY: all install uninstall test

all:

test:
	cd tests && python3 -m unittest -v test_helper test_gui test_vendors test_app

install:
	install -Dm755 swangui-helper $(DESTDIR)$(LIBDIR)/swangui-helper
	install -Dm755 swangui.py $(DESTDIR)$(LIBDIR)/swangui.py
	install -Dm644 i18n/en.json $(DESTDIR)$(LIBDIR)/i18n/en.json
	install -Dm644 CHANGELOG.md $(DESTDIR)$(LIBDIR)/CHANGELOG.md
	install -d $(DESTDIR)$(BINDIR)
	ln -sf $(LIBDIR)/swangui.py $(DESTDIR)$(BINDIR)/swangui
	install -d $(DESTDIR)$(POLKIT_ACTIONDIR)
	sed 's|@LIBDIR@|$(LIBDIR)|g' data/org.swangui.helper.policy.in \
		> $(DESTDIR)$(POLKIT_ACTIONDIR)/org.swangui.helper.policy
	chmod 644 $(DESTDIR)$(POLKIT_ACTIONDIR)/org.swangui.helper.policy
	install -Dm644 data/49-swangui.rules $(DESTDIR)$(POLKIT_RULESDIR)/49-swangui.rules
	install -Dm644 data/swangui.desktop $(DESTDIR)$(DATADIR)/applications/swangui.desktop
	install -Dm644 data/swangui.svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/swangui.svg
	install -Dm644 README.md $(DESTDIR)$(DOCDIR)/README.md
	install -Dm644 LICENSE $(DESTDIR)$(LICENSEDIR)/LICENSE

uninstall:
	rm -rf $(DESTDIR)$(LIBDIR) $(DESTDIR)$(DOCDIR) $(DESTDIR)$(LICENSEDIR)
	rm -f $(DESTDIR)$(BINDIR)/swangui \
		$(DESTDIR)$(POLKIT_ACTIONDIR)/org.swangui.helper.policy \
		$(DESTDIR)$(POLKIT_RULESDIR)/49-swangui.rules \
		$(DESTDIR)$(DATADIR)/applications/swangui.desktop \
		$(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/swangui.svg
