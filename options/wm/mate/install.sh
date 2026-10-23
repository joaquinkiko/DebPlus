#!/bin/bash
# Minimal MATE for DebPlus
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get install -y --no-install-recommends mate-core lightdm lightdm-gtk-greeter
apt-get clean