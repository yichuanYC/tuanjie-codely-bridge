#!/usr/bin/env python3
"""Tuanjie / Unity Codely bridge client (FRAMING=1 protocol).

Protocol (reverse engineered from codely.exe):
  1. Server sends a text line: "WELCOME UNITY-TCP 1 FRAMING=1 SERVER_VERSION=3 PROJECT_ROOT=..."
  2. Every message after that is a frame: uint64 big-endian length prefix + UTF-8 payload.
  3. If SERVER_VERSION >= 2 the client first sends CLIENT_VERSION=<ver> as a frame.
  4. Command frame payload: {"type": <cmd>, "params": {...}, "request_id": <id>}
  5. "ping" is sent as a raw frame payload (no JSON) and answered by the server.

Usage:
  python tuanjie_bridge.py list_tools
  python tuanjie_bridge.py call manage_scene --params '{"action":"get_hierarchy"}'
  python tuanjie_bridge.py call execute_csharp_script --params '{"code":"return 1+1;"}'
  python tuanjie_bridge.py raw '{"type":"list_tools","params":{}}'
"""
import argparse
import json
import os
import socket
import struct
import sys
import time
import urllib.parse

DEFAULT_PORT = 25916
PORT_SCAN = 40
REGISTRY = ".com-unity-codely.json"


def find_port(project_path=None, explicit=None):
    if explicit:
        return explicit
    roots = []
    if project_path:
        roots.append(os.path.join(project_path, "Temp"))
    roots.append(os.path.join(os.getcwd(), "Temp"))
    for root in roots:
        f = os.path.join(root, REGISTRY)
        if os.path.isfile(f):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                port = data.get("unity_port")
                if port:
                    return int(port)
            except Exception:
                pass
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN):
        s = socket.socket()
        s.settimeout(0.15)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
    return DEFAULT_PORT


class Bridge:
    def __init__(self, port, host="127.0.0.1", timeout=120.0, client_version="1.0.87", debug=False):
        self.port = port
        self.host = host
        self.timeout = timeout
        self.client_version = client_version
        self.debug = debug
        self.sock = None
        self.server_version = 1
        self.project_root = None
        self._seq = 0
        self._buf = b""
        self.events = []

    # ---------- transport ----------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=20.0)
        self.sock.settimeout(self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        line = self._read_line()
        if "WELCOME" not in line or "FRAMING=1" not in line:
            raise RuntimeError("unexpected handshake: %r" % line)
        parts = line.split()
        for p in parts:
            if p.startswith("SERVER_VERSION="):
                self.server_version = int(p.split("=", 1)[1])
            elif p.startswith("PROJECT_ROOT="):
                try:
                    self.project_root = urllib.parse.unquote(p.split("=", 1)[1])
                except Exception:
                    self.project_root = None
        if self.server_version >= 2:
            self._send_frame(("CLIENT_VERSION=%s" % self.client_version).encode("utf-8"))
        return self

    def _read_line(self):
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("socket closed during handshake")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("ascii", "replace").strip()

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise RuntimeError("socket closed mid-frame")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload):
        self.sock.sendall(struct.pack(">Q", len(payload)) + payload)

    def _recv_frame(self):
        header = self._recv_exact(8)
        (length,) = struct.unpack(">Q", header)
        if length > 64 * 1024 * 1024:
            raise RuntimeError("frame too large: %d" % length)
        return self._recv_exact(int(length))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    # ---------- protocol ----------
    def ping(self, timeout=5.0):
        """Send a ping frame and consume the matching pong frame."""
        self._send_frame(b"ping")
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            raw = self._recv_frame()
        finally:
            self.sock.settimeout(old)
        return raw.decode("utf-8", "replace")

    def send(self, cmd_type, params=None, request_id=None):
        if request_id is None:
            self._seq += 1
            request_id = "r%d" % self._seq
        body = json.dumps({"type": cmd_type, "params": params or {}, "request_id": request_id})
        self._send_frame(body.encode("utf-8"))
        return request_id

    def wait_for(self, request_id, timeout=None):
        """Read frames until the response carrying request_id shows up."""
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        self.sock.settimeout(max(0.5, deadline - time.time()))
        while time.time() < deadline:
            try:
                raw = self._recv_frame()
            except socket.timeout:
                raise TimeoutError("no response for %s within %ss" % (request_id, timeout or self.timeout))
            text = raw.decode("utf-8", "replace")
            try:
                msg = json.loads(text)
            except Exception:
                if self.debug:
                    print("[non-json frame] %r" % text[:400], file=sys.stderr)
                continue
            if self.debug:
                print("[frame] %s" % json.dumps(msg, ensure_ascii=False)[:800], file=sys.stderr)
            rid = msg.get("request_id") or msg.get("requestId") or (msg.get("meta") or {}).get("request_id")
            if rid is not None and str(rid) == str(request_id):
                return msg
            if rid is None and ("success" in msg or "error" in msg) and not msg.get("event"):
                return msg
            self.events.append(msg)
        raise TimeoutError("timed out waiting for %s" % request_id)

    def call(self, cmd_type, params=None, timeout=None):
        rid = self.send(cmd_type, params)
        return self.wait_for(rid, timeout=timeout)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Drive a running Tuanjie/Unity Codely bridge.")
    ap.add_argument("--port", type=int)
    ap.add_argument("--project", help="Unity project root (used to locate Temp/%s)" % REGISTRY)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--json-out", action="store_true", help="print compact JSON only")
    sub = ap.add_subparsers(dest="mode", required=True)

    sub.add_parser("ping")
    sub.add_parser("list_tools")
    p_call = sub.add_parser("call")
    p_call.add_argument("type")
    p_call.add_argument("--params", default="{}")
    p_raw = sub.add_parser("raw")
    p_raw.add_argument("payload")

    args = ap.parse_args(argv)
    port = find_port(args.project, args.port)
    bridge = Bridge(port, timeout=args.timeout, debug=args.debug).connect()

    try:
        if args.mode == "ping":
            bridge.ping()
            print("ping sent to %s:%d (server_version=%s, project=%s)"
                  % (bridge.host, bridge.port, bridge.server_version, bridge.project_root))
            return 0
        if args.mode == "list_tools":
            resp = bridge.call("list_tools")
        elif args.mode == "call":
            params = json.loads(args.params)
            resp = bridge.call(args.type, params)
        else:
            payload = args.payload
            rid = bridge.send(**json.loads(payload)) if payload.strip().startswith("{") else None
            resp = bridge.wait_for(rid)
        if args.json_out:
            print(json.dumps(resp, ensure_ascii=False))
        else:
            print(json.dumps(resp, ensure_ascii=False, indent=2))
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
