# I built a Blender add-on that tells you which of your game assets are broken

*Blender is wonderful for making game assets and terrible for keeping them
correct.*

Nothing warns you that the normal map is in `sRGB` instead of `Non-Color`. That
the texel density on one island is twice that of its neighbour. That the
texture file you linked two weeks ago is no longer on disk. You find out when
the engine complains — or, worse, when it doesn't.

## The routine I wanted to kill

Every asset went through the same sequence, and every step was manual:

- unwrap it, and check that the texel density is even;
- hand the layout to an external painting program and bring the result back;
- bake the procedural material into real textures;
- pack the textures so the project survives being moved;
- find out it is still not ready to hand over.

And the part that actually hurt: with a hundred objects in the scene, finding
*the* badly configured one is no longer a five-minute job.

## The doctor

That last problem is what the add-on is really about.

`AI → Material Doctor` reads the whole scene and reports what is broken: the
wrong colour space, a normal map wired straight into Base Color, a missing
texture file, a mesh with textures but no UV, absolute paths, unpacked
textures, a shader the exporter cannot express. It says which object, which
material, how serious it is, and what can be repaired safely.

Twelve checks, and the safe ones are fixed for you. Nothing is deleted.

## What works today

**The buttons — no model needed, they just do the work**

- *PBR from folder* — a folder of maps becomes a material, assigned, with the
  right colour spaces.
- *Doctor* / *Fix* — the audit above, and the safe repairs.
- *UV* — unwrap, even texel density, a shared atlas with padding, and a **UV
  kit**: the layout as a PNG plus a manifest, to paint in Aseprite or Krita and
  bring back, without the UVs ever being touched.
- *Bake* — procedural material to textures, written next to the `.blend`, with
  a plain material on the object. The file names can follow the game
  convention (`_col`, `_nrm`, `_rgh`).
- *Textures* — pack into the `.blend`, relative paths, relink lost files, fix
  colour spaces.
- *Export* — GLB and FBX with the axes and modifiers the engines expect. The
  readiness check runs first.
- *Presets* — 16 procedural recipes and 4 hand-written layered materials to
  start from.

**The agent — the same tools, plus a chat that sees your scene**

- Every request carries a **snapshot of the scene**, focused on what you are
  looking at right now, and a section of problems the model cannot see.
- The model can call any of the **40 tools** by name, and reads back what
  actually happened instead of guessing.
- **8 skills** carry the knowledge it should not improvise — the exact socket
  names of Blender 4.x/5.x, colour spaces, PBR wiring. They attach to your
  own words, and every code sample in them is executed in Blender by a test,
  so an instruction cannot quietly drift away from the API.
- When no tool fits, it writes the code; the **Run code** button executes it
  in the scene, with Undo.
- Destructive tools never run on their own: it asks, and the chat shows a
  Run / Cancel block.

| | |
|---|---|
| Tools | 40 — each one a button, a model call, and a headless test |
| Doctor checks | 12, 4 of them repaired automatically |
| Skills | 8, with runnable code samples |
| Headless checks | 16 scripts that run the real code in the real Blender |
| Needs | Blender 4.2+ and a model (local via Ollama, or an API key) — only for the agent half |

## Where it is going

**More around the agent**, because right now it does not do that much, but it
can already help with analysis and information:

1. the result of "Run code" back into the model's context, so it can check its
   own work;
2. a doctor check for *"this is a flat BSDF, not a procedural material"* —
   the piece that lets a small local model learn to build materials;
3. the preset buttons calling the tool directly;
4. the presets as node groups, so they live in the `.blend` like any Blender
   asset;
5. the model driving other add-ons.

**And around the pipeline:** ORM packing, a preview render, and material
generation by the model as a separate step once (2) exists.

## Try it

```bash
git clone <repo-url>
cd CLI-code-agent
python tools/build_addon.py      # -> dist/asset_assistant-<version>.zip
```

*Edit → Preferences → Get Extensions → ▾ → Install from Disk*, pick the zip,
enable **Asset Assistant**.

The buttons need no model at all. For the agent, run Ollama locally
(`ollama pull qwen2.5-coder:7b`) or point it at any OpenAI-compatible
endpoint.

---

*If the doctor has ever found a broken asset you did not know about, that is
the feature I would like to hear about.*
