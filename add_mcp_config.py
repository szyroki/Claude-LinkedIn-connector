#!/usr/bin/env python3
import json, sys, subprocess
from pathlib import Path

config_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
server_path = str(Path.home() / "Documents" / "Claude" / "Projects" / "LinkedIn connector" / "server.py")

# Find the Python that actually has mcp installed
python_exe = sys.executable
try:
    subprocess.run([python_exe, "-c", "import mcp, requests"], check=True, capture_output=True)
    print(f"✓ mcp found in: {python_exe}")
except subprocess.CalledProcessError:
    print(f"✗ mcp NOT found in {python_exe}")
    sys.exit(1)

config_path.parent.mkdir(parents=True, exist_ok=True)
config = json.loads(config_path.read_text()) if config_path.exists() else {}
config.setdefault("mcpServers", {})
config["mcpServers"]["linkedin"] = {
    "command": python_exe,
    "args": [server_path]
}
config_path.write_text(json.dumps(config, indent=2))

print(f"✓ Config written")
print(f"  python:  {python_exe}")
print(f"  script:  {server_path}")
input("Press Enter to close...")
