#!/bin/bash
# Minimal i3 (X11) for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends xserver-xorg xinit i3-wm i3status \
	i3lock xss-lock rofi dunst picom alacritty maim xclip \
	libpam-systemd fonts-dejavu-core lightdm lightdm-gtk-greeter

install -D /dev/null /etc/i3/config
cat > /etc/i3/config << 'I3'
set $mod Mod4
set $term alacritty

font pango:monospace 10
floating_modifier $mod

exec --no-startup-id picom -b
exec --no-startup-id dunst
exec --no-startup-id xss-lock --transfer-sleep-lock -- i3lock -n -c 000000

bindsym $mod+Return exec $term
bindsym $mod+d exec rofi -show drun
bindsym $mod+Shift+q kill
bindsym $mod+Shift+x exec i3lock -n -c 000000
bindsym $mod+Shift+e exec i3-nagbar -t warning -m 'Exit i3?' -b 'Yes' 'i3-msg exit'
bindsym $mod+Shift+c reload
bindsym $mod+Shift+r restart
bindsym Print exec maim -s | xclip -selection clipboard -t image/png

bindsym $mod+h focus left
bindsym $mod+j focus down
bindsym $mod+k focus up
bindsym $mod+l focus right
bindsym $mod+Shift+h move left
bindsym $mod+Shift+j move down
bindsym $mod+Shift+k move up
bindsym $mod+Shift+l move right
bindsym $mod+b split h
bindsym $mod+v split v
bindsym $mod+f fullscreen toggle
bindsym $mod+Shift+space floating toggle

bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
bindsym $mod+3 workspace number 3
bindsym $mod+4 workspace number 4
bindsym $mod+5 workspace number 5
bindsym $mod+Shift+1 move container to workspace number 1
bindsym $mod+Shift+2 move container to workspace number 2
bindsym $mod+Shift+3 move container to workspace number 3
bindsym $mod+Shift+4 move container to workspace number 4
bindsym $mod+Shift+5 move container to workspace number 5

bar {
	status_command i3status
}
I3

apt-get clean