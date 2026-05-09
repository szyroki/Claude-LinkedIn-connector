#!/bin/bash
# Launcher for LinkedIn MCP server
# Logs to a file so we can debug startup issues

LOG="$HOME/Documents/Claude/Projects/LinkedIn connector/server.log"
SCRIPT="$HOME/Documents/Claude/Projects/LinkedIn connector/server.py"

echo "[$(date)] Starting LinkedIn MCP server" >> "$LOG"

# Find a Python 3 that has the mcp package
for PY in python3 /usr/bin/python3 /usr/local/bin/python3 "$HOME/.local/bin/python3" /opt/homebrew/bin/python3; do
    if command -v "$PY" &>/dev/null 2>&1 || [ -f "$PY" ]; then
        if "$PY" -c "import mcp, requests" &>/dev/null 2>&1; then
            echo "[$(date)] Using Python: $PY ($($PY --version 2>&1))" >> "$LOG"
            exec "$PY" "$SCRIPT" 2>> "$LOG"
        fi
    fi
done

# If no suitable Python found, try installing deps with whatever python3 we have
echo "[$(date)] No Python with mcp found. Trying to install..." >> "$LOG"
python3 -m pip install mcp requests --break-system-packages >> "$LOG" 2>&1
exec python3 "$SCRIPT" 2>> "$LOG"
