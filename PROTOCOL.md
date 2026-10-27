# Codely bridge wire protocol (`FRAMING=1`)

Observed against `cn.tuanjie.codely.bridge@1.0.87` on Tuanjie 2022.3.62t17 (Windows).
Everything below is derived from watching the traffic and from the bundled native
`NativeTcpBridge.dll` / C# layer — it is not an official specification.

## 1. Discovery

The editor writes a registry file, rewritten on state changes:

| Bridge version | Path |
| --- | --- |
| ≥ 1.0.86 | `<project>/Temp/.com-unity-codely.json` |
| older | `<project>/.com-unity-codely.json` |

```json
{
  "unity_port": 1147,
  "project_path": "D:/TuanjieProjects/ShinanoViewer/Assets",
  "reloading": false,
  "reason": "ready",
  "last_updated": "2026-10-27T14:35:52.605Z"
}
```

The port is **not fixed** and not guaranteed to be in any range (plain 1147 was observed;
other builds start at 25916 and probe upward). Read the file; do not guess.

## 2. Handshake

On connect the server sends one ASCII line terminated by `\n`:

```
WELCOME UNITY-TCP 1 FRAMING=1 SERVER_VERSION=3 PROJECT_ROOT=D%3A%2FTuanjieProjects%2FShinanoViewer
```

- `WELCOME UNITY-TCP` + `FRAMING=1` identify the dialect. (`CHANGELOG.md` line ~1409 notes a
  historical `WELCOME Codely-Bridge 1 FRAMING=1` spelling.)
- `SERVER_VERSION` — when `>= 2` the client **must** send its version as a frame before any
  command:

  ```
  payload: CLIENT_VERSION=1.0.87
  ```

  Skip it and the server stays silent, then closes. This is the single most common reason a
  hand-rolled client "connects but nothing works".
- `PROJECT_ROOT` is URI-encoded (`%3A` = `:`).

## 3. Framing

Every message after the welcome line is:

```
uint64 (big-endian) payload length | payload bytes (UTF-8)
```

No delimiter, no padding. `ping` is a payload of the four bytes `ping` (not JSON) and is
answered with a JSON `pong` frame.

## 4. Requests

```json
{"type": "manage_scene", "params": {"action": "get_hierarchy"}, "request_id": "r1"}
```

- `type` — tool name; `list_tools` returns the full catalogue plus `custom_tools`.
- `params` — tool-specific; almost every tool takes an `action`.
- `request_id` — echoed back. Responses can interleave with unsolicited event frames, so
  match on this field rather than assuming one-request-one-response.

Equivalent spellings seen in response envelopes: `request_id` / `requestId` /
`meta.request_id` — accept all three.

## 5. Responses

```json
{"success": true, "message": "Command executed successfully", "data": {...}, "request_id": "r1"}
```

Tool-level failures usually arrive as `success: false` **inside** `data` while the envelope
still says `success: true`, e.g.:

```json
{"success": true, "message": "Command executed successfully",
 "data": {"success": false, "message": "C# script execution failed: ..."}}
```

Callers must inspect `data.success`, not just the envelope.

## 6. Traps

- **Silently dropped parameters.** A parameter whose JSON type does not match what the C#
  tool deserializes (a string where `JArray` is expected, for instance) is skipped while the
  reply still says success. Always read the object back to confirm a write.
- **Play mode guard.** `WriteGuard.CheckCommandWriteAllowed(type, action)` rejects mutating
  commands while the editor is playing.
- **Screenshots.** `manage_screenshot` joins `path` (a directory) and `filename`, and
  appends `.png` unconditionally; `data.path` is authoritative.
- **Binary payloads** are wrapped as `{"bytes": [...], "subtype": "..."}` in the native
  layer — not needed for JSON tools.
- Max frame is 64 MiB (`MaxFrameBytes`), I/O timeout ~3 s in the native bridge.

## 7. Minimal client

```python
import json, socket, struct, urllib.parse

sock = socket.create_connection(("127.0.0.1", 1147), timeout=10)
line = b""
while not line.endswith(b"\n"):
    line += sock.recv(1)
version = int([p for p in line.split() if p.startswith(b"SERVER_VERSION=")][0].split(b"=")[1])

def send(payload: bytes):
    sock.sendall(struct.pack(">Q", len(payload)) + payload)

def recv():
    (n,) = struct.unpack(">Q", sock.recv(8, socket.MSG_WAITALL))
    return sock.recv(n, socket.MSG_WAITALL)

if version >= 2:
    send(b"CLIENT_VERSION=1.0.87")
send(json.dumps({"type": "list_tools", "params": {}, "request_id": "r1"}).encode())
print(json.loads(recv()))
```

Production notes: read the length prefix with a loop (`recv` can return short), drain the
`pong` frame after `ping`, and time out per request.
