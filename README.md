# DebPlus

A TUI installer for Debian 13 "trixie", derived from [butterknife](https://justaguy.dev/drew/butterknife), by the Butterbian project.

Terminal-based. Deliberately simple and elegant. Boot from ISO, choose your settings and windows manager, and reboot into your configured system. System setup kept simple.

A selection of packages and window manager setups are provided for the user, as well as optional full desktop enviroments.

Users may also choose special advanced features like enabling additional apt sources, multiarch-i386, or backports out-of-the-box.

## Build from source

```bash
# Requirements
sudo apt install debootstrap xorriso squashfs-tools\
mtools grub-pc-bin grub-efi-amd64-bin grub-common

# Outputs ISO in project directory as 'debplus-trixie-<YYYYMMDD>.iso'
sudo bash build-iso.sh
```

## Testing

```bash
# Install requirements
sudo apt install python3 python3-urwid

# Start installer (--test ensures no accidental install)
sudo python3 -m installer --test
```

## Support

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/joaquinkiko)

## License

GPL-2.0
