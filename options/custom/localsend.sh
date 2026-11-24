#!/bin/sh
# LocalSend flatpak installer
set -e
export DEBIAN_FRONTEND=noninteractive

flatpak install --system --noninteractive flathub org.localsend.localsend_app
