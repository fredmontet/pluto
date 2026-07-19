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

if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Installing dependencies with uv"
uv sync --extra hardware

echo
echo "Done. Try it with:"
echo "  uv run pluto"
echo
echo "To run on boot:"
echo "  sudo cp pluto.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now pluto"
