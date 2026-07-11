#!/usr/bin/env python3
"""WorldLoom — a local sci-fi & fantasy worldbuilding studio.

Zero-dependency server: Python 3.8+ standard library only.
Run:  python3 server.py            (then open http://127.0.0.1:8765)
      python3 server.py --port 9000 --host 0.0.0.0

Providers: Anthropic API, Ollama (local), any OpenAI-compatible endpoint,
and a built-in Demo provider for trying the UI without an LLM.
"""
import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
WORLDS_DIR = DATA_DIR / "worlds"
CONFIG_PATH = DATA_DIR / "config.json"

_IO_LOCK = threading.Lock()

CATEGORIES = [
    "overview", "geography", "history", "peoples", "cultures", "politics",
    "magic", "science", "religion", "economy", "language", "creatures",
    "locations", "characters", "hooks",
]

DEFAULT_CONFIG = {
    "provider": "anthropic",
    "max_tokens": 16000,
    "anthropic": {
        "api_key": "",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-4-8",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.1",
    },
    "openai": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "",
    },
}

SYSTEM_PROMPT = """You are Loom, a collaborative worldbuilding partner for science fiction and fantasy settings. You help the user iteratively invent a coherent world they can later set stories or games in.

How to work:
- Build on established canon below. Never contradict it; if the user asks for something that conflicts, point out the tension and offer ways to reconcile it.
- Be a brainstorming partner, not a lecturer. When the user is vague or unsure, offer 2-4 distinct, concrete directions and ask which resonates. Ask at most 2 focused questions per reply.
- Think about how elements connect: geography shapes trade, trade shapes politics, politics shapes war and religion, magic or technology warps all of it. Surface interesting consequences and conflicts the user may not have considered.
- Keep replies vivid but tight — usually under 400 words unless the user asks for depth.
- Cover, over time: geography & climate, deep history & timeline, peoples & species, cultures & daily life, politics & power structures, magic or power systems (with costs and limits), science & technology, religion & myth, economy & trade, languages & names, creatures & ecology, notable locations, key characters, and story hooks.

Recording canon:
When an idea has crystallized — the user reacted positively, refined it, or asked you to lock it in — propose it as canon using a fenced block, one JSON object per block:

```canon
{"category": "geography", "title": "The Shattered Coast", "content": "A 2-6 sentence encyclopedia-style entry written as established fact."}
```

Rules for canon blocks:
- category must be one of: overview, geography, history, peoples, cultures, politics, magic, science, religion, economy, language, creatures, locations, characters, hooks.
- Propose at most 3 canon blocks per reply, and only for ideas the user has embraced — not for fresh suggestions they haven't reacted to yet.
- Content is written in-world, as fact, with no meta commentary.
- Everything outside canon blocks is ordinary discussion."""

DEMO_REPLY = """The bones of this world are taking shape nicely. A few threads we could pull on:

1. **The tide-locked moon** — one hemisphere always faces the gas giant, bathed in amber light; the far side knows only stars. Cultures on each side would barely believe in each other.
2. **Salt-singers** — navigators who read ocean currents by taste and song, guild-bound and secretive.
3. **The Long Thaw** — a slow apocalypse in reverse: glaciers retreating after ten thousand years, uncovering cities nobody remembers building.

The Long Thaw pairs beautifully with your earlier idea about contested ruins — want me to develop who is racing to claim what the ice gives back?

```canon
{"category": "history", "title": "The Long Thaw", "content": "For ten millennia the world lay under the Great Ice, and civilization survived only along the equatorial belt. Three centuries ago the glaciers began an inexplicable retreat, uncovering pre-glacial cities of black stone each year. No living culture remembers who built them, and every power now races to claim what the ice surrenders."}
```

*(This is the built-in Demo provider — connect Anthropic, Ollama, or an OpenAI-compatible endpoint in Settings to brainstorm for real.)*"""


# ---------------------------------------------------------------- storage

def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_config():
    with _IO_LOCK:
        cfg = _read_json(CONFIG_PATH, {})
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, val in cfg.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def save_config(cfg):
    with _IO_LOCK:
        _write_json(CONFIG_PATH, cfg)
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def world_path(world_id):
    if not re.fullmatch(r"[a-f0-9]{32}", world_id):
        raise ValueError("bad world id")
    return WORLDS_DIR / (world_id + ".json")


def load_world(world_id):
    with _IO_LOCK:
        world = _read_json(world_path(world_id), None)
    if world is None:
        raise KeyError(world_id)
    return world


def save_world(world):
    world["updated"] = time.time()
    with _IO_LOCK:
        _write_json(world_path(world["id"]), world)


def list_worlds():
    WORLDS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in WORLDS_DIR.glob("*.json"):
        w = _read_json(p, None)
        if w:
            out.append({
                "id": w["id"], "name": w["name"],
                "updated": w.get("updated", 0),
                "entries": len(w.get("entries", [])),
            })
    out.sort(key=lambda w: -w["updated"])
    return out


# ---------------------------------------------------------------- prompt

def build_system_prompt(world):
    parts = [SYSTEM_PROMPT, "\n\n# This world: " + world["name"]]
    if world.get("premise"):
        parts.append("\n## Premise\n" + world["premise"].strip())
    entries = world.get("entries", [])
    if entries:
        parts.append("\n## Established canon (do not contradict)")
        budget = 26000
        for cat in CATEGORIES:
            cat_entries = [e for e in entries if e["category"] == cat]
            if not cat_entries:
                continue
            parts.append("\n### " + cat.capitalize())
            for e in cat_entries:
                content = e["content"].strip()
                if len(content) > 1500:
                    content = content[:1500] + " …"
                line = "- **{}** — {}".format(e["title"], content)
                budget -= len(line)
                if budget < 0:
                    parts.append("- (further canon omitted for length)")
                    break
                parts.append(line)
            if budget < 0:
                break
    else:
        parts.append("\n## Established canon\n(None yet — this world is a blank page. Help the user find its first big ideas.)")
    return "\n".join(parts)


def chat_messages_for_provider(world, limit=40):
    msgs = [{"role": m["role"], "content": m["content"]}
            for m in world.get("chat", [])]
    return msgs[-limit:]


# ---------------------------------------------------------------- providers

def _http_stream(req, timeout=300):
    """Open a request and yield decoded lines."""
    resp = urllib.request.urlopen(req, timeout=timeout)
    for raw in resp:
        yield raw.decode("utf-8", "replace")


def _provider_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except OSError:
            detail = ""
        return "Provider returned HTTP {}: {}".format(exc.code, detail or exc.reason)
    if isinstance(exc, urllib.error.URLError):
        return "Could not reach provider: {}".format(exc.reason)
    return "Provider error: {}".format(exc)


def stream_anthropic(cfg, system, messages, max_tokens):
    p = cfg["anthropic"]
    if not p.get("api_key"):
        raise RuntimeError("No Anthropic API key configured. Open Settings and paste your key (console.anthropic.com).")
    body = {
        "model": p["model"] or "claude-opus-4-8",
        "max_tokens": max_tokens,
        "stream": True,
        "system": system,
        "messages": messages,
    }
    req = urllib.request.Request(
        p["base_url"].rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": p["api_key"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    for line in _http_stream(req):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        etype = event.get("type")
        if etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                yield delta.get("text", "")
        elif etype == "error":
            raise RuntimeError(event.get("error", {}).get("message", "unknown API error"))
        elif etype == "message_stop":
            return


def stream_ollama(cfg, system, messages, max_tokens):
    p = cfg["ollama"]
    body = {
        "model": p["model"],
        "stream": True,
        "messages": [{"role": "system", "content": system}] + messages,
        "options": {"num_predict": max_tokens},
    }
    req = urllib.request.Request(
        p["base_url"].rstrip("/") + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for line in _http_stream(req):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("error"):
            raise RuntimeError(obj["error"])
        chunk = obj.get("message", {}).get("content", "")
        if chunk:
            yield chunk
        if obj.get("done"):
            return


def stream_openai(cfg, system, messages, max_tokens):
    p = cfg["openai"]
    body = {
        "model": p["model"],
        "stream": True,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    headers = {"Content-Type": "application/json"}
    if p.get("api_key"):
        headers["Authorization"] = "Bearer " + p["api_key"]
    req = urllib.request.Request(
        p["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    for line in _http_stream(req):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        choices = obj.get("choices") or []
        if choices:
            chunk = choices[0].get("delta", {}).get("content")
            if chunk:
                yield chunk


def stream_demo(cfg, system, messages, max_tokens):
    for word in re.split(r"(\s+)", DEMO_REPLY):
        yield word
        time.sleep(0.004)


PROVIDERS = {
    "anthropic": stream_anthropic,
    "ollama": stream_ollama,
    "openai": stream_openai,
    "demo": stream_demo,
}


def test_provider(cfg):
    """One tiny non-streaming round trip to verify connectivity."""
    provider = cfg.get("provider", "anthropic")
    if provider == "demo":
        return "Demo provider is always ready."
    chunks = []
    gen = PROVIDERS[provider](cfg, "You are a connectivity test. Reply with the single word: ready",
                              [{"role": "user", "content": "ping"}], 20)
    for chunk in gen:
        chunks.append(chunk)
        if len("".join(chunks)) > 40:
            break
    reply = "".join(chunks).strip()
    return "Connected. Model replied: {!r}".format(reply[:60] or "(empty)")


# ---------------------------------------------------------------- export

def export_markdown(world):
    lines = ["# {} — World Bible".format(world["name"]), ""]
    if world.get("premise"):
        lines += ["## Premise", "", world["premise"].strip(), ""]
    entries = world.get("entries", [])
    for cat in CATEGORIES:
        cat_entries = [e for e in entries if e["category"] == cat]
        if not cat_entries:
            continue
        lines += ["## " + cat.capitalize(), ""]
        for e in cat_entries:
            lines += ["### " + e["title"], "", e["content"].strip(), ""]
    if not entries:
        lines += ["*(No canon entries yet.)*", ""]
    lines += ["---", "*Exported from WorldLoom on {}*".format(time.strftime("%Y-%m-%d %H:%M"))]
    return "\n".join(lines)


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WorldLoom/1.0"

    # ---- helpers

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self._send_error_json("not found", 404)
            return
        if not target.is_file():
            self._send_error_json("not found", 404)
            return
        ctypes = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                  ".png": "image/png", ".ico": "image/x-icon"}
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctypes.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    # ---- routing

    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/config":
                return self._send_json(load_config())
            if path == "/api/worlds":
                return self._send_json(list_worlds())
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})", path)
            if m:
                try:
                    return self._send_json(load_world(m.group(1)))
                except KeyError:
                    return self._send_error_json("world not found", 404)
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/export", path)
            if m:
                try:
                    world = load_world(m.group(1))
                except KeyError:
                    return self._send_error_json("world not found", 404)
                md = export_markdown(world).encode()
                fname = re.sub(r"[^A-Za-z0-9_-]+", "_", world["name"]).strip("_") or "world"
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 'attachment; filename="{}_world_bible.md"'.format(fname))
                self.send_header("Content-Length", str(len(md)))
                self.end_headers()
                self.wfile.write(md)
                return
            return self._serve_static(path)
        except Exception as exc:  # noqa: BLE001 — last-resort guard for the request thread
            try:
                self._send_error_json("server error: {}".format(exc), 500)
            except OSError:
                pass

    def do_PUT(self):
        try:
            path = self.path.split("?", 1)[0]
            body = self._read_body()
            if path == "/api/config":
                cfg = load_config()
                for key, val in body.items():
                    if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                        cfg[key].update(val)
                    elif key in DEFAULT_CONFIG:
                        cfg[key] = val
                if cfg.get("provider") not in PROVIDERS:
                    return self._send_error_json("unknown provider")
                save_config(cfg)
                return self._send_json(cfg)
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})", path)
            if m:
                try:
                    world = load_world(m.group(1))
                except KeyError:
                    return self._send_error_json("world not found", 404)
                for key in ("name", "premise"):
                    if key in body and isinstance(body[key], str):
                        world[key] = body[key].strip()
                save_world(world)
                return self._send_json(world)
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/entries/([a-f0-9]{32})", path)
            if m:
                return self._update_entry(m.group(1), m.group(2), body)
            return self._send_error_json("not found", 404)
        except Exception as exc:  # noqa: BLE001
            self._send_error_json("server error: {}".format(exc), 500)

    def do_DELETE(self):
        try:
            path = self.path.split("?", 1)[0]
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})", path)
            if m:
                try:
                    world_path(m.group(1)).unlink()
                except FileNotFoundError:
                    return self._send_error_json("world not found", 404)
                return self._send_json({"ok": True})
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/entries/([a-f0-9]{32})", path)
            if m:
                try:
                    world = load_world(m.group(1))
                except KeyError:
                    return self._send_error_json("world not found", 404)
                before = len(world["entries"])
                world["entries"] = [e for e in world["entries"] if e["id"] != m.group(2)]
                if len(world["entries"]) == before:
                    return self._send_error_json("entry not found", 404)
                save_world(world)
                return self._send_json(world)
            return self._send_error_json("not found", 404)
        except Exception as exc:  # noqa: BLE001
            self._send_error_json("server error: {}".format(exc), 500)

    def do_POST(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/worlds":
                body = self._read_body()
                name = (body.get("name") or "").strip()
                if not name:
                    return self._send_error_json("world needs a name")
                world = {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "premise": (body.get("premise") or "").strip(),
                    "created": time.time(),
                    "updated": time.time(),
                    "entries": [],
                    "chat": [],
                }
                save_world(world)
                return self._send_json(world, 201)
            if path == "/api/config/test":
                body = self._read_body()
                cfg = load_config()
                for key, val in body.items():
                    if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                        cfg[key].update(val)
                    elif key in DEFAULT_CONFIG:
                        cfg[key] = val
                try:
                    return self._send_json({"ok": True, "detail": test_provider(cfg)})
                except Exception as exc:  # noqa: BLE001
                    return self._send_json({"ok": False, "detail": _provider_error(exc)})
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/entries", path)
            if m:
                return self._create_entry(m.group(1), self._read_body())
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/chat", path)
            if m:
                return self._chat(m.group(1), self._read_body())
            m = re.fullmatch(r"/api/worlds/([a-f0-9]{32})/chat/clear", path)
            if m:
                try:
                    world = load_world(m.group(1))
                except KeyError:
                    return self._send_error_json("world not found", 404)
                world["chat"] = []
                save_world(world)
                return self._send_json(world)
            return self._send_error_json("not found", 404)
        except Exception as exc:  # noqa: BLE001
            try:
                self._send_error_json("server error: {}".format(exc), 500)
            except OSError:
                pass

    # ---- entries

    def _create_entry(self, world_id, body):
        try:
            world = load_world(world_id)
        except KeyError:
            return self._send_error_json("world not found", 404)
        category = body.get("category")
        title = (body.get("title") or "").strip()
        content = (body.get("content") or "").strip()
        if category not in CATEGORIES:
            return self._send_error_json("unknown category")
        if not title or not content:
            return self._send_error_json("entry needs a title and content")
        entry = {
            "id": uuid.uuid4().hex,
            "category": category,
            "title": title[:200],
            "content": content[:8000],
            "created": time.time(),
        }
        world["entries"].append(entry)
        save_world(world)
        return self._send_json(world, 201)

    def _update_entry(self, world_id, entry_id, body):
        try:
            world = load_world(world_id)
        except KeyError:
            return self._send_error_json("world not found", 404)
        for entry in world["entries"]:
            if entry["id"] == entry_id:
                if body.get("category") in CATEGORIES:
                    entry["category"] = body["category"]
                if isinstance(body.get("title"), str) and body["title"].strip():
                    entry["title"] = body["title"].strip()[:200]
                if isinstance(body.get("content"), str) and body["content"].strip():
                    entry["content"] = body["content"].strip()[:8000]
                save_world(world)
                return self._send_json(world)
        return self._send_error_json("entry not found", 404)

    # ---- chat streaming

    def _chat(self, world_id, body):
        try:
            world = load_world(world_id)
        except KeyError:
            return self._send_error_json("world not found", 404)
        message = (body.get("message") or "").strip()
        if not message:
            return self._send_error_json("empty message")

        cfg = load_config()
        provider = cfg.get("provider", "anthropic")
        stream_fn = PROVIDERS.get(provider)
        if stream_fn is None:
            return self._send_error_json("unknown provider {!r}".format(provider))

        world["chat"].append({"role": "user", "content": message, "ts": time.time()})
        system = build_system_prompt(world)
        history = chat_messages_for_provider(world)
        max_tokens = int(cfg.get("max_tokens") or 16000)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(obj):
            self.wfile.write((json.dumps(obj) + "\n").encode())
            self.wfile.flush()

        collected = []
        try:
            for chunk in stream_fn(cfg, system, history, max_tokens):
                collected.append(chunk)
                emit({"text": chunk})
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away mid-stream; keep whatever was generated
        except Exception as exc:  # noqa: BLE001 — surface provider failures to the client
            err = _provider_error(exc)
            try:
                emit({"error": err})
            except OSError:
                pass
            if not collected:
                # Nothing generated: drop the user turn so a failed send can be retried.
                world["chat"].pop()
                save_world(world)
                return

        reply = "".join(collected)
        if reply.strip():
            world["chat"].append({"role": "assistant", "content": reply, "ts": time.time()})
        save_world(world)
        try:
            emit({"done": True})
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="WorldLoom worldbuilding studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    WORLDS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("WorldLoom is running:  http://{}:{}".format(
        "127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port))
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
