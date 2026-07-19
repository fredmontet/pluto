#!/usr/bin/env bash
# Set up Pluto on a Raspberry Pi (tested on Raspberry Pi OS Bookworm, Pi Zero 2 W).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Enabling I2C and SPI"
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0

# The Enviro+ MEMS microphone needs this overlay; without it the noise
# page is simply skipped.
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
if ! grep -q "adau7002-simple" "$CONFIG"; then
    echo "==> Enabling the Enviro+ microphone overlay in $CONFIG"
    echo "dtoverlay=adau7002-simple" | sudo tee -a "$CONFIG" >/dev/null
    echo "    (a reboot is needed for the microphone to appear)"
fi

echo "==> Creating virtualenv and installing dependencies"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo
echo "Done. Try it with:"
echo "  .venv/bin/python -m pluto"
echo
echo "To run on boot:"
echo "  sudo cp pluto.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now pluto"
