#!/bin/bash
# Minimal Sway for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"
USER_NAME="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	sway swaybg swayidle swaylock xwayland waybar wofi kitty mako-notifier grim slurp \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk autotiling \
	libpam-systemd policykit-1 lxpolkit \
	lightdm lightdm-gtk-greeter \
	network-manager-gnome \
	pipewire pipewire-pulse wireplumber pavucontrol brightnessctl playerctl \
	mousepad mpv nemo synaptic lxtask \
	xdg-user-dirs xdg-utils sensible-utils fonts-recommended

install -Dm644 "$SCRIPT_DIRECTORY/config" /etc/sway/config
install -Dm644 "$SCRIPT_DIRECTORY/sway.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/waybar.jsonc" /etc/xdg/waybar/config.jsonc
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/kitty 50
update-alternatives --set x-terminal-emulator /usr/bin/kitty

systemctl enable lightdm.service
runuser -u "$USER_NAME" -- xdg-user-dirs-update
apt-get clean
