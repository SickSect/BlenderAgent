# Asset Assistant 0.5.9

An AI assistant for game assets that lives inside Blender — and a set of
deterministic tools that do the boring half of the pipeline.

**Download:** `asset_assistant-0.5.9.zip` (below) → in Blender:
*Edit → Preferences → Get Extensions → ▾ → Install from Disk*, pick the zip,
enable **Asset Assistant**.

**A model is required for the chat half** — either a local one
([Ollama](https://ollama.com/download), `ollama pull qwen2.5-coder:7b`) or any
OpenAI-compatible endpoint with an API key. The buttons — the doctor, the UV
tools, baking, packing, the export — need no model at all.

## The doctor

`AI → Material Doctor` reads the whole scene and reports what is broken and
where: a normal map in `sRGB`, a normal map wired straight into Base Color, a
texture file that is gone, a mesh with textures but no UV, absolute paths,
unpacked textures, a shader the exporter cannot express, duplicate and unused
materials, maps of different resolutions.

Twelve checks. The safe ones are repaired for you; nothing is deleted. With a
hundred objects in the scene, this is the difference between hunting for the
misconfigured one and being told.

## The pipeline

- **PBR from folder** — a folder of maps becomes a material, assigned, with
  the right colour spaces. A second run updates it instead of making
  `Wood.001`.
- **UV** — unwrap, even texel density, a shared atlas with padding, and a
  **UV kit**: the layout as a PNG plus a manifest, to paint in Aseprite or
  Krita and bring back, without the UVs ever being touched.
- **Bake** — procedural material to textures (albedo, normal, roughness, AO),
  written to a folder next to the `.blend`, with a plain material on the
  object. The file names can follow the game convention (`_col`, `_nrm`,
  `_rgh`).
- **Textures** — pack into the `.blend`, relative paths, relink lost files, fix
  colour spaces.
- **Export** — GLB and FBX with the axes and modifiers the engines expect.
  The readiness check runs first: the tool refuses to write a broken file.
- **Presets** — 16 procedural recipes and 4 hand-written layered materials
  (scratched metal, worn metal, rusty painted metal, clean brushed steel) to
  start from.

## The agent

- Every request carries a **snapshot of the scene**, focused on what you are
  looking at right now, plus a section of problems the model cannot see.
- The model calls any of the **40 tools** by name and reads back what actually
  happened. Destructive tools never run on their own — the chat shows a
  Run / Cancel block.
- **8 skills** carry the knowledge it should not improvise: the exact socket
  names of Blender 4.x/5.x, colour spaces, PBR wiring, the procedural
  recipes. They attach to your own words, and every code sample in them is
  executed in Blender by a test.
- When no tool fits, it writes the code; **Run code** executes it in the scene,
  with Undo.

## Known limit

A small local model does not invent beautiful procedural materials — and it
should not: the presets are hand-written for that, and the model picks one and
turns its numbers. The words of your request are matched against the
keywords in the code, so "поцарапанный металл" finds the right material
without the model's guess.

| | |
|---|---|
| Tools | 40 — each one a button, a model call, and a headless test |
| Doctor checks | 12, 4 repaired automatically |
| Skills | 8, with runnable code samples |
| Headless checks | 16 scripts running the real code in the real Blender |
| Requires | Blender 4.2+ (tested on 5.2 LTS) |
