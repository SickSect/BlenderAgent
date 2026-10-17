# codeagent

A local multi-agent CLI code generator. It writes code, runs it, tests it, and
fixes it — entirely on your machine, using open models via Ollama.

Most LLM tools stop at "generated". This one closes the loop: a tester agent
writes real assertions, the code is executed in a sandbox, and a reviewer only
approves when the tests actually pass.

## The loop

```
planner -> coder -> tester -> executor -> reviewer -> fixer
                                  ^                     |
                                  +---------------------+
                                   repeats until approved
```

| Agent | Responsibility |
|---|---|
| **Planner** | Designs the file structure and plan; returns `{language, entry, files, plan}` |
| **Coder** | Writes the full content of every file; returns `[{path, content}]` |
| **Tester** | Writes a standalone test module that imports the generated code and asserts on it |
| **Executor** | Lays all files out in a temp dir and runs the tests in isolation |
| **Reviewer** | Gives a verdict based on the real execution results |
| **Fixer** | Applies the review and returns the corrected file set |

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/download) running locally with a code model pulled
- Docker (optional — only for the isolated execution backend)

## Install

```bash
git clone <repo-url>
cd CLI-code-agent
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux / macOS
pip install -e .
```

Check the setup:

```bash
codeagent doctor      # is Ollama reachable? is the model available?
codeagent models      # list downloaded models
```

## Usage

### One-off task

```bash
codeagent run "write a factorial function" --exec -o result.py
```

### Interactive session

```bash
codeagent chat --exec
```

Inside a session:

| Command | What it does |
|---|---|
| `<any text>` | Runs it as a task |
| `/save <path>` | Writes all generated files to that folder |
| `/new` | Clears the session context |
| `/exit` | Ends the session |

The session keeps the previous task's files in context, so follow-ups like
"now add a delete method" build on what was already generated. Use `/new` when
switching to an unrelated task.

## Options

| Flag | Default | Description |
|---|---|---|
| `--exec / --no-exec` | `--no-exec` | Actually run the generated code (otherwise static check only) |
| `--backend` | `subprocess` | Where to run the code: `subprocess` or `docker` |
| `-n, --iterations` | `5` | Max review/fix iterations |
| `-t, --timeout` | `10` | Seconds before an execution is killed |
| `-m, --model` | from config | Override the model |
| `-q, --quiet` | off | Suppress step-by-step logs |

## Execution safety

Generated code is untrusted, so it never runs in the main process.

**Subprocess backend** — throwaway temp dir, isolated interpreter (`python -I`),
and on POSIX, hard limits on memory, CPU time and file size.

**Docker backend** — a disposable container with no network (`--network none`),
a memory cap, a process cap (`--pids-limit`), and the code mounted read-only.
This is the stronger option: network and filesystem isolation cannot be achieved
with a plain subprocess.

## Configuration

`src/codeagent/config.yaml` holds the model and host settings. Agent prompts
live in `src/codeagent/system_prompts/` as Markdown — edit them to change how
each agent behaves.

## Blender add-on: Game Asset Assistant

The project is being reoriented from a code-gen CLI into an AI asset helper.
The first integration is a Blender 4.2+ extension: an **AI Assistant** tab in
the 3D Viewport sidebar (`N` panel) with a chat that knows your current scene.

### Build & install

```bash
python tools/build_addon.py          # -> dist/game_asset_assistant-0.1.0.zip
```

Blender → *Edit → Preferences → Add-ons → Install from Disk*, pick the zip,
enable the add-on, then configure it under the add-on's preferences:

| Backend | Settings |
|---|---|
| **Ollama (local)** | host (default `http://localhost:11434`) + model, e.g. `qwen2.5-coder:7b` |
| **External API** | OpenAI-compatible base URL + API key + model |

The backend switch lives in the add-on preferences and can be flipped at any
time; the API key is stored in Blender's preferences (plain text — prefer the
local backend if that matters to you).

### How it works

```
Blender add-on  --commands.jsonl-->  sidecar worker  --HTTP-->  Ollama / API
   (timers)       <--events.jsonl---  (own process)   <--stream--
```

- Requests are executed in a **separate worker process** (`asset_core/worker.py`).
  Blender's API docs state that Python threads living past a script cause
  hard-to-diagnose crashes, so the add-on never blocks and never spawns threads —
  a `bpy.app.timers` callback polls two append-only JSONL files.
- A compact **scene snapshot** (objects, transforms, polygons, UV, materials,
  modifiers, selection) is injected into every request, so the model answers
  about *your* scene. Toggleable in preferences.
- `asset_core/` is Blender-independent (stdlib only) and is vendored into the
  extension zip by `tools/build_addon.py`.

### Roadmap for the add-on

- Tool-calling: the model will execute real `bpy` tools (create/modify objects,
  materials, export) over the same JSONL channel, executed on the main thread
  with confirmation for destructive operations.

## Tests

```bash
pytest -q
```

Integration tests need a running Ollama and are skipped by default:

```bash
RUN_INTEGRATION=1 pytest -q
```

Docker tests skip automatically when Docker is unavailable.

## Current limitations

- Flat file structure only — no nested folders yet
- Static validation implemented for Python; other languages are rejected rather
  than validated
- The Docker backend runs Python only

## Roadmap

- Nested folders and richer project layouts
- Static validation and execution for languages beyond Python
- Better review quality and smarter diffs
- Full session history, not just the previous task's files