#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

apt-get install -y --no-install-recommends \
	labwc seatd xwayland foot fuzzel fonts-recommended \
	pipewire-audio wireplumber pavucontrol \
	grim slurp wl-clipboard waybar \
	brightnessctl playerctl \
	xdg-user-dirs-gtk \
	swaybg swayidle swaylock greetd wlopm \
	libpam-systemd dbus-user-session \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk \
	lxpolkit mako-notifier tuigreet

systemctl enable seatd

install -D -m 644 "$SCRIPT_DIR/labwc.png" /usr/share/backgrounds/wallpaper.png
systemctl disable getty@tty1.service
systemctl enable greetd.service

# System-wide defaults. Per-user files in ~/.config override these.
install -D -m 644 "$SCRIPT_DIR/rc.xml"       /etc/xdg/labwc/rc.xml
install -D -m 644 "$SCRIPT_DIR/autostart"    /etc/xdg/labwc/autostart
install -D -m 644 "$SCRIPT_DIR/config.jsonc" /etc/xdg/waybar/config.jsonc
install -D -m 644 "$SCRIPT_DIR/config.toml"  /etc/greetd/config.toml

# Grant seat access to every regular user
getent group seat >/dev/null || groupadd --system seat
for user_name in $(awk -F: '$3>=1000 && $3<65534 {print $1}' /etc/passwd); do
	usermod -aG seat "$user_name"
done