#!/bin/bash
# Hyprland from trixie-backports for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

backports_suite="trixie-backports"

if ! grep -q "$backports_suite" /etc/apt/sources.list.d/debian.sources; then
	cat >> /etc/apt/sources.list.d/debian.sources << BACKPORTS

Types: deb
URIs: https://deb.debian.org/debian
Suites: $backports_suite
Components: main contrib non-free-firmware non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
BACKPORTS
fi
printf 'Package: *\nPin: release a=%s\nPin-Priority: 100\n' "$backports_suite" \
	> /etc/apt/preferences.d/backports

apt-get update
apt-get install -y --no-install-recommends -t "$backports_suite" \
	hyprland hyprlock hypridle hyprpolkitagent xdg-desktop-portal-hyprland
apt-get install -y --no-install-recommends waybar wofi foot mako-notifier \
	grim slurp wl-clipboard xdg-desktop-portal-gtk fonts-dejavu-core \
	libpam-systemd lightdm lightdm-gtk-greeter

install -D /dev/null /etc/hypr/hyprland.conf
cat > /etc/hypr/hyprland.conf << 'HYPR'
$mod = SUPER
$term = foot
$menu = wofi --show drun

monitor = , preferred, auto, 1
exec-once = waybar
exec-once = mako

input {
	kb_layout = us
	follow_mouse = 1
}

general {
	gaps_in = 4
	gaps_out = 8
	border_size = 2
	layout = dwindle
}

decoration {
	rounding = 6
}

bind = $mod, Return, exec, $term
bind = $mod, D, exec, $menu
bind = $mod SHIFT, Q, killactive
bind = $mod SHIFT, X, exec, hyprlock
bind = $mod SHIFT, E, exit
bind = $mod, F, fullscreen
bind = $mod, V, togglefloating
bind = , Print, exec, grim -g "$(slurp)" - | wl-copy

bind = $mod, H, movefocus, l
bind = $mod, J, movefocus, d
bind = $mod, K, movefocus, u
bind = $mod, L, movefocus, r

bind = $mod, 1, workspace, 1
bind = $mod, 2, workspace, 2
bind = $mod, 3, workspace, 3
bind = $mod, 4, workspace, 4
bind = $mod, 5, workspace, 5
bind = $mod SHIFT, 1, movetoworkspace, 1
bind = $mod SHIFT, 2, movetoworkspace, 2
bind = $mod SHIFT, 3, movetoworkspace, 3
bind = $mod SHIFT, 4, movetoworkspace, 4
bind = $mod SHIFT, 5, movetoworkspace, 5

bindm = $mod, mouse:272, movewindow
bindm = $mod, mouse:273, resizewindow
HYPR

apt-get clean