#!/bin/bash
# Minimal i3 (X11) for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends xserver-xorg xinit i3-wm i3status \
	i3lock xss-lock rofi dunst picom alacritty feh maim \
	libpam-systemd fonts-recommended lightdm lightdm-gtk-greeter \
	pipewire pipewire-pulse wireplumber \
	brightnessctl playerctl \
	xdg-user-dirs-gtk

install -D /dev/null /etc/i3/config
cat > /etc/i3/config << 'I3'
# i3 Config

### Variables
set $mod Mod4
set $smod Shift
set $term alacritty
set $menu rofi -show drun
set $lock i3lock -n -c 000000
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
exec --no-startup-id feh --bg-fill $wallpaper

### Executions
exec --no-startup-id picom -b
exec --no-startup-id xss-lock --transfer-sleep-lock -- $lock
exec --no-startup-id xdg-user-dirs-update

bar {
	status_command i3status
}

### Appearance
font pango:monospace 12

### Key bindings

bindsym $mod+d exec $menu
bindsym $mod+Return exec $term
bindsym $mod+e exec $explorer
bindsym $mod+b exec $browser
floating_modifier $mod normal
bindsym $mod+$smod+q kill
bindsym $mod+$smod+l exec $lock
bindsym $mod+$smod+e exec i3-nagbar -t warning -m 'You pressed the exit shortcut. Do you really want to exit i3? This will end your X session.' -B 'Yes, exit i3' 'i3-msg exit'
bindsym $mod+$smod+c reload
bindsym $mod+$smod+r restart

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

# Screenshot via maim
bindsym Print exec --no-startup-id maim -s $HOME/Pictures/$(date +%F_%T).png

I3

apt-get clean