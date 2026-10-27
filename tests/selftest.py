"""Self-test: exercises the client against an in-process fake bridge.

No editor required. Run: python tests/selftest.py
"""
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tuanjie_bridge import Bridge  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print("[%s] %s%s" % (status, label, "" if condition else " -> %s" % detail))
    if not condition:
        FAILURES.append(label)


def recv_exact(conn, n, buf):
    while len(buf[0]) < n:
        chunk = conn.recv(4096)
        if not chunk:
            raise RuntimeError("peer closed")
        buf[0] += chunk
    out, buf[0] = buf[0][:n], buf[0][n:]
    return out


def send_frame(conn, payload):
    conn.sendall(struct.pack(">Q", len(payload)) + payload)


def serve(srv, seen):
    conn, _ = srv.accept()
    conn.sendall(
        b"WELCOME UNITY-TCP 1 FRAMING=1 SERVER_VERSION=3 "
        b"PROJECT_ROOT=D%3A%2FTuanjieProjects%2FShinanoViewer\n"
    )
    buf = [b""]
    (n,) = struct.unpack(">Q", recv_exact(conn, 8, buf))
    seen.append(("hello", recv_exact(conn, n, buf).decode()))
    while True:
        try:
            header = recv_exact(conn, 8, buf)
        except Exception:
            break
        (n,) = struct.unpack(">Q", header)
        payload = recv_exact(conn, n, buf)
        if payload == b"ping":
            send_frame(conn, b'{"success":true,"message":"pong"}')
            continue
        msg = json.loads(payload)
        seen.append(("cmd", msg["type"], msg.get("params")))
        # Emit an unsolicited event and a stale reply first: a correct client must not
        # mistake either for the answer to this command.
        send_frame(conn, b'{"event":"state_changed","scene_dirty":true}')
        send_frame(conn, b'{"request_id":"stale","success":true,"message":"old","data":{"echo":"stale"}}')
        send_frame(conn, json.dumps({
            "request_id": msg["request_id"],
            "success": True,
            "message": "Command executed successfully",
            "data": {"echo": msg["type"], "params": msg.get("params")},
        }).encode())
    conn.close()


def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    seen = []
    threading.Thread(target=serve, args=(srv, seen), daemon=True).start()
    time.sleep(0.2)

    bridge = Bridge(port, timeout=10).connect()
    check("handshake server_version", bridge.server_version == 3, bridge.server_version)
    check(
        "project root decoded",
        bridge.project_root == "D:/TuanjieProjects/ShinanoViewer",
        bridge.project_root,
    )

    pong = bridge.ping()
    check("ping consumed its pong", "pong" in pong, pong)

    resp = bridge.call("manage_scene", {"action": "get_hierarchy"})
    check("stale reply skipped, real one matched", resp["data"]["echo"] == "manage_scene", resp)
    check("params round-tripped", resp["data"]["params"] == {"action": "get_hierarchy"})
    check(
        "CLIENT_VERSION frame sent first",
        seen[0] == ("hello", "CLIENT_VERSION=1.0.87"),
        seen[0],
    )
    check(
        "unsolicited frames buffered, not returned",
        [e.get("event") for e in bridge.events] == ["state_changed", None],
        bridge.events,
    )

    bridge.close()
    srv.close()

    print("\n%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
