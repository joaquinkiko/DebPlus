#!/bin/bash
# Hyprland from trixie-backports for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"
BACKPORTS_SUITE="trixie-backports"
USER_NAME="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"
USER_HOME="/home/$USER_NAME"

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

if ! grep -q "$BACKPORTS_SUITE" /etc/apt/sources.list.d/debian.sources; then
	cat >> /etc/apt/sources.list.d/debian.sources << BACKPORTS

Types: deb
URIs: https://deb.debian.org/debian
Suites: $BACKPORTS_SUITE
Components: main contrib non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
BACKPORTS
fi
printf 'Package: *\nPin: release a=%s\nPin-Priority: 100\n' "$BACKPORTS_SUITE" \
	> /etc/apt/preferences.d/hyprland-backports

apt-get update
apt-get install -y --no-install-recommends -t "$BACKPORTS_SUITE" \
	hyprland hyprlock hypridle hyprpolkitagent xdg-desktop-portal-hyprland

apt-get install -y --no-install-recommends \
	waybar wofi kitty mako-notifier grim slurp wl-clipboard fonts-dejavu-core \
	swaybg xdg-desktop-portal-gtk \
	libpam-systemd policykit-1 \
	lightdm lightdm-gtk-greeter \
	network-manager-gnome \
	pipewire pipewire-pulse wireplumber pavucontrol brightnessctl playerctl \
	mousepad mpv nemo synaptic lxtask \
	xdg-user-dirs xdg-utils

install -Dm644 "$SCRIPT_DIRECTORY/hyprland.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/waybar.jsonc" /etc/xdg/waybar/config.jsonc
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults

install -d -o "$USER_NAME" -g "$USER_NAME" "$USER_HOME/.config" "$USER_HOME/.config/hypr"
install -Dm644 -o "$USER_NAME" -g "$USER_NAME" "$SCRIPT_DIRECTORY/hyprland.lua" "$USER_HOME/.config/hypr/hyprland.lua"

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/kitty 50
update-alternatives --set x-terminal-emulator /usr/bin/kitty

systemctl enable lightdm.service
runuser -u "$USER_NAME" -- xdg-user-dirs-update
apt-get clean
