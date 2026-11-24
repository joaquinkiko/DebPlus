#!/bin/bash
# Minimal Sway for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"
USER_NAME="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"

apt-get install -y --no-install-recommends \
	sway swaybg swayidle swaylock xwayland waybar wofi kitty mako-notifier grim slurp \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk autotiling \
	libpam-systemd policykit-1 lxpolkit \
	greetd seatd tuigreet \
	network-manager-gnome \
	pipewire pipewire-pulse wireplumber pavucontrol brightnessctl playerctl \
	mousepad mpv nemo synaptic lxtask \
	xdg-user-dirs xdg-utils sensible-utils fonts-recommended

apt-get install -y --no-install-recommends \
	xdg-desktop-portal gvfs gvfs-backends udisks2 imv zathura file-roller \
	gnome-keyring libpam-gnome-keyring seahorse \
	wdisplays wlogout wlsunset

. /etc/default/keyboard
KEYBOARD_LAYOUT="${XKBLAYOUT:-us}"

install -Dm644 "$SCRIPT_DIRECTORY/config" /etc/sway/config
sed -i "s/^input type:keyboard {/&\n    xkb_layout $KEYBOARD_LAYOUT/" /etc/sway/config
install -Dm644 "$SCRIPT_DIRECTORY/sway.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/waybar.jsonc" /etc/xdg/waybar/config.jsonc
install -Dm644 "$SCRIPT_DIRECTORY/config.toml" /etc/greetd/config.toml
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults
install -Dm644 "$SCRIPT_DIRECTORY/gtk-settings.ini" /etc/xdg/gtk-3.0/settings.ini
install -Dm644 "$SCRIPT_DIRECTORY/gtk-settings.ini" /etc/xdg/gtk-4.0/settings.ini
install -Dm644 "$SCRIPT_DIRECTORY/theme" /etc/sway/theme
install -Dm644 "$SCRIPT_DIRECTORY/waybar-style.css" /etc/xdg/waybar/style.css

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/kitty 50
update-alternatives --set x-terminal-emulator /usr/bin/kitty
update-alternatives --set x-www-browser /usr/bin/firefox-esr

systemctl enable seatd
systemctl disable getty@tty1.service
systemctl enable greetd.service

getent group seat >/dev/null || groupadd --system seat
for USER_NAME in $(awk -F: '$3>=1000 && $3<65534 {print $1}' /etc/passwd); do
	usermod -aG seat "$USER_NAME"
	runuser -u "$USER_NAME" -- xdg-user-dirs-update
done
apt-get clean
