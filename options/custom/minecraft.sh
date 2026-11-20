#!/bin/sh
# Minecraft flatpak installer
set -e
export DEBIAN_FRONTEND=noninteractive

flatpak install --system --noninteractive flathub com.mojang.Minecraft
