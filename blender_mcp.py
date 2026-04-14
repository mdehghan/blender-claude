#!/usr/bin/env python3
"""
blender_mcp_bridge.py
---------------------
MCP stdio bridge: relays JSON-RPC messages between Claude Desktop
(which speaks MCP over stdin/stdout) and the Blender TCP server
running on localhost:9876.

Place this file somewhere permanent, e.g.:
  ~/blender_mcp/blender_mcp_bridge.py

Then in claude_desktop_config.json use:
  {
    "mcpServers": {
      "blender": {
        "command": "python3",
        "args": ["/absolute/path/to/blender_mcp_bridge.py"]
      }
    }
  }

On Windows, use "python" instead of "python3".
"""

import socket
import sys
import threading

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876


def blender_to_stdout(sock: socket.socket):
    """Forward Blender responses → Claude (stdout)."""
    buffer = ""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    sys.stdout.write(line + "\n")
                    sys.stdout.flush()
    except OSError:
        pass


def stdin_to_blender(sock: socket.socket):
    """Forward Claude messages (stdin) → Blender."""
    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                sock.sendall((line + "\n").encode("utf-8"))
    except OSError:
        pass


def main():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((BLENDER_HOST, BLENDER_PORT))
    except ConnectionRefusedError:
        sys.stderr.write(
            f"[bridge] Could not connect to Blender on {BLENDER_HOST}:{BLENDER_PORT}.\n"
            "Make sure the Blender MCP server script is running first.\n"
        )
        sys.exit(1)

    # Run the Blender → stdout direction in a background thread
    t = threading.Thread(target=blender_to_stdout, args=(sock,), daemon=True)
    t.start()

    # Run stdin → Blender in the main thread
    stdin_to_blender(sock)

    sock.close()


if __name__ == "__main__":
    main()
