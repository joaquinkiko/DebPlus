#!/bin/sh
# Heroic flatpak installer
set -e
export DEBIAN_FRONTEND=noninteractive

flatpak install --system --noninteractive flathub com.heroicgameslauncher.hgl
