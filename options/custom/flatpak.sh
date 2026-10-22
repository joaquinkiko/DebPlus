#!/bin/bash
# Install and setup flatpak
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

