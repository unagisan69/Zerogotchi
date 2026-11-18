VERSION = $(shell sed -n 's/^PKG_VERSION=//p' packaging/meta.env)
ARCH = $(shell sed -n 's/^PKG_ARCH=//p' packaging/meta.env)
PACKAGE = build/zerogotchi_$(VERSION)_$(ARCH).deb
STAGE = build/package
SOURCES = $(wildcard app/*.py app/zerogotchi app/radio app/themes/*/*.png)
PACKAGING = $(wildcard packaging/*)

.PHONY: all package appbuilder-package clean
all: package
package: $(PACKAGE)

$(PACKAGE): $(SOURCES) $(PACKAGING) Makefile
	rm -rf $(STAGE)
	install -d -m755 $(STAGE)/DEBIAN
	set -e; . ./packaging/meta.env; \
		STAGE="$(CURDIR)/$(STAGE)" INSTALL_PREFIX=/usr/share/APPLaunch ./packaging/stage.sh; \
		install -d -m755 $(STAGE)/usr/share/APPLaunch/applications; \
		sed -e "s|{{APP_NAME}}|$$APP_NAME|g" -e "s|{{APP_EXEC}}|$$APP_EXEC|g" \
			-e "s|{{APP_ICON}}|$$APP_ICON|g" -e "s|{{APP_TERMINAL}}|$$APP_TERMINAL|g" \
			packaging/app.desktop.in > $(STAGE)/usr/share/APPLaunch/applications/zerogotchi.desktop; \
		printf '%s\n' "Package: $$PKG_NAME" "Version: $$PKG_VERSION" "Architecture: $$PKG_ARCH" \
			"Maintainer: $$PKG_MAINTAINER" "Section: APPLaunch" "Priority: optional" \
			"Homepage: $$PKG_HOMEPAGE" "Depends: $$PKG_DEPENDS" "Description: $$PKG_DESC" > $(STAGE)/DEBIAN/control
	dpkg-deb --root-owner-group --build $(STAGE) $@

appbuilder-package:
	@test -f "$(APPBUILDER)/scripts/pack-deb.sh" || { echo "Set APPBUILDER to a CardputerZero AppBuilder checkout"; exit 1; }
	umask 022; bash "$(APPBUILDER)/scripts/pack-deb.sh" "$(CURDIR)" --skip-build --output-dir "$(CURDIR)/build/appbuilder"

clean:
	rm -rf build
