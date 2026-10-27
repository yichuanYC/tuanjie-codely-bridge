# tuanjie-codely-bridge

A small, dependency-free Python client for the **Codely TCP bridge** that ships inside
团结引擎 (Tuanjie) — the `cn.tuanjie.codely.bridge` package — so any agent or script can
drive a *running* Tuanjie editor: scenes, GameObjects, component properties, screenshots,
console, and arbitrary C# inside the editor process.

No extra Unity package, no project changes: if the editor is open, the bridge is already
listening.

```python
from tuanjie_bridge import Bridge, find_port

port = find_port(r"D:\TuanjieProjects\ShinanoViewer")   # reads <project>/Temp/.com-unity-codely.json
b = Bridge(port).connect()
try:
    print(b.call("list_tools"))
    b.call("manage_gameobject", {"action": "create", "name": "Player"})
finally:
    b.close()
```

Command line:

```bash
python tuanjie_bridge.py --project D:\TuanjieProjects\ShinanoViewer list_tools
python tuanjie_bridge.py --project D:\TuanjieProjects\ShinanoViewer call manage_scene --params '{"action":"get_hierarchy"}'
python tuanjie_bridge.py --project D:\TuanjieProjects\ShinanoViewer call execute_csharp_script --params '{"script":"return Application.unityVersion;"}'
```

## Protocol

See [PROTOCOL.md](PROTOCOL.md). Short version: a plain-text `WELCOME` line, then
`uint64` big-endian length-prefixed UTF-8 frames; the client must introduce itself with a
`CLIENT_VERSION=` frame when the server announces `SERVER_VERSION>=2`.

## What it can do

Everything the bridge exposes — 24 tool types, 100+ actions, on Tuanjie 2022.3:

| Area | Tools |
| --- | --- |
| Scene | `manage_scene` (load / save / create / hierarchy) |
| Objects | `manage_gameobject` (create / delete / modify / find / components) |
| Assets | `manage_asset`, `manage_guid`, `manage_package` |
| Editor | `manage_editor` (play / pause / refresh / compile / state) |
| Visuals | `manage_screenshot`, `manage_gameview`, `manage_shader` |
| Runtime | `execute_csharp_script`, `exec_editor_script`, `exec_runtime_script` |
| Input | `manage_input` (virtual mouse / keyboard) |
| Misc | `read_console`, `execute_menu_item`, `manage_bake`, `manage_job` |

Responses are JSON: `{"success": bool, "message": str, "data": ...}`.

## Why this exists

The official route (`unity` CLI + `com.unity.pipeline`) requires **Unity 6**; 团结引擎 is a
2022.3 fork and cannot run it. Tuanjie ships its own bridge with the editor — this client
speaks it, so agent tooling works on Tuanjie without installing anything into the project.

## Notes

- Attaches only to a **running** editor. It cannot launch one.
- `manage_screenshot` writes PNGs relative to the project root; read `data.path` for the
  real location.
- The bridge drops a document silently when a parameter has the wrong JSON type and still
  replies `success: true`. This client passes raw JSON (no `--flag value` round-trip), which
  removes the usual string-coercion traps, but **verify writes**: read the object back.
- Writes are refused while the editor is in Play mode.

## Test

```bash
python tests/selftest.py     # runs against an in-process fake bridge, no editor needed
```

## License

MIT. Not affiliated with Unity China / Tuanjie. `cn.tuanjie.codely.bridge` is their
package; this repository only implements the client side of its wire protocol.
