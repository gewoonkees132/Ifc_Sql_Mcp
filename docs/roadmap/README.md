# Roadmap: a participatory feature request

[`feature-request.pdf`](feature-request.pdf) asks the people who would use the tool what
it should do next. It lists twenty features in four themes, each starting from a
question an architect asks and from something the tool already does:

| Theme | Features | What they add |
|---|---|---|
| 1 See it | F01–F03 | A model viewer beside every answer, plans with the rooms that were counted, changes shown in the model |
| 2 Check it | F04–F09 | Participatory audits: the architect's verdict on an answer, a finding or a figure is stored as data |
| 3 Ask it | F10–F14 | Areas to SIA 416, DIN 277 or NEN 2580, cost groups, embodied carbon, the room programme, Excel and BCF |
| 4 Scale it | F15–F20 | An answer log, an office database with sign-in, quality trends, Rhino and Grasshopper, watched folders, localised Revit |

The PDF is fillable and printable. Each feature has a vote (now, later, not for me) and
a note box. Page 12 asks for the reader's three most needed features, what is missing,
and whether they would bring a model to a participatory audit session. Red marks every
place where a person decides; black is what the tool proposes.

Every figure in it comes from [the November audit](../audit/2026-11-modern-revit-exports.md),
[its re-check](../audit/2026-11-recheck.md) and the README. When the code changes, keep
the "Builds on" texts in [`source/features.js`](source/features.js) true.

## Building it

```bash
uv run --no-project --with fonttools --with reportlab --with pypdf \
    python docs/roadmap/source/build.py
```

- [`source/features.js`](source/features.js) holds the content: themes, features, and
  for each one the question, what it would do, what it builds on and who decides.
- [`source/feature_request.html`](source/feature_request.html) lays it out as fixed A4
  pages and draws the cover plan, the participation loop and the pipeline.
- [`source/render.mjs`](source/render.mjs) prints it with Playwright's Chromium, fails
  if a page overflows, and records where every form box sits.
- [`source/build.py`](source/build.py) converts Inter to TrueType outlines (Chromium
  embeds the CFF `.otf` only as Type 3 fonts), lays fillable fields over the boxes with
  reportlab, gives the buttons a clean appearance, and merges both with pypdf.

It needs Node with Playwright, Inter in `/usr/share/fonts/opentype/inter` and JetBrains
Mono in `/usr/share/fonts/truetype/jetbrains-mono`.
