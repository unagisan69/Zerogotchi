# Zerogotchi

A Hello World app for the M5Stack Cardputer Zero. Select **Zerogotchi** in the stock launcher to open it, then press **Esc** to return.

## How the stock launcher finds apps

M5Stack's APPLaunch watches `/usr/share/APPLaunch/applications/` for `.desktop` entries. Zerogotchi's entry names the executable with `Exec`, supplies the installed PNG path with `Icon`, and sets `Terminal=false` to launch it as a standalone process. APPLaunch pauses its own interface while the process runs and restores the carousel when it exits.

Zerogotchi draws directly to the 320 × 170 RGB565 panel at `/dev/fb_lcd` and reads Esc from `/dev/input/cardputer-zero-internal`. The stock OS provides both device nodes and their permissions. The app runs as the launcher user. See the [APPLaunch application model](https://github.com/CardputerZero/launcher/blob/master/docs/launcher-project-guide/04-application-model-and-launch-mechanism.md).

## Build and install on the Cardputer Zero

From a checkout on the Cardputer, run:

```sh
make package
sudo apt install ./build/zerogotchi_0.1.0_arm64.deb
```

The build uses a C compiler, Linux headers, GNU Make, and `dpkg-deb`. Install `build-essential` if the toolchain is missing. APPLaunch reloads its app list when the entry appears.

To remove the app, run `sudo apt remove zerogotchi`.
