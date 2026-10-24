#!/bin/bash
# Minimal Openbox for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends xserver-xorg openbox \
	rofi dunst picom alacritty i3lock xss-lock feh maim \
	libpam-systemd fonts-recommended lightdm lightdm-gtk-greeter \
	pipewire pipewire-pulse  \
	xdg-user-dirs-gtk

sed -i 's#</keyboard>#\
  <keybind key="W-Return"><action name="Execute"><command>alacritty</command></action></keybind>\
  <keybind key="W-d"><action name="Execute"><command>rofi -show drun</command></action></keybind>\
  <keybind key="W-S-x"><action name="Execute"><command>i3lock -n -c 000000</command></action></keybind>\
  <keybind key="Print"><action name="Execute"><command>maim -s $HOME/Pictures/$(date +%F_%T).png</command></action></keybind>\
  <keybind key="XF86AudioRaiseVolume"><action name="Execute"><command>wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+</command></action></keybind>\
  <keybind key="XF86AudioLowerVolume"><action name="Execute"><command>wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-</command></action></keybind>\
  <keybind key="XF86AudioMute"><action name="Execute"><command>wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle</command></action></keybind>\
  </keyboard>#' /etc/xdg/openbox/rc.xml

install -D /dev/null /etc/xdg/openbox/autostart
cat > /etc/xdg/openbox/autostart << 'AUTOSTART'
picom -b &
feh --no-fehbg --bg-fill /path/to/wallpaper &
xss-lock --transfer-sleep-lock -- i3lock -n -c 000000 &
AUTOSTART

xdg-user-dirs-update

apt-get clean