# 🔮 WorldLoom

A local worldbuilding studio for science fiction and fantasy settings. Brainstorm
iteratively with an AI partner, lock the good ideas into a living **Codex** —
geography, history, cultures, politics, magic & power systems, science, religion,
economy, languages, creatures, locations, characters, story hooks — and export a
Markdown **world bible** you can build stories or games on.

Runs on **macOS and Linux** with nothing but Python 3. No pip installs, no Node,
no build step. Your worlds and your API key never leave your machine.

## Quick start

```sh
cd worldloom
python3 server.py
```

Open **http://127.0.0.1:8765** in your browser, create a world, and start talking.

Out of the box the app uses the **Demo** provider (a canned reply) so you can try
the interface immediately. Connect a real model under **Settings**:

## Choosing a brain

| Provider | What you need |
|---|---|
| **Anthropic API** (subscription) | An API key from [console.anthropic.com](https://console.anthropic.com). Default model: `claude-opus-4-8` (also try `claude-sonnet-5` for lower cost or `claude-haiku-4-5` for speed). |
| **Ollama** (local, free) | Install from [ollama.com](https://ollama.com), then `ollama pull llama3.1` (or `qwen2.5`, `mistral-nemo`, …). WorldLoom talks to it at `http://127.0.0.1:11434`. |
| **OpenAI-compatible endpoint** | Any server speaking the `/v1/chat/completions` protocol: LM Studio, `llama.cpp` server, vLLM, or a hosted API. Set the base URL, model name, and key if required. |

Use **Test connection** in Settings to verify before you start.

## How the loop works

1. **Talk.** Loom (the AI partner) asks focused questions, offers concrete
   directions, and pushes on consequences — how geography shapes trade, how magic
   warps politics.
2. **Lock in canon.** When an idea crystallizes, Loom proposes **canon cards** in
   the chat. One click adds them to the Codex.
3. **The Codex feeds back.** Everything in the Codex is injected into the AI's
   context on every message, so the world stays consistent as it grows. You can
   also add and edit entries by hand.
4. **Export.** Download the whole world as a clean Markdown world bible.

## Where things live

- Worlds: `worldloom/data/worlds/*.json`
- Settings (including your API key, plain text, `chmod 600`): `worldloom/data/config.json`

Back up the `data/` directory to keep your worlds. The server binds to
`127.0.0.1` by default; use `--host`/`--port` to change
(`python3 server.py --host 0.0.0.0 --port 9000` to share on your LAN — note
there is no authentication, so only do that on a network you trust).

## Requirements

- Python 3.8+ (preinstalled on macOS and virtually every Linux distro)
- A modern browser
