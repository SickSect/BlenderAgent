# Asset Assistant 0.5.9

The installable extension for Blender 4.2+.

**Download:** `asset_assistant-0.5.9.zip` (below) → in Blender:
*Edit → Preferences → Get Extensions → ▾ → Install from Disk*, pick the zip,
enable **Asset Assistant**.

**A model is required** — either a local one
([Ollama](https://ollama.com/download), `ollama pull qwen2.5-coder:7b`) or any
OpenAI-compatible endpoint with an API key. The deterministic half of the
add-on (the material doctor, UV tools, baking, packing, the export check) needs
no model at all.

## What is in this release

**The chat is no longer a guessing game about the scene.** The model now sees
what it said itself and what the tools answered, so it stops repeating itself:
a request that needed three tools used to start the same answer three times.

**The panel survives.** An icon that does not exist in Blender 5.2 (`MOD_EXTRUDE`)
killed the whole panel on the first redraw. Every icon and every operator id
in the UI is now checked against the running Blender
(`tests/run_ui_check.py`).

**The bake is a pipeline, not a button.** One click bakes the procedural
material, writes the maps to `<Name>_Bake/` next to the `.blend`, builds a
plain PBR material from them and puts it on the object. The size, the sample
count, the map set and the file naming (`_col` / `_nrm` / `_rgh` for the game
pipelines) are settings right under the button. Baking used to take minutes
because it rendered at the project atlas size; one object needs 1024.

**Nothing is overwritten silently.** A folder with two files for one role
(`Wood_albedo.png` and `Wood_col.png`) says so out loud. A destructive tool
that was waiting for a confirmation dies with the turn, so it cannot run
after you pressed Stop.

**The words are understood.** "сделай текстуру дерева" used to build bricks:
the keyword `дерево` does not appear in `дерева`. The matching now survives
the Russian cases, and the assistant is told when the material it built is not
the one you asked for.

**More tools, more skills:** 37 tools in total, 16 procedural recipes (brick,
tile, cobble among them), 8 verified skills, and a textbook of 23 chapters in
Russian.

## Known rough edge

On a small local model the assistant is not yet reliable at *choosing* a
material recipe. The deterministic half of the add-on does not depend on that.

## The changelog in short

- the tool loop: the model sees its own turn and the tool reports, the
  conversation is kept, the code block survives and collapses when it is run;
- the procedural materials: the brick pattern, no silent fog, the recipes
  live in the code, the knowledge in the skills, and the prompt stopped eating
  80% of the context window;
- the baking: the whole pipeline, the settings, the game file names;
- the bugs found by reading the code: the collapsed-code flag, the confirmation
  that survived a stop, the second bake that read its own result, the temp
  folders that piled up.
