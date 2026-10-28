#!/bin/bash
# Minimal Sway for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends sway swaybg swayidle swaylock \
	xwayland waybar wofi kitty mako-notifier grim slurp \
	xdg-desktop-portal-wlr xdg-desktop-portal-gtk libpam-systemd \
	fonts-recommended lightdm lightdm-gtk-greeter autotiling \
	pipewire pipewire-pulse wireplumber \
	brightnessctl playerctl \
	xdg-user-dirs-

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WALLPAPER_NAME="sway.png"
WALLPAPER_PATH="/usr/share/backgrounds/wallpaper.png"
install -Dm644 "$SCRIPT_DIR/$WALLPAPER_NAME" "$WALLPAPER_PATH"

install -D /dev/null /etc/sway/config
cat > /etc/sway/config << 'SWAY'
# sway config

### Variables
set $mod Mod4
set $smod Shift
set $term kitty
set $menu wofi --show drun
set $lock swaylock -f -c 000000
set $browser firefox-esr
set $explorer nemo

set $wallpaper /usr/share/backgrounds/wallpaper.png

set $ws1  " 1 "
set $ws2  " 2 "
set $ws3  " 3 "
set $ws4  " 4 "
set $ws5  " 5 "
set $ws6  " 6 "
set $ws7  " 7 "
set $ws8  " 8 "
set $ws9  " 9 "
set $ws10 " 10 "

### Wallpaper
output * bg $wallpaper fill

### Executions
exec dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP=sway
exec mako
exec autotiling
exec xdg-user-dirs-update

bar {
	swaybar_command waybar
}

### Idle configuration
# Lock after 10 minutes idle
# Monitor off after 15 min idle, back on at the first input
exec swayidle -w \
    timeout 600 '$lock' \
    900 'swaymsg "output * power off"' \
        resume 'swaymsg "output * power on"' \
    before-sleep '$lock'

### Input configuration
input type:touchpad {
    dwt enabled
    tap enabled
    natural_scroll enabled
    middle_emulation enabled
}

input type:keyboard {
    xkb_numlock enabled
    repeat_delay 300
    repeat_rate 50
}

### Appearance
font pango:monospace 12

### Key bindings

# Basic
bindsym $mod+d exec $menu
bindsym $mod+Return exec $term
bindsym $mod+e exec $explorer
bindsym $mod+b exec $browser
floating_modifier $mod normal
bindsym $mod+$smod+q kill
bindsym $mod+$smod+l exec $lock
bindsym $mod+$smod+e exec swaynag -t warning -m 'You pressed the exit shortcut. Do you really want to exit sway? This will end your Wayland session.' -B 'Yes, exit sway' 'swaymsg exit'
bindsym $mod+$smod+c reload

# Switch to workspace n
bindsym $mod+1 workspace $ws1
bindsym $mod+2 workspace $ws2
bindsym $mod+3 workspace $ws3
bindsym $mod+4 workspace $ws4
bindsym $mod+5 workspace $ws5
bindsym $mod+6 workspace $ws6
bindsym $mod+7 workspace $ws7
bindsym $mod+8 workspace $ws8
bindsym $mod+9 workspace $ws9
bindsym $mod+0 workspace $ws10

# Move focused container to workspace n
bindsym $mod+$smod+1 move container to workspace $ws1
bindsym $mod+$smod+2 move container to workspace $ws2
bindsym $mod+$smod+3 move container to workspace $ws3
bindsym $mod+$smod+4 move container to workspace $ws4
bindsym $mod+$smod+5 move container to workspace $ws5
bindsym $mod+$smod+6 move container to workspace $ws6
bindsym $mod+$smod+7 move container to workspace $ws7
bindsym $mod+$smod+8 move container to workspace $ws8
bindsym $mod+$smod+9 move container to workspace $ws9
bindsym $mod+$smod+0 move container to workspace $ws10

# Navigate focus
bindsym $mod+left focus left
bindsym $mod+down focus down
bindsym $mod+up focus up
bindsym $mod+right focus right

# Move focused
bindsym $mod+Shift+left move left
bindsym $mod+Shift+down move down
bindsym $mod+Shift+up move up
bindsym $mod+Shift+right move right

# Scratchpad
bindsym $mod+$smod+minus move scratchpad
bindsym $mod+minus scratchpad show

# Layout
bindsym $mod+h split h
bindsym $mod+v split v
bindsym $mod+f fullscreen toggle
bindsym $mod+$smod+space floating toggle

# Resizing
mode "resize" {
    bindsym Left resize shrink width 10px
    bindsym Down resize grow height 10px
    bindsym Up resize shrink height 10px
    bindsym Right resize grow width 10px

    bindsym Return mode "default"
    bindsym Escape mode "default"
}
bindsym $mod+r mode "resize"

# Audio via PulseAudio
bindsym --locked XF86AudioMute exec pactl set-sink-mute \@DEFAULT_SINK@ toggle
bindsym --locked XF86AudioLowerVolume exec pactl set-sink-volume \@DEFAULT_SINK@ -5%
bindsym --locked XF86AudioRaiseVolume exec pactl set-sink-volume \@DEFAULT_SINK@ +5%
bindsym --locked XF86AudioMicMute exec pactl set-source-mute \@DEFAULT_SOURCE@ toggle

# Media via playerctl
bindsym --locked XF86AudioPlay exec playerctl play-pause
bindsym --locked XF86AudioPause exec playerctl play-pause
bindsym --locked XF86AudioPrev exec playerctl previous
bindsym --locked XF86AudioNext exec playerctl next
bindsym --locked XF86AudioStop exec playerctl stop

# Brightness via brightnessctl
bindsym --locked XF86MonBrightnessDown exec brightnessctl set 5%-
bindsym --locked XF86MonBrightnessUp exec brightnessctl set 5%+

# Screenshot via grim
bindsym Print exec grim -g \"\$(slurp)\" $HOME/Pictures/\$(date +%F_%T).png

SWAY

apt-get clean