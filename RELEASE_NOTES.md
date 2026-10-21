# Asset Assistant 0.5.9

**A Blender add-on that tells you which of your game assets are broken — and does the rest of the pipeline for you.**

Blender is wonderful for making game assets and terrible for keeping them correct.
Nothing warns you that the normal map is in `sRGB` instead of `Non-Color`, that
the texel density on one island is twice that of its neighbour, or that the
texture file you linked two weeks ago is no longer on disk. You find out when
the engine complains — or, worse, when it doesn't.

## The doctor

`AI → Material Doctor` reads the whole scene and reports what is broken and
where. Twelve checks:

- data maps (roughness, metallic, normal, height) in the wrong colour space;
- a normal map wired straight into Base Color instead of a Normal Map node;
- texture files that are gone;
- meshes with textures but no UV layer;
- absolute paths and unpacked textures;
- a shader the exporter cannot express, displacement without subdivision;
- duplicate and unused materials, maps of different resolutions.

The safe ones are repaired for you (`Doctor → Fix`); nothing is deleted.

With a hundred objects in the scene, this is the difference between hunting for
the misconfigured one and being told.

## The pipeline

- **PBR from folder** — a folder of maps becomes a material, assigned, with the
  right colour spaces. A second run updates the material instead of creating
  `Wood.001`.
- **UV** — unwrap, even texel density, a shared atlas with padding, and a **UV
  kit**: the layout as a PNG plus a manifest (the atlas size, the gap, the
  density), to paint in Aseprite or Krita and bring back — without the UVs ever
  being touched.
- **Bake** — a procedural material turned into textures (albedo, normal,
  roughness, AO), written to a folder next to the `.blend`, with a plain PBR
  material on the object. The file names can follow the game convention
  (`_col`, `_nrm`, `_rgh`).
- **Textures** — pack everything into the `.blend`, make the paths relative,
  relink lost files, fix the colour spaces.
- **Export** — GLB and FBX with the axes and modifiers the engines expect. The
  readiness check runs first: the tool refuses to write a broken file, and the
  button puts the findings in the report.
- **Presets** — 16 procedural recipes and 16 hand-written layered materials
  (oak, light and dark wood, dry and peeling-bark wood, chipboard, tree
  cross-sections, clean / scratched / worn / rusty metal, asphalt) to start from.
  The words of your request are matched against the keywords in the code, so
  "дубовое дерево" finds the oak one without the model guessing.

## The agent

The same tools, plus a chat that sees your scene:

- every request carries a **snapshot of the scene**, focused on what you are
  looking at right now, plus a section of problems the model cannot see (lost
  files, data maps in `sRGB`, textures on meshes without UV);
- the model calls any of the **40 tools** by name and reads back what actually
  happened instead of guessing;
- **8 skills** carry the knowledge it should not improvise — the exact socket
  names of Blender 4.x/5.x, colour spaces, PBR wiring, the procedural recipes.
  They attach to your own words, and every code sample in them is executed in
  Blender by a test, so an instruction cannot quietly drift away from the API;
- when no tool fits, it writes the code and **Run code** executes it in the
  scene, with Undo;
- destructive tools never run on their own: the chat shows a Run / Cancel block
  and the turn stays open until you decide.

## What it does not do

A small local model does not invent beautiful procedural materials — and it
should not: the presets are hand-written for that, and the model picks one and
turns its numbers.

The buttons need **no model at all** (the doctor, the UV tools, baking, packing,
the export). The model is a multiplier, not a foundation.

## Install

1. Download `asset_assistant-0.5.9.zip` (below).
2. In Blender: *Edit → Preferences → Get Extensions → ▾ → Install from Disk*,
   pick the zip, enable **Asset Assistant**.
3. For the chat: a local model (`ollama pull qwen2.5-coder:7b`) or any
   OpenAI-compatible endpoint — the base URL goes in the preferences **with
   `/v1` and without `/chat/completions`** (the add-on appends it).

Requires Blender 4.2+ (tested on 5.2 LTS).

| | |
|---|---|
| Tools | 40 — each one a button, a model call, and a headless test |
| Doctor checks | 12, 4 repaired automatically |
| Skills | 8, with runnable code samples |
| Presets | 16 hand-written materials + 16 procedural recipes |
| Headless checks | 17 scripts running the real code in the real Blender |
