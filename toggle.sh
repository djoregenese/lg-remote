#!/bin/bash
# Toggle LG TV Remote on/off with debounce.
STAMP="/tmp/.lg-remote-gui.stamp"

EXISTING=$(pgrep -f "[l]g_remote_gui.py")

if [ -n "$EXISTING" ]; then
    # Don't kill if launched less than 2 seconds ago (debounce)
    if [ -f "$STAMP" ]; then
        LAUNCHED=$(cat "$STAMP")
        NOW=$(date +%s)
        DIFF=$((NOW - LAUNCHED))
        if [ "$DIFF" -lt 2 ]; then
            exit 0
        fi
    fi
    kill -9 $EXISTING 2>/dev/null
    rm -f "$STAMP"
else
    /Users/dev/.asdf/installs/python/3.12.7/bin/python3 /Users/dev/Documents/projects/lg-remote/lg_remote_gui.py &
    date +%s > "$STAMP"
fi
