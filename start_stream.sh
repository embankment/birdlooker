#!/usr/bin/env bash
# Start pi-webrtc with the settings known to work on the Pi 4.
#
# Run this in one terminal, then pan_demo.py in another.
# Ctrl-C stops the stream cleanly.

set -u

PI_WEBRTC="${PI_WEBRTC:-pi-webrtc}"
WIDTH="${WIDTH:-1600}"       # must be a multiple of 64 (stride padding)
HEIGHT="${HEIGHT:-1200}"
FPS="${FPS:-30}"
PORT="${PORT:-8080}"
ROTATION="${ROTATION:-180}"
UID_NAME="${UID_NAME:-birdlooker}"

if ! command -v "$PI_WEBRTC" >/dev/null 2>&1 && [ ! -x "$PI_WEBRTC" ]; then
    echo "pi-webrtc not found. Set PI_WEBRTC to its path:" >&2
    echo "    PI_WEBRTC=/path/to/pi-webrtc $0" >&2
    exit 1
fi

# The Pi 5 has no hardware H.264 encoder; the Pi 4 does. --hw-accel is
# deliberately absent here because software encoding is what we measured
# at ~94% across four cores on the Pi 4 at 1600x1200/30.

IP=$(hostname -I | awk '{print $1}')
echo "Stream will be at http://${IP}:${PORT}"
echo "Player: https://tzuhuantai.github.io/webrtc-player/demo/  (adapter: WHEP)"
echo "Enter the URL above with no path. Chrome must be allowed local"
echo "network access; other browsers block it as mixed content."
echo

exec "$PI_WEBRTC" \
    --camera=libcamera:0 \
    --uid="$UID_NAME" \
    --fps="$FPS" \
    --width="$WIDTH" \
    --height="$HEIGHT" \
    --rotation="$ROTATION" \
    --use-whep \
    --whep-port="$PORT" \
    --no-audio
