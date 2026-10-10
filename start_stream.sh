#!/usr/bin/env bash
# Start pi-webrtc with the settings known to work on the Pi 4.
#
# Run this in one terminal, then pan_demo.py in another.
# Ctrl-C stops the stream cleanly.
#
# Override anything from the environment, e.g.
#     PI_WEBRTC=/elsewhere/pi-webrtc WIDTH=1280 HEIGHT=960 ./start_stream.sh

set -u

PI_WEBRTC="${PI_WEBRTC:-/home/squintr/pi-webrtc}"
WIDTH="${WIDTH:-1600}"       # must be a multiple of 64 (stride padding)
HEIGHT="${HEIGHT:-1200}"
FPS="${FPS:-30}"
PORT="${PORT:-8080}"
ROTATION="${ROTATION:-180}"
UID_NAME="${UID_NAME:-birdlooker}"

# Resolve PI_WEBRTC to an actual executable.
#
# A plain -x test is not enough: directories have the execute bit set
# (that is the traverse permission), so pointing this at the repo folder
# instead of the binary passes the test and then fails at exec time with
# a confusing error.
if [ -d "$PI_WEBRTC" ]; then
    echo "note: $PI_WEBRTC is a directory, looking for the binary inside" >&2
    found=""
    for c in "$PI_WEBRTC/pi-webrtc" \
             "$PI_WEBRTC/build/pi-webrtc" \
             "$PI_WEBRTC/out/pi-webrtc"; do
        if [ -f "$c" ] && [ -x "$c" ]; then
            found="$c"
            break
        fi
    done
    if [ -z "$found" ]; then
        found=$(find "$PI_WEBRTC" -maxdepth 3 -name pi-webrtc -type f \
                     -perm -u+x -print -quit 2>/dev/null)
    fi
    if [ -n "$found" ]; then
        echo "note: using $found" >&2
        PI_WEBRTC="$found"
    fi
fi

if ! command -v "$PI_WEBRTC" >/dev/null 2>&1; then
    if [ ! -e "$PI_WEBRTC" ]; then
        echo "pi-webrtc not found at: $PI_WEBRTC" >&2
        echo "Locate it with:  find \$HOME -name pi-webrtc -type f" >&2
    elif [ -d "$PI_WEBRTC" ]; then
        echo "$PI_WEBRTC is a directory and no pi-webrtc binary was" >&2
        echo "found inside it. Point PI_WEBRTC at the binary itself." >&2
    elif [ ! -x "$PI_WEBRTC" ]; then
        echo "$PI_WEBRTC exists but is not executable." >&2
        echo "Fix with:  chmod +x $PI_WEBRTC" >&2
    else
        echo "Could not run $PI_WEBRTC." >&2
    fi
    exit 1
fi

# The Pi 5 has no hardware H.264 encoder; the Pi 4 does. --hw-accel is
# deliberately absent here because software encoding is what we measured
# at ~94% across four cores on the Pi 4 at 1600x1200/30.

IP=$(hostname -I | awk '{print $1}')
echo "Binary:  $PI_WEBRTC"
echo "Stream:  http://${IP}:${PORT}"
echo "Player:  https://tzuhuantai.github.io/webrtc-player/demo/  (adapter: WHEP)"
echo
echo "Enter the stream URL above with no path. Chrome must be allowed"
echo "local network access; other browsers block it as mixed content."
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
