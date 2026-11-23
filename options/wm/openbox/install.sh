#!/bin/bash
# Minimal Openbox for DebPlus. Runs inside target chroot.
set -e
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIRECTORY="$(dirname "$(readlink -f "$0")")"
USER_NAME="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"

echo "shared/default-x-display-manager select lightdm" | debconf-set-selections

apt-get install -y --no-install-recommends \
	xserver-xorg openbox tint2 rofi dunst picom alacritty feh maim i3lock xss-lock \
	lightdm lightdm-gtk-greeter \
	network-manager-gnome lxpolkit policykit-1 \
	pipewire pipewire-pulse wireplumber pavucontrol brightnessctl playerctl \
	mousepad mpv nemo synaptic lxtask \
	xdg-user-dirs xdg-utils libpam-systemd fonts-recommended

install -Dm644 "$SCRIPT_DIRECTORY/rc.xml" /etc/xdg/openbox/rc.xml
install -Dm644 "$SCRIPT_DIRECTORY/autostart" /etc/xdg/openbox/autostart
install -Dm644 "$SCRIPT_DIRECTORY/openbox.png" /usr/share/backgrounds/wallpaper.png
install -Dm644 "$SCRIPT_DIRECTORY/mimeapps.list" /etc/xdg/mimeapps.list
install -Dm644 "$SCRIPT_DIRECTORY/user-dirs.defaults" /etc/xdg/user-dirs.defaults

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/alacritty 50
update-alternatives --set x-terminal-emulator /usr/bin/alacritty

systemctl enable lightdm.service
runuser -u "$USER_NAME" -- xdg-user-dirs-update
apt-get clean
