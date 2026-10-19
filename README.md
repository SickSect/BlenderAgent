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

## Blender add-on: Asset Assistant

The project is being reoriented from a code-gen CLI into an AI asset helper.
The first integration is a Blender 4.2+ extension: an **AI Assistant** tab in
the 3D Viewport sidebar (`N` panel) with a chat that knows your current scene.

> 📖 **Textbook (`docs/`).** A from-scratch, teacher-style walkthrough of the
> code — Blender API basics, the add-on architecture, the chat/worker protocol,
> running AI code safely, materials/texturing recipes with runnable examples,
> the scene snapshot, Blender's window/editor system, and the skill library:
> [`docs/`](docs/README.md). It is written in Russian.

### Build & install

```bash
python tools/build_addon.py          # -> dist/asset_assistant-0.5.0.zip
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

### Skills

Beyond the prompt, the assistant has a small library of **skill** files
(`src/blender_addon/skills/*.md`): Principled inputs, colour spaces, PBR maps,
glass, metals, normal maps, ORM packing. The system prompt carries only a
one-line catalog; when a task needs detail the model answers with

````
```skill
material-glass
```
````

and the add-on appends that instruction to the prompt and asks again (at most
two such round trips per question). Skill code samples are executed inside
Blender by `tests/run_skills_check.py`, so they cannot silently drift away from
the API.

### Interface language

The add-on UI is bilingual (**RU / EN**). Switch it under *Preferences →
Add-ons → Asset Assistant → Interface language*; the assistant is asked to
answer in the same language (there are two system prompts). All strings live in
`src/blender_addon/i18n.py`, so adding a language means adding one dict to
`i18n.STRINGS` plus one item to the `language` enum. Operator labels/tooltips
are RNA-baked and re-registered on the switch; a few preference tooltips stay
bilingual because Blender bakes them at registration time.

### Chat window & running code

- The chat is **docked in three places** (one shared state, one `AI` tab):
  3D Viewport sidebar (`N`), Shader/Node Editor sidebar and Image/UV Editor
  sidebar. `Ctrl+Alt+A` or the "Chat window" button opens it as a **floating
  popup** next to the mouse cursor; `Esc` or a click outside closes it.
- Popup width is configurable: *Preferences → Add-ons → Asset Assistant →
  Chat window width* (300–1400 px, default 560); text wrapping follows it.
- When a reply contains a ` ```python ` block, it appears in an "AI code ready
  to run" box with two buttons: **Run code** runs it in the current scene on
  the main thread (stdout and tracebacks are reported back into the chat),
  **To Text Editor** stores it in an `AI Assistant Code` text datablock.

### Material shortcuts

Two one-click buttons jump straight from the chat to the editor that matters for
the active object's material:

- **Shader Editor** — shows the material's node tree (`shader_type = OBJECT`,
  follows the active object).
- **Image Editor** — shows the material's first texture (or `Render Result`, or
  the file's first image) if the material has no textures.

Both reuse an already-open editor when there is one, otherwise repurpose the
largest area that is *not* the one the button was clicked from, so the sidebar
hosting the chat is never replaced. If the active object has no material, the
editor still opens and the status bar says so. See
[`docs/08-windows-and-editors.md`](docs/08-windows-and-editors.md).

Below the input, a **Material presets** block offers ready-made requests and one
one-click action:

- **Presets** (Matte / Metal / Glass / Emission / PBR) fill the input with a
  ready-made request — a starting point you can edit before sending. They are
  localized; PBR asks for albedo as color and the data maps as `Non-Color`. See
  [`docs/09-presets-and-operator-props.md`](docs/09-presets-and-operator-props.md).
- **PBR from folder** builds the material itself: pick a texture folder and the
  add-on guesses each map's role from its file name (`albedo`/`base`, `normal`,
  `roughness`, `metallic`, packed `ORM`, `emission`, `height`), loads the images
  with the right colour space (colour maps as `sRGB`, data maps as `Non-Color`),
  wires a Principled BSDF node tree and assigns it to the active object. No model
  call involved. See
  [`docs/10-pbr-from-folder.md`](docs/10-pbr-from-folder.md).

### Running AI code safely

AI code is arbitrary Python, so the add-on does a few things to keep it
predictable:

- **Real viewport context.** The script runs inside a `temp_override` bound to a
  real 3D viewport (window/screen/area/region/space/view layer), so
  context-sensitive operators such as `bpy.ops.object.duplicate` behave exactly
  as if you pressed the hotkey. Running them from the chat popup's UI region could
  otherwise leave the dependency graph in a state Blender crashes on later
  (`DepsgraphNodeBuilder::build_materials` access violation).
- **Immediate graph rebuild.** `view_layer.update()` runs right after the script,
  while the good context is still active, instead of leaving a transient state for
  the event loop.
- **Undoable.** *Run code* is an `UNDO` operator, so the whole script is one
  `Ctrl+Z` step.
- **One-shot.** The block can be run once per reply (the button is replaced by a
  "Ran · Ctrl+Z to undo" label). Re-running the same reply used to duplicate the
  cube again and again — ask the assistant for a fresh answer to run something
  anew.
- Still, this is *arbitrary* code inside Blender: save your file before running
  anything destructive, and prefer `Ctrl+Z` if the result is not what you wanted.

### Worker lifecycle

The sidecar worker is a child process. If Blender dies hard (a crash runs no
cleanup), the worker notices through a parent-pid watchdog and exits on its own,
and it is started with its working directory in a temp folder — never inside the
add-on directory, which a lingering worker would otherwise keep locked and make
reinstalling the extension fail with `Access is denied`.

### Troubleshooting: Blender crashed after running AI code

**Symptoms.** Blender closes *after* the assistant's script has finished, not while
it runs. The crash report (Windows: `%TEMP%`, `blender.crash.txt`) shows an access
violation in `DepsgraphNodeBuilder::build_materials` with an **empty** Python
backtrace. That points at Blender's C++ dependency-graph rebuild, not at the Python
code.

**What the add-on already does to avoid it:**

- runs the script inside a real 3D viewport context (`temp_override`) — see
  [Running AI code safely](#running-ai-code-safely);
- calls `view_layer.update()` right after the script;
- makes *Run code* an `UNDO` operator and a one-shot button.

**If you still hit a crash:**

1. **Save the file first** (`Ctrl+S`) — the add-on cannot undo a hard crash.
2. Copy the crash log (Windows `%TEMP%\blender.crash.txt`; on Linux/macOS run
   Blender with `--debug` and capture the backtrace).
3. Note what triggered it — which reply, which script, how many times *Run code*
   was pressed — and file it in the Blender issue tracker: this is a
   dependency-graph crash in Blender that reproduces outside the add-on.
4. Workaround: ask for a **fresh answer** (the button is one-shot on purpose) and
   prefer data-API operations (`bpy.data…`, `collection.link()`) over
   context-sensitive operators (`bpy.ops…`) in generated code.

#### A lingering worker holds the extension folder

If installing the extension fails with `[WinError 5] Access is denied`, a worker
from a crashed Blender may still be alive. Find it:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*asset_core*worker.py*" } |
  Select-Object ProcessId, CommandLine
```

Kill the reported PID(s), then reinstall. (The parent-pid watchdog normally makes
this unnecessary — it only happens if the watchdog could not start.)

### How it works

```
Blender add-on  --commands.jsonl-->  sidecar worker  --HTTP-->  Ollama / API
   (timers)       <--events.jsonl---  (own process)   <--stream--
```

- Requests are executed in a **separate worker process** (`asset_core/worker.py`).
  Blender's API docs state that Python threads living past a script cause
  hard-to-diagnose crashes, so the add-on never blocks and never spawns threads —
  a `bpy.app.timers` callback polls two append-only JSONL files.
- A compact **scene snapshot** (objects, transforms, polygons, UV, modifiers,
  selection, plus per-material node/shader/texture details and the image list)
  is injected into every request, so the model answers about *your* scene.
  Toggleable in preferences.
- `asset_core/` is Blender-independent (stdlib only) and is vendored into the
  extension zip by `tools/build_addon.py`.
- **Skills**: verified instruction packs (`skills/*.md`) the model can pull in
  on demand. The system prompt always carries a one-line catalog; when a task
  needs detail the model answers with a ```skill``` block, the add-on appends
  that instruction to the prompt and asks again. Bodies are written in English
  (half the tokens, and the model knows the API better in English), live in git,
  and every ```python block in them is executed by `tests/run_skills_check.py`
  on the running Blender — a renamed socket is caught before a user sees it.

### Roadmap for the add-on

- Materials & texturing through the assistant (in progress): the scene snapshot
  now covers materials and images, the chat can open the Shader/Image editor on
  the active material, "PBR from folder" builds a textured material in one
  click, and skills ship verified recipes for glass, metals, normal maps, ORM
  packs and colour spaces. Still to come: node quick actions,
  generating/packing/backing textures.
- Tool-calling over the JSONL channel: real `bpy` tools (create/modify objects,
  materials, export) executed on the main thread with confirmation for
  destructive operations, plus teaching the model to use extra add-ons. The
  skill protocol is the first half of this.

## Tests

```bash
pytest -q
```

The skill library has two levels of checks; the second one needs Blender and
executes every code sample the assistant can be handed:

```bash
blender --background --factory-startup --python tests/run_skills_check.py
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