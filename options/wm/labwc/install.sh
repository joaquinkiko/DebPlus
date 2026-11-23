#!/bin/bash
# Minimal labwc (Wayland) for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	labwc seatd xwayland foot fuzzel fonts-recommended \
	pipewire-audio wireplumber pavucontrol \
	grim slurp wl-clipboard waybar \
	brightnessctl playerctl \
	xdg-user-dirs xdg-utils \
	swaybg swayidle swaylock wlopm \
	libpam-systemd dbus-user-session \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk \
	lxpolkit policykit-1 mako-notifier \
	network-manager-gnome \
	lightdm lightdm-gtk-greeter \
	mousepad mpv nemo synaptic lxtask

install -Dm644 "$SCRIPT_DIRECTORY/labwc.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/rc.xml" /etc/xdg/labwc/rc.xml
install -Dm644 "$SCRIPT_DIRECTORY/autostart" /etc/xdg/labwc/autostart
install -Dm644 "$SCRIPT_DIRECTORY/config.jsonc" /etc/xdg/waybar/config.jsonc
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/foot 50
update-alternatives --set x-terminal-emulator /usr/bin/foot

systemctl enable seatd
systemctl enable lightdm.service

getent group seat >/dev/null || groupadd --system seat
for USER_NAME in $(awk -F: '$3>=1000 && $3<65534 {print $1}' /etc/passwd); do
	usermod -aG seat "$USER_NAME"
	runuser -u "$USER_NAME" -- xdg-user-dirs-update
done
apt-get clean
