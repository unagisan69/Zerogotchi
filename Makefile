CC ?= cc
CFLAGS ?= -O2
VERSION = 0.1.0
ARCH = $(shell dpkg --print-architecture)
PACKAGE = build/zerogotchi_$(VERSION)_$(ARCH).deb
STAGE = build/package

.PHONY: all package clean
all: build/zerogotchi

build/zerogotchi: zerogotchi.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -Wpedantic $< $(LDFLAGS) -o $@

package: $(PACKAGE)

$(PACKAGE): build/zerogotchi packaging/zerogotchi.desktop packaging/zerogotchi.png Makefile
	rm -rf $(STAGE)
	install -Dm755 build/zerogotchi $(STAGE)/usr/bin/zerogotchi
	install -Dm644 packaging/zerogotchi.png $(STAGE)/usr/share/APPLaunch/share/images/zerogotchi.png
	install -Dm644 packaging/zerogotchi.desktop $(STAGE)/usr/share/APPLaunch/applications/zerogotchi.desktop
	mkdir -p $(STAGE)/DEBIAN
	printf '%s\n' 'Package: zerogotchi' 'Version: $(VERSION)' 'Architecture: $(ARCH)' 'Maintainer: Zerogotchi contributors' 'Depends: libc6' 'Description: Zerogotchi Hello World for Cardputer Zero' > $(STAGE)/DEBIAN/control
	dpkg-deb --root-owner-group --build $(STAGE) $@

clean:
	rm -rf build
