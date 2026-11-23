#!/bin/bash
# Minimal i3 (X11) for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"
USER_NAME="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	xserver-xorg i3-wm i3status i3lock xss-lock rofi dunst picom alacritty feh maim \
	lightdm lightdm-gtk-greeter firefox-esr \
	network-manager-gnome lxpolkit policykit-1 \
	pipewire pipewire-pulse wireplumber pavucontrol brightnessctl playerctl \
	mousepad mpv nemo synaptic lxtask \
	xdg-user-dirs xdg-utils sensible-utils libpam-systemd fonts-recommended

apt-get install -y --no-install-recommends \
	xdg-desktop-portal gvfs gvfs-backends udisks2 imv zathura file-roller \
	gnome-keyring libpam-gnome-keyring seahorse \
	xserver-xorg-input-libinput x11-xserver-utils arandr oblogout \
	redshift-gtk geoclue-2.0

install -Dm644 "$SCRIPT_DIRECTORY/config" /etc/i3/config
install -Dm644 "$SCRIPT_DIRECTORY/i3status.conf" /etc/i3status.conf
install -Dm644 "$SCRIPT_DIRECTORY/i3.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults
install -Dm644 "$SCRIPT_DIRECTORY/gtk-settings.ini" /etc/xdg/gtk-3.0/settings.ini
install -Dm644 "$SCRIPT_DIRECTORY/gtk-settings.ini" /etc/xdg/gtk-4.0/settings.ini
install -Dm644 "$SCRIPT_DIRECTORY/theme" /etc/i3/theme

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/alacritty 50
update-alternatives --set x-terminal-emulator /usr/bin/alacritty
update-alternatives --set x-www-browser /usr/bin/firefox-esr

systemctl enable lightdm.service
runuser -u "$USER_NAME" -- xdg-user-dirs-update
apt-get clean
