# Asset Assistant

An AI assistant for game assets that lives **inside Blender**.

A chat in the sidebar that knows your scene, plus a set of deterministic
tools — materials, textures, UV, baking, project hygiene — that the assistant
can call on its own and that you can press yourself.

The assistant does not draw. It does the mechanical part of an asset pipeline:
it checks what is broken, wires the maps, unwraps, bakes and gets the project
ready to hand over to a game engine.

---

## What it does

| Area | What you get |
|---|---|
| **Materials** | audit and repair, build a PBR material from a folder of maps, assign to the selection, replace everywhere, instances, tinted variants, cleanup |
| **Textures** | report, pack into the `.blend`, relative paths, relink lost files, fix colour spaces |
| **UV** | texel-density audit, seams, unwrap, shared atlas, a kit for Aseprite/Krita || **Baking** | procedural material → maps, shared atlas, normal map from a height map, plain material from the result |
| **Procedural** | 16 recipes built from a description — always a valid node graph |
| **Shader menu** | right-click a texture in the Shader Editor: fix its colour space, pack it, make its path relative, connect it to Base Color / Roughness / Normal |
| **Project** | the whole folder → material → assign → pack → check in one call |

**37 tools.** Every tool is written once and available in three places: as a
button in the AI panel, as a call the model may make by name, and as a case in
the headless test harness. **8 skills** carry the knowledge the model needs
(exact socket names, colour spaces, PBR wiring, the procedural recipes).

Two tools are destructive and never run on their own: the assistant asks, and
the chat shows a **Run / Cancel** block.

---

## Requirements

- **Blender 4.2 or newer** (tested on 5.2 LTS).
- **A model — one of two ways:**
  - **local**: [Ollama](https://ollama.com/download) running on your machine
    with a code model pulled:
    ```bash
    ollama pull qwen2.5-coder:7b
    ```
    Nothing leaves your computer.
  - **or an API key**: any OpenAI-compatible endpoint — base URL, key and
    model name. A hosted model is stronger on small tasks; a local one is
    private and free.

The backend is switched in the add-on preferences at any time.

> The **deterministic half** of the add-on — the material doctor, the UV
> audit, baking, packing, the export check — needs **no model at all**. Those
> are plain buttons that run real Blender code.

---

## Install

### 1. Get the extension

**Download the ready-made zip** from the
[releases page](https://github.com/SickSect/BlenderAgent/releases) - the
file `asset_assistant-<version>.zip`. No clone, no Python, no build: install
it and it works.

Or build it yourself from the sources (only the standard library is needed):

```bash
git clone <repo-url>
cd BlenderAgent
python tools/build_addon.py          # -> dist/asset_assistant-0.5.9.zip
```

Every release is built from the tagged commit, so the zip on the releases page
always matches the tag.

### 2. Install it in Blender

*Edit → Preferences → Get Extensions → ▾ → Install from Disk*, pick the zip,
enable **Asset Assistant**. (In 4.2–4.5 the legacy *Add-ons* tab takes the
same zip.)

### 3. Point it at a model

*Edit → Preferences → Add-ons (or *Get Extensions*) → Asset Assistant*:

| Setting | Meaning |
|---|---|
| **Language** | interface language (Russian or English) |
| **Backend** | `Ollama` (local) or `OpenAI API` |
| **Ollama host / model** | `http://localhost:11434` and the model you pulled |
| **Base URL / API key / model** | for the API backend |
| **Model context (`num_ctx`)** | the context window the model gets (16384 by default) |
| **Context budget** | how many characters of the conversation are sent |
| **Send the scene snapshot** | whether every request carries the state of your scene |
| **Chat window width** | the width of the floating chat |

### 4. Open it

- `N` in the 3D Viewport → the **AI** tab of the sidebar;
- the same tab in the Shader Editor and the Image Editor;
- `Ctrl+Alt+A` anywhere → the chat as a floating window;
- the **Scripts** button next to *Shader Editor* and *Image Editor* → a Text
  Editor right next to the view, where the assistant's code lands.

> 🧪 **See it working:** [`demo/README.md`](demo/README.md) is a step-by-step
> walkthrough for a Blender beginner (in Russian). It opens a scene with
> deliberately broken materials and shows the assistant naming the problems the
> snapshot finds. Generate the scene with `python tools/demo_scene.py`.

---

## How it works

```
Blender add-on  --commands.jsonl-->  sidecar worker  --HTTP-->  Ollama / API
   (timers)      <--events.jsonl----   (own process)   <--stream--
```

- The assistant's requests run in a **separate worker process**. Blender's API
  docs state that Python threads living past a script cause hard-to-diagnose
  crashes, so the add-on never blocks and never spawns threads — a
  `bpy.app.timers` callback polls two append-only JSONL files.
- A compact **scene snapshot** travels with every request: objects,
  transforms, polygons, UVs, modifiers, selection, plus per-material node,
  shader and texture details and the image list. It *focuses* on what you are
  working on: in the Shader Editor it details the material being edited, in the
  Image Editor the texture.
- A **Problems** section tells the model what it cannot see: texture files
  that are gone from disk, data maps (roughness / metallic / normal) still in
  `sRGB`, textured meshes without a UV layer.
- The model may call a **tool** by name:
  ````
  ```tool
  material.build_from_folder {"folder": "D:/textures/Wood_2k"}
  ```
  ````
  The tool runs on the main thread and its report goes back into the model's
  context, so the assistant reports *what actually happened* instead of
  guessing.
- The last ```python block of a reply can be executed right in the scene with
  the **Run code** button.

---

## The tools

### Materials

| Tool | What it does |
|---|---|
| `material.audit` | Finds the mistakes that are invisible in the viewport: data maps in the wrong colour space, normal maps wired into Base Color, missing texture files, meshes without UVs, unused and duplicate materials |
| `material.fix` | Applies the repairs that are safe: colour spaces, normal maps through a Normal Map node, an unconnected shader output. Nothing is deleted |
| `material.build_from_folder` | Builds a PBR material from a folder of maps (albedo, normal, roughness, metallic, ORM, emission, height), assigns it to the active object, and reuses the material on a second run instead of making `Wood.001` |
| `materials.assign_to_selection` | Puts a material on every selected object (the active slot is replaced, not appended) |
| `materials.replace_everywhere` | Replaces a material across the scene **in the slots**, so the per-face assignments stay valid |
| `materials.find_users` | Who uses this material: the object, the slot index and the number of faces in it |
| `materials.make_instance` / `make_single_user` | Share one mesh between objects, and undo that |
| `materials.make_variant` | A tinted copy of a material — an independent copy whose Base Color is multiplied by the tint |
| `materials.cleanup_orphans` | Deletes unused materials and images. **Destructive** — the assistant asks first |

### Textures

| Tool | What it does |
|---|---|
| `textures.report` | Every texture with its size, colour space, whether it is packed and where its file is |
| `textures.pack_all` | Packs every texture into the `.blend`, so the project travels with its textures |
| `textures.unpack_all` | Writes the packed textures back to disk. **Destructive** — it can overwrite files |
| `textures.make_relative` | Rewrites absolute texture paths as relative to the `.blend` |
| `textures.relink` | Finds texture files that went missing and points the images at them again, by name |
| `textures.fix_colorspace` | Sets every texture to the right colour space: `sRGB` for colour maps, `Non-Color` for data maps |

### UV

| Tool | What it does |
|---|---|
| `uv.audit` | Texel density in pixels per metre, overlapping islands, coordinates outside `[0,1]`, stretched triangles |
| `uv.unwrap` / `uv.unwrap_own` | Unwraps with automatic seams, or leaves every island where it is (for baking) |
| `uv.mark_seams` | By angle, by height (a horizontal cut — the way a sleeve needs one) or by the sharp edges |
| `uv.build_atlas` | Lays the islands of several objects into one shared atlas with a gap and a padding |
| `uv.match_density` | Brings every island to the same texel density, so the texture is equally sharp everywhere. The islands are scaled and packed — the layout changes, and `Ctrl+Z` puts it back. An already even layout is not touched at all |
| `uv.symmetric` | Unwraps symmetrically: the far half gets the UVs of its counterpart, so one painting serves both sides |
| `uv.export_kit` / `uv.import_atlas` | The round trip with Aseprite or Krita: the layout as a PNG plus a manifest (the atlas size, the gap, the density) and a copy of the current texture — paint it there, bring it back, and the UV coordinates are never touched, so the picture lands exactly where the layout said it would |

### Baking

| Tool | What it does |
|---|---|
| `bake.check` | Whether the object can be baked: a UV layout without overlaps, a material, a source of height for the normal map |
| `bake.maps` | Bakes the procedural material into albedo, normal, roughness and AO — then builds a plain material from the maps and puts it on the object. The maps are saved in a folder next to the `.blend` (`<Name>_Bake/`), so the same material can go on other objects |
| `bake.atlas` | The same for several objects into one shared atlas |
| `bake.normal_from_height` | A normal map from a height map: the height is differentiated into surface slopes, the way every normal map expects |
| `bake.replace` | Builds a plain material from the already baked maps and swaps it in |

### Procedural materials

| Tool | What it does |
|---|---|
| `procedural.find` | Finds the recipes that match the user's words — the matching happens in the code, not in the model's memory |
| `procedural.list` | The whole catalogue with the pattern of each recipe |
| `procedural.build` | Builds a material from a recipe plus overrides, or from explicit parameters. The graph is always valid: the assistant picks the numbers, the add-on assembles the nodes |

16 recipes: wood, marble, stone, concrete, rust, brushed metal, fabric, leather,
paper, snow, lava, plastic, checker, **brick**, **tile**, **cobble**.

### Project and export

| Tool | What it does |
|---|---|
| `project.import_textures` | The whole workflow in one step: scan a folder of maps, build the PBR material, assign it, pack the textures and check that the project is ready to hand off |
| `export.check` | Whether the project is ready for glTF/FBX: lost files, unpacked textures, meshes without UVs, shaders the exporter cannot express, textures that are too large |
| `export.glb` / `export.fbx` | Writes the selected objects for a game: the axes the engines expect, the modifiers applied, the textures copied next to the file. The readiness check runs first and refuses the export if it found errors |

### The assistant's own tools

| Tool | What it does |
|---|---|
| `tools.list` | What the add-on can do — the catalogue of tools and the skills |
| `tools.history` | What has already been done to the scene during this conversation, with the reports |

---

## Skills

Skills are knowledge; tools are actions. A skill is a markdown file
(`src/blender_addon/skills/*.md`) with a small front-matter block: the
Principled inputs of Blender 4.x/5.x, colour spaces, PBR map wiring, glass,
metals, normal maps, ORM packing, procedural recipes.

The system prompt always carries a one-line catalogue of them. When a task
needs detail, the model answers with

````
```skill
material-glass
```
````

and the add-on appends that instruction to the prompt and asks again (at most
two such round trips per question). Skills are also attached **automatically**:
the add-on matches their keywords against your message, so "make a glass
material" gets the glass recipe in the very first request.

Every code sample inside a skill is executed inside Blender by
`tests/run_skills_check.py`, so an instruction cannot silently drift away from
the API.

---

## Tests

```bash
pytest -q
```

The add-on has its own harness: every check runs the **real** code on the
**real** Blender, in `--background`, on a synthetic scene.

```bash
blender --background --factory-startup --python tests/run_tools_check.py
blender --background --factory-startup --python tests/run_skills_check.py
blender --background --factory-startup --python tests/run_procedural_check.py
blender --background --factory-startup --python tests/run_baking_check.py
blender --background --factory-startup --python tests/run_uv_check.py
blender --background --factory-startup --python tests/run_doctor_check.py
blender --background --factory-startup --python tests/run_textures_check.py
blender --background --factory-startup --python tests/run_bulk_check.py
blender --background --factory-startup --python tests/run_project_check.py
blender --background --factory-startup --python tests/run_confirm_check.py
blender --background --factory-startup --python tests/run_exchange_check.py
blender --background --factory-startup --python tests/run_heightmap_check.py
blender --background --factory-startup --python tests/run_snapshot_check.py
blender --background --factory-startup --python tests/run_ui_check.py
blender --background --factory-startup --python tests/run_readonly_check.py
```

The UI has its own check: the panel is only drawn in a window, so a wrong icon
or a typo in an operator id stays invisible to every other test — and then the
whole panel dies on the first redraw. `run_ui_check.py` compares the icons and
the operator ids in the UI with what the running Blender offers.

The reporting buttons are checked for being free: `run_readonly_check.py` takes
a fingerprint of the whole scene — every vertex, every UV, every socket value,
every link, the images, the mode, the selection, the project settings — runs
the doctor, the UV audit, the export check, the texture report and the other
reporting tools, and compares. A button that only reports must not move a
single coordinate. The same script checks that the acting buttons leave the
mesh in object mode instead of stranding you in edit mode.

---

## 📖 The textbook

[`docs/`](docs/README.md) is a teacher-style walkthrough of the whole code —
Blender API basics, the architecture, the chat/worker protocol, running AI code
safely, materials and textures, the scene snapshot, the skill library, the
doctor, UV, baking. It is written in Russian and meant to be read, not
skimmed.

Start with the [features guide](docs/15-features-guide.md) if you want to know
what to press.

---

## Roadmap

**Done** — the materials-and-textures line: the focused scene snapshot, the
doctor, the tool registry, skills, texture hygiene, bulk operations, the UV
toolbox, the baking pipeline, the confirmation dialog, the project pipeline.

**Next — the export itself.** The add-on checks that the project is ready for
glTF/FBX but does not export. One button ("export the selected objects with
the settings a game needs") closes the loop from *a folder of textures* to *a
file the engine opens*.

**Then**
- several tool calls per answer instead of one round trip each;
- the assistant learns to drive other add-ons;
- the procedural recipes grow (and the model picks them better).

**Known rough edge:** on a small local model the assistant is not yet reliable
at *choosing* a material recipe. The deterministic half of the add-on does not
depend on that.

---

## Releasing

Publishing is manual — a GitHub release is a web form:

```bash
python tools/release.py --bump 0.5.10   # write the version everywhere
python tools/release.py                 # build the zip and print the steps
git add -A && git commit -m "release: 0.5.10"
git tag v0.5.10 && git push origin main v0.5.10
```

Then *Releases → Draft a new release*, pick the tag `v0.5.10`, attach
`dist/asset_assistant-0.5.10.zip`, paste the notes (`RELEASE_NOTES.md`) and
publish. The script checks that the version agrees in all three places it is
written, so a forgotten bump cannot ship a mislabelled add-on.

---

## Repository layout

```
src/blender_addon/     the add-on (runs inside Blender)
src/asset_core/        the worker: Blender-independent, stdlib only
tools/build_addon.py   builds the installable zip
tools/release.py       bumps the version, builds the zip, prints the release steps
docs/                  the textbook (Russian)
demo/                  a demo scene and its walkthrough
tests/                 the headless harness
RELEASE_NOTES.md       the notes of the current release
```

The repository also contains the earlier project (`src/codeagent/`, a local
multi-agent CLI code generator). It is no longer the focus; the add-on is.

## License

MIT.
