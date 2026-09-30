#!/usr/bin/env bash
# One-time setup for Pi Buddy on Raspberry Pi OS (64-bit, desktop).
# Run from the repo folder:  bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev libportaudio2 portaudio19-dev libsdl2-2.0-0 \
    libsdl2-ttf-2.0-0 alsa-utils git

echo "==> Creating Python virtual environment (.venv)"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

echo "==> Installing openWakeWord (no-deps: its tflite dependency has no wheel for newer Python)"
pip install --no-deps "openwakeword>=0.6.0" || echo "!! openWakeWord failed to install — tap-to-talk will still work"
python - <<'EOF' || echo "!! Wake word model download failed — will retry on first run"
import openwakeword.utils as u
u.download_models(["hey_jarvis"])
print("wake word models ready")
EOF

echo "==> Downloading Piper voice"
mkdir -p voices
python -m piper.download_voices en_US-joe-medium --data-dir voices

echo "==> Pre-downloading Whisper speech model"
python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

echo "==> Config files"
[ -f config.toml ] || cp config.example.toml config.toml
if [ ! -f .env ]; then
    read -r -p "Paste your Anthropic API key (starts with sk-ant-): " KEY
    echo "ANTHROPIC_API_KEY=$KEY" > .env
    chmod 600 .env
fi

echo "==> Start automatically when the desktop loads"
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/pi-buddy.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Pi Buddy
Exec=$(pwd)/run.sh
X-GNOME-Autostart-enabled=true
EOF
chmod +x run.sh

echo
echo "Done! Test it now with:  ./run.sh"
echo "Tap the screen or say 'hey jarvis'. Press-and-hold 5 seconds (or Esc) to quit."
