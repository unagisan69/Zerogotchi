VERSION = 0.4.3
ARCH = all
PACKAGE = build/zerogotchi_$(VERSION)_$(ARCH).deb
STAGE = build/package
SOURCES = app/zerogotchi app/wifi.py app/gnss.py app/radio
ASSETS = packaging/zerogotchi.desktop packaging/zerogotchi.png

.PHONY: all package clean
all: package
package: $(PACKAGE)

$(PACKAGE): $(SOURCES) $(ASSETS) Makefile
	rm -rf $(STAGE)
	install -Dm755 app/zerogotchi $(STAGE)/usr/lib/zerogotchi/zerogotchi
	install -Dm755 app/radio $(STAGE)/usr/lib/zerogotchi/radio
	install -Dm644 app/wifi.py $(STAGE)/usr/lib/zerogotchi/wifi.py
	install -Dm644 app/gnss.py $(STAGE)/usr/lib/zerogotchi/gnss.py
	install -d -m755 $(STAGE)/usr/bin
	ln -s ../lib/zerogotchi/zerogotchi $(STAGE)/usr/bin/zerogotchi
	install -Dm644 packaging/zerogotchi.png $(STAGE)/usr/share/APPLaunch/share/images/zerogotchi.png
	install -Dm644 packaging/zerogotchi.desktop $(STAGE)/usr/share/APPLaunch/applications/zerogotchi.desktop
	install -d -m755 $(STAGE)/DEBIAN
	printf '%s\n' 'Package: zerogotchi' 'Version: $(VERSION)' 'Architecture: $(ARCH)' 'Maintainer: Zerogotchi contributors' 'Depends: python3, python3-pil, fonts-dejavu-core, network-manager, sudo, iw, iproute2, tcpdump, aircrack-ng, hcxtools' 'Description: Wi-Fi auditing for the M5Stack Cardputer Zero' > $(STAGE)/DEBIAN/control
	dpkg-deb --root-owner-group --build $(STAGE) $@

clean:
	rm -rf build
