#!/bin/bash
# Minimal Sway for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends sway swaybg swayidle swaylock \
	xwayland waybar wofi foot mako-notifier grim slurp wl-clipboard \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk libpam-systemd \
	fonts-font-awesome fonts-dejavu-core lightdm lightdm-gtk-greeter

install -D /dev/null /etc/sway/config
cat > /etc/sway/config << 'SWAY'
set $mod Mod4
set $term foot
set $menu wofi --show drun
set $lock swaylock -f -c 000000

font pango:monospace 10
floating_modifier $mod

exec dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP=sway
exec waybar
exec mako
exec swayidle -w timeout 600 'swaylock -f -c 000000' timeout 900 'swaymsg "output * power off"' resume 'swaymsg "output * power on"' before-sleep 'swaylock -f -c 000000'

bindsym $mod+Return exec $term
bindsym $mod+d exec $menu
bindsym $mod+Shift+q kill
bindsym $mod+Shift+x exec $lock
bindsym $mod+Shift+e exec swaynag -t warning -m 'Exit sway?' -B 'Yes' 'swaymsg exit'
bindsym $mod+Shift+c reload
bindsym Print exec grim -g "$(slurp)" - | wl-copy

bindsym $mod+h focus left
bindsym $mod+j focus down
bindsym $mod+k focus up
bindsym $mod+l focus right
bindsym $mod+Shift+h move left
bindsym $mod+Shift+j move down
bindsym $mod+Shift+k move up
bindsym $mod+Shift+l move right
bindsym $mod+b splith
bindsym $mod+v splitv
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
SWAY

apt-get clean