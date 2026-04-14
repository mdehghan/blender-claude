"""
Blender MCP Server (Thread-safe, JSON-RPC 2.0)
===============================================
Run this inside Blender's Scripting tab and press Run Script.

IMPORTANT: All bpy calls MUST run on Blender's main thread.
This script uses a queue + bpy.app.timers to safely hand work
from the socket thread to the main thread, preventing the
segfault caused by calling bpy from background threads.
"""

import bpy
import socket
import threading
import json
import traceback
import sys
import io
import queue
import time

HOST = "127.0.0.1"
PORT = 9876

# ── Thread-safe work queue ─────────────────────────────────────
# Socket threads put (request_dict, result_queue) here.
# The main thread timer picks them up and executes bpy code.
_work_queue = queue.Queue()

# ── JSON-RPC helpers ───────────────────────────────────────────

def ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

# ── Tool definitions ───────────────────────────────────────────

TOOLS = [
    {
        "name": "execute",
        "description": (
            "Execute arbitrary Python code inside Blender on the main thread. "
            "bpy, bpy.context (C), bpy.data (D), and bpy.ops (ops) are pre-imported. "
            "Returns stdout output from the executed code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to run inside Blender."}
            },
            "required": ["code"]
        }
    },
    {
        "name": "get_scene_info",
        "description": "Return a JSON summary of the current Blender scene: name, frame, and all objects.",
        "inputSchema": {"type": "object", "properties": {}}
    }
]

# ── Tool implementations (called on main thread) ───────────────

def tool_execute(params):
    code = params.get("code", "")
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        exec(code, {"bpy": bpy, "C": bpy.context, "D": bpy.data, "ops": bpy.ops})
        output = buf.getvalue() or "(no output)"
        return ok(None, {"content": [{"type": "text", "text": output}]})
    except Exception:
        return ok(None, {"content": [{"type": "text", "text": traceback.format_exc()}], "isError": True})
    finally:
        sys.stdout = old


def tool_get_scene_info(params):
    scene = bpy.context.scene
    info = {
        "scene_name": scene.name,
        "frame_current": scene.frame_current,
        "objects": [
            {"name": o.name, "type": o.type, "location": list(o.location)}
            for o in scene.objects
        ]
    }
    return ok(None, {"content": [{"type": "text", "text": json.dumps(info, indent=2)}]})


TOOL_HANDLERS = {
    "execute": tool_execute,
    "get_scene_info": tool_get_scene_info,
}

# ── Dispatch (non-bpy methods handled inline; bpy methods queued) ──

def dispatch_immediate(request):
    """Handle methods that don't touch bpy — safe to run on any thread."""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params") or {}

    if method == "initialize":
        return ok(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "blender-mcp", "version": "1.0.0"}
        })
    if method == "notifications/initialized":
        return None  # notification, no response
    if method == "ping":
        return ok(req_id, {})
    if method == "tools/list":
        return ok(req_id, {"tools": TOOLS})
    return None  # needs main thread


def dispatch_on_main_thread(request):
    """Handle methods that require bpy — must run on main thread."""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params") or {}

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return err(req_id, -32601, f"Unknown tool: '{tool_name}'")
        try:
            response = handler(tool_args)
            # Patch in the real req_id
            response["id"] = req_id
            return response
        except Exception:
            return err(req_id, -32603, traceback.format_exc())

    return err(req_id, -32601, f"Method not found: '{method}'")


# ── Main-thread timer: drains the work queue ───────────────────

def _main_thread_timer():
    """Called by Blender on the main thread every 0.05s."""
    try:
        while True:
            request, result_queue = _work_queue.get_nowait()
            response = dispatch_on_main_thread(request)
            result_queue.put(response)
    except queue.Empty:
        pass
    return 0.05  # reschedule


# ── Per-client handler (runs on socket thread) ─────────────────

def handle_client(conn, addr):
    print(f"[MCP] Client connected: {addr}")
    buf = ""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError as e:
                    conn.sendall((json.dumps(err(None, -32700, str(e))) + "\n").encode())
                    continue

                # Try immediate (non-bpy) dispatch first
                response = dispatch_immediate(request)

                if response is None and request.get("method") != "notifications/initialized":
                    # Needs main thread — queue it and wait
                    result_queue = queue.Queue()
                    _work_queue.put((request, result_queue))
                    try:
                        response = result_queue.get(timeout=10.0)
                    except queue.Empty:
                        response = err(request.get("id"), -32603, "Timeout: main thread did not respond")

                if response is not None:
                    conn.sendall((json.dumps(response) + "\n").encode())

    except Exception as e:
        print(f"[MCP] Connection error: {e}")
    finally:
        conn.close()
        print(f"[MCP] Client disconnected: {addr}")


# ── Server loop ────────────────────────────────────────────────

def server_loop(server_sock):
    print(f"[MCP] Listening on {HOST}:{PORT}")
    while True:
        try:
            conn, addr = server_sock.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except OSError:
            break
    print("[MCP] Server stopped.")


# ── Start / stop ───────────────────────────────────────────────

_server_socket = None
_server_thread = None

def start_server():
    global _server_socket, _server_thread
    if _server_socket is not None:
        print("[MCP] Already running.")
        return

    _server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server_socket.bind((HOST, PORT))
    _server_socket.listen(5)

    _server_thread = threading.Thread(target=server_loop, args=(_server_socket,), daemon=True)
    _server_thread.start()

    # Register the main-thread timer (safe bpy execution)
    if not bpy.app.timers.is_registered(_main_thread_timer):
        bpy.app.timers.register(_main_thread_timer, persistent=True)

    print(f"[MCP] ✅ Blender MCP server started on {HOST}:{PORT}")


def stop_server():
    global _server_socket, _server_thread
    if bpy.app.timers.is_registered(_main_thread_timer):
        bpy.app.timers.unregister(_main_thread_timer)
    if _server_socket:
        _server_socket.close()
        _server_socket = None
        _server_thread = None
        print("[MCP] Server stopped.")
    else:
        print("[MCP] Not running.")


start_server()
