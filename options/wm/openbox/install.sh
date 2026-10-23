#!/bin/bash
# Minimal Openbox for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends xserver-xorg openbox tint2 \
	rofi dunst picom alacritty i3lock xss-lock maim xclip obconf \
	libpam-systemd fonts-dejavu-core lightdm lightdm-gtk-greeter \

sed -i 's|</keyboard>|  <keybind key="W-Return"><action name="Execute"><command>alacritty</command></action></keybind>\n    <keybind key="W-d"><action name="Execute"><command>rofi -show drun</command></action></keybind>\n    <keybind key="W-S-x"><action name="Execute"><command>i3lock -n -c 000000</command></action></keybind>\n  </keyboard>|' /etc/xdg/openbox/rc.xml

install -D /dev/null /etc/xdg/openbox/autostart
cat > /etc/xdg/openbox/autostart << 'AUTOSTART'
tint2 &
dunst &
picom -b
xss-lock --transfer-sleep-lock -- i3lock -n -c 000000 &
AUTOSTART

apt-get clean