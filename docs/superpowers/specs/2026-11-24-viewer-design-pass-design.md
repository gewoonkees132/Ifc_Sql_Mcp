# The viewer's design pass: Kees's style, "Ask Claude", and a page that explains itself

**Date:** 2026-11-24 · **Status:** approved by Kees, 2026-11-24 · **Mockups:**
[`2026-11-24-viewer-design-pass/`](2026-11-24-viewer-design-pass/) (open the HTML files in a
browser; the second one is clickable). Where a mockup and this document differ, this
document wins.

## Intent

The page of `ifc-sql view` is also the public demo on GitHub Pages: the same files, byte for
byte (ADR 14, decision 7). Today it reads as a developer's tool. A visitor lands on "ifc-sql
view", a sidebar of setup controls, and lines such as "run_sql: 402 characters of SQL" or
"218 drawn, 0 without shape, 50 openings not drawn". Nothing says what the tool is for, and
the demo plays its nine questions by itself, one every 5 s.

This pass makes the page:

- **work first, explain on request.** The model and the question lead. A "?" button explains
  what the tool does and how it is built.
- **built around asking.** An "Ask Claude" box with a history of questions. In the demo it
  offers the nine preset questions; later, in the local app, it will take typed questions
  for Claude Code (project 2, not this one).
- **Kees's style.** It uses the look of his CV site
  (<https://github.com/gewoonkees132/gewoonkees132/tree/main/cv>): Nunito, the teal
  `#0E8192`, soft greys, tinted pills and calm motion.
- **usable on a phone.**

It is for architects: people who export IFC from Revit and want answers from their models.
It also has to work for anyone who opens the demo link, for example from Kees's CV or
LinkedIn.

## Goals

1. One page design for both the local viewer and the GitHub Pages demo.
2. The 3D model fills the screen. The current question and its answer sit in a card over it.
3. "Ask Claude": a button that grows into a panel with the question box and the history.
4. In the demo, you pick a question. Nothing plays by itself.
5. A "?" explainer with two tabs: **What it does** (the value, in plain words) and **How
   it's built** (a diagram from a Revit export to the page, then one real wall followed
   through it).
6. A Model panel for project, disciplines, storeys and rooms.
7. Plain wording everywhere. The technical detail, such as the tool and the SQL, stays one
   click away.
8. A phone layout: the model on top, a sheet at the bottom with the answer and the box.

## Non-goals

- **Asking Claude from the page** (project 2, below). In this pass the local app's box tells
  you how to ask in Claude Desktop or Claude Code.
- **Preset questions in the plain local app.** Locally you ask Claude. Only the demo, and
  the scripted demo, have presets.
- **Opening the local viewer to a phone on the network.** It stays on 127.0.0.1. "Works on a
  phone" means the public demo, plus the same layout locally in a narrow window.
- **Any change to the MCP server, the database, the ingest or the data-quality rules.**
- **A front-end framework or a build step.**
- **Dark mode.** The CV site is light only.

## Anti-goals: this fails, even if it works, when…

- **the demo suggests Claude answers there.** It doesn't: the answers were recorded through
  the real tools by a script. The demo has to say so wherever "Ask Claude" appears.
- **a splash screen or intro stands between the visitor and the model.**
- **anything plays by itself.**
- **the query behind an answer is hidden.** "How it was answered" keeps it one click away.
- **the copy claims something the code doesn't do.** Every number and claim is real and
  checked (see Verification).

## Constraints from the project

- **The page is plain ES modules and CSS, with three.js vendored.** ADR 14 rejected a build
  step (npm, a bundler). The page's files are served by `ifc-sql view` and copied by
  `ifc-sql demo --export`.
- **The security policy** (`viewer/security.py: content_security_policy`):
  - It reads `default-src 'none'`, `style-src 'self'`, `img-src 'self'`, and
    `script-src 'self'` plus the hash of the one inline script, the import map.
  - The page may not have inline styles or inline handlers (`tests/unit/viewer/test_page.py`).
  - Its JavaScript may never turn text into markup: `textContent` only, no `innerHTML`.
  - So the font and every picture must be files of the page. CSS custom properties set
    from JS (`style.setProperty`) remain fine.
- **There are no fonts today.** Nunito (SIL Open Font Licence) is vendored under
  `static/vendor/`, with its licence and SHA-256 sums like three.js, and the policy gains
  `font-src 'self'`. The static copy writes the same policy into a `<meta>` tag (`export.py`).
- **IFC text from the database stays text.** Names such as
  `Basic Wall:Interior - Furring (152 mm Stud):190774` are set with `textContent`.
- **The demo's credit stays visible.** The Duplex is CC BY 4.0 (`DATA_ATTRIBUTION.md`).
- **Discipline tints stay**: ARC in its material colours, MEP `#2f7ed8`, ELE `#e0a100`,
  PLU `#16a085`. The colours of an answer's groups come from the server, as today.

## Approaches considered

**Layout.** Two layouts were mocked up:

- **A:** an "Ask" panel beside the model, which Claude recommended.
- **B:** cards floating over a full-screen model.

Kees picked B, then refined it:

- On desktop the answer card and the ask control are separate boxes.
- The ask control is a button at the bottom left.
- Opening it merges the two into one panel; closing it splits them again.
- On a phone, the box sits at the bottom of a sheet, with the history above it.

**How it is built.** Three options were considered:

- **Rework the page in place (picked).** It is the only one that fits: one page serves
  both the local app and the demo, as the docs promise.
- **A framework such as React or Vue.** It brings back the build step ADR 14 rejected.
- **A separate page for the demo.** It breaks "the demo is `view`'s page", and the two pages
  would drift apart.

## The design

### Look

Take the tokens from the CV's `style.css` (section 1):

- **Colour:**
  - Background `#ffffff`, surface `#f7f8f9` / `#fafbfc`, hover `#f0f2f4`.
  - Text `#1a1f24`, secondary text `#586069`, muted text `#6b7480`.
  - Teal `#0E8192` (hover `#0a6473`), with `#0a6b7a` for small teal text so it passes contrast.
  - Teal tints `rgba(14,129,146,.08)` and `.15`.
  - Borders `#d1d5da`, `#e1e4e8`, `#f0f3f6`.
  - The canvas grey stays `#eef0f3`.
- **Type:** Nunito 400/500/600/700, base 15–16 px. Use the CV's fluid sizes where they fit.
- **Shape:**
  - Radii: 6 px for pills and inputs, 12–14 px for cards, 16 px for the explainer.
  - The CV's soft shadows (`--shadow-sm` to `--shadow-2xl`).
- **Patterns from the CV:**
  - Active tabs and pills use a teal tint with teal text (`.filter-pill[aria-pressed]`).
  - The active item has a 4 px teal bar (`.main-nav a.active::before`).
  - The explainer is shaped like the CV's project modal: picture hero, stats row, sections.
  - Focus rings are 4 px, `rgba(14,129,146,.2)`.
- **Brand:** a simple cube mark beside "ifc-sql", not Kees's K logo. This is Claude's call;
  see the list below.
- **The clicked element in 3D** turns teal instead of orange (`scene.js` `ACCENT`). Check in
  the browser that it stands apart from PLU's green `#16a085`. If it doesn't, use a darker
  teal or another colour that does. It must be told apart.

### Desktop

See mockups 2 and 3.

- **The model fills the window.** Everything else floats over it.
- **Top left: the answer card.**
  - The brand row: the cube mark, "ifc-sql", "Ask questions of your building models",
    and the "?" button.
  - The current question, with its number and `‹ 3/9 ›` arrows in the demo.
  - The answer in words first: a big line ("23 walls") and a sentence ("are external, of
    four types").
  - The legend of its groups, each with swatch, name and count. Clicking a group shows only
    that group.
  - "▸ How it was answered", which opens how the answer was found. This is the tool's name
    and its arguments in plain words for every call, plus the SQL for a `run_sql` call
    (the only call whose SQL the feed carries, ADR 14 decision 4). The collapsed line
    reads "1 SQL query, read-only" for `run_sql`, and the tool's title plus "read-only"
    for the others ("Find elements, read-only").
    - *Claude's call:* option (a) of the builder check. Showing the fixed tools' own SQL
      would need a new data path from `server/tools.py` through `view` and the export;
      that could come later.
- **Top right: "☰ Model"**, which opens the Model panel.
- **Bottom centre: the view pill.** "Fit all", "Fit answer", "Only the answer". The labels
  follow what is lit: after a click on an element they read "Fit element" and "Only this
  element". Both are disabled when nothing is lit.
- **Bottom left: "✦ Ask Claude"**, a teal pill.
- **Bottom right, in the demo only:** a small credit line ("Duplex Apartment ·
  buildingSMART · CC BY 4.0", linked).

### Ask Claude: one panel that changes shape

See mockup 2, the clickable one.

- **Closed:** the answer card at the top left and the Ask Claude button at the bottom left.
- **Open:**
  - The card grows down to meet the button, and the button becomes the question box.
  - The history slides in above the answer, newest at the bottom, like a chat.
  - The header's second line reads "Ask Claude · 3 of 9 asked" (demo) or the feed's state
    (local app).
  - The building moves a little to the right to make room.
- **Closing:** ×, Esc, or the button again splits them back.
- **Picking a question keeps the panel open.** The answer is in the panel, beside the model.
- **The history:**
  - One row per question, with its number, the question, and the answer in one line.
  - Clicking a row lights its answer again, as a feed row does today.
  - The current one is marked with the teal bar.
- **The box:**
  - A field ("Ask about this model…"), "☰ 9 questions" to open the list, and a send button.
  - Above it, "Next:" suggestions of questions not yet asked.
  - Below it, in the demo: "Recorded answers · Claude doesn't run on this page."
- **Motion:**
  - About 0.5 s on the CV's curve, `cubic-bezier(0.16, 1, 0.3, 1)`.
  - With `prefers-reduced-motion: reduce` it switches at once.

How the box behaves depends on where the page runs:

| Where | The list | Typing | History | Header line |
|---|---|---|---|---|
| **Demo** (static copy) | The nine questions; asked ones marked "✓ asked" | Filters the nine; a question outside them gets "Want to ask your own? Install ifc-sql and ask Claude about your own models. How it works ›" | The questions you picked | "Ask Claude · n of 9 asked" |
| **Scripted demo** (`ifc-sql demo`, live) | The script's questions; those not yet asked show "coming up" and can't be picked | As the demo | Fills as the script plays, at its own `--pause` | "Scripted demo · no LLM" |
| **Local app** (`ifc-sql view`) | None | The box is disabled and reads "Ask Claude in Claude Desktop or Claude Code", with "How to connect ›" opening the explainer's Connect box | Claude's calls, from the feed | The feed's state in words: "Listening for Claude" (live), "Connecting…", "Connection lost, retrying" (interrupted), "Off" with how to turn it on |

**What the local history can and can't show.** The server sees Claude's tool calls, not the
words you typed into Claude Desktop. So a local row reads as a plain sentence from the call,
for example "Found walls on Level 1", with the tool name muted. It does not show your own
question. Project 2 fixes this, since the page will then hold the question itself.

**The demo on open.** It shows question 1, "What is loaded?", answered in the card, and
nothing plays. "What is loaded?" lights nothing and shows all four disciplines.

**The answer card in the local app.** Locally the page has neither your question nor
Claude's answer, only the call: its tool, its arguments, `selection.count` and the group
labels.

- **The question line** is the call's plain sentence, the same one the history row uses.
- **The big line** is the count, with a noun when every lit object is of one class
  ("21 walls"). Otherwise it reads "21 found".
- **There is no answer sentence.**
- **The legend and "How it was answered"** work as in the demo.
- **Before the first call,** the card shows its brand row and one line: "No question yet.
  Ask Claude in Claude Desktop or Claude Code, and what it finds lights up here."
- *Claude's call.*

### The answer card for a click

A click on an element lights it teal and opens Element details on the right.

- A click replaces an answer's highlight (`select()` clears the call).
- The card then shows that its answer is no longer lit, with "Light it again".
- Esc clears the selection, as today.
- A call that zooms to one object, such as question 8, opens its details as today.

### The Model panel

See mockup 3. It floats on the right, top to bottom, and has ×.

- **Project:** a select. Disabled when there is only one project.
- **Disciplines:** one row per discipline, with a swatch, its name in words ("Architecture",
  "Mechanical", "Electrical", "Plumbing"), and a switch to show or hide it.
  - The second line gives the element count and its colour in words: "in their material
    colours", "tinted blue".
  - This replaces the "ARC own colours / MEP tinted" chips.
  - **Under each discipline, one muted line per model:** its file name and element count
    (`Duplex_A_20110907.ifc · 268 elements`).
  - **A model without a scene** keeps today's hint on its line: "No 3D yet:" and the
    command from `scene_hint` (`main.js` today). A discipline whose models all lack a
    scene still gets its row, with its switch disabled. Then a user who loaded a file
    without `--viewer` sees why nothing is drawn. The "nothing to draw" status points
    to the Model panel.
  - *Claude's call:* the mockup left this out. Today's sidebar has a Models section, and
    dropping it would hide that hint.
- **Storeys:** a checkbox per storey, with its name as the model has it (`T/FDN` stays
  `T/FDN`), its elevation ("−1.25 m") and its element count. "Show all" sits in the heading.
- **Rooms:** a switch, "See-through volumes of each room".

### Element details

`details.js` keeps its content, restyled. It opens as a floating panel on the right, in the
Model panel's style. The two share the right side: opening one closes the other. Its header
shows what the old caption showed: class · discipline, name, storey and room.

### The "?" explainer

See mockups 3 and 4. It is a modal dialog shaped like the CV's project pop-up: a picture
hero, then two tabs.

**Hero.** "Ask your building models", over a still of the Duplex. The still is a file
shipped with the page, credited where it is shown.

**Tab 1, What it does.** This is the approved draft. Check every claim against the README
while building.

- **Stats:**
  - **11** tools Claude can use.
  - **45** data-quality checks.
  - **0** changes to your models.
- **How it works:**
  1. **Load:** "Your Revit IFC exports go into a database as they are. Mixed units and
     packed names are untangled on the way in."
  2. **Ask:** "Ask Claude in your own words. It answers with read-only queries, so it can't
     change a model."
  3. **See:** "Each answer lights up here: the walls, doors or rooms it found, and how it
     found them."
- **Why it helps:**
  - **Answers you can check.** "Every answer shows how it was found: the tool Claude used,
    its settings, and the SQL whenever Claude wrote its own."
    - *Claude's call:* reworded from the approved draft ("Every answer shows the query that
      produced it"). Only `run_sql` calls carry SQL, so the old line overclaimed.
  - **Problems surface early.** "45 checks flag missing fire ratings, rooms counted twice
    and placeholder values Revit leaves behind."
  - **Your models stay on your computer.** "Claude sees only the results of the queries it
    runs."
- **Last box, demo version.** "About this demo. The Duplex Apartment: four models
  (architecture, mechanical, electrical, plumbing) by buildingSMART, CC BY 4.0 (linked).
  Nine questions, answered by the real tools and recorded. Claude doesn't run on this page."
  - Buttons: **Try it on your own models →** (README, Quick start) and **View on GitHub**.
- **Last box, local version.** "Connect Claude. Add ifc-sql to Claude Desktop or Claude Code
  once. Keep this page open and ask Claude there: whatever it looks up lights up here."
  - Buttons: **Set up Claude Desktop** and **Set up Claude Code**, linking to `docs/mcp.md`.

**Tab 2, How it's built.** A diagram in three rows:

1. **Row 1:** `1 IFC export (Revit)` → `2 Read (IfcOpenShell)` → `3 Clean up`.
2. **Row 2:** a wide bar, `4 Store (PostgreSQL 16)`.
3. **Row 3:** `5 Check (45 rules)` (both ways), `6 Claude asks (MCP)` and `7 You see
   (three.js)`.
   - The arrows to 6 and 7 are labelled "read-only".
   - A dashed teal arrow from 6 to 7 has the key "Each call Claude makes lights up on this
     page".

The sentence in each box:

| Step | Text |
|---|---|
| 1 | "Your model as Revit exports it: valid, but with mixed units and packed names." |
| 2 | "Every element is read with its properties and materials; its shape becomes 3D." |
| 3 | "Units become metres; names split into family, type and Revit id. What doesn't fit is logged." |
| 4 | "One model in one go: elements, properties, quantities and materials in their own tables, and the 3D per storey, ready to draw." |
| 5 | "Flags gaps like missing fire ratings or rooms counted twice. Every run is kept." |
| 6 | "Through 11 read-only tools. A query that would change data is refused." |
| 7 | "This page draws the stored 3D and lights up what each answer found." |

Below the diagram, "Follow one wall" traces the wall of question 8:

- **In the IFC file:** `Basic Wall:Interior - Furring (152 mm Stud):190774`.
- **After clean-up:** family **Basic Wall** · type **Interior - Furring (152 mm Stud)** ·
  Revit id **190774**. Check this against the stored row: `revit_family`, `revit_type`,
  `revit_element_id`.
- **In the demo:** "Question 8 shows everything stored about it."

Then the "Built with" chips (Python, IfcOpenShell, PostgreSQL 16, MCP, three.js) and "The full
design on GitHub →" (README, Architecture).

In the demo the dashed arrow is shown as a recording, since nothing is live there.

### Phone, below about 760 px wide

See mockup 1: its phone frame and its notes. The desktop frame in that file was superseded
by mockup 2.

- **Top:** a floating pill with the cube mark, "ifc-sql", "☰ Model" and "?".
- **Middle:** the model, with a round "fit" button above the sheet.
- **Bottom sheet, peeking:** the current answer (question, big line, legend as chips, "How
  it was answered") and the Ask Claude box at the very bottom, in thumb reach.
  - Dragging it up shows the history ("▴ 2 earlier questions").
- **The Model panel, Element details and the explainer** open as sheets too. The diagram
  becomes a vertical timeline: 1 to 4, then 5, 6 and 7 (mockup 4).
- **Touch targets** are at least 44 px.

### Wording

Old to new:

| Today | New |
|---|---|
| ifc-sql view / The Duplex, step by step | ifc-sql · Ask questions of your building models |
| fit / fit selection / only selection | Fit all / Fit answer (Fit element) / Only the answer (Only this element) |
| Feed · off / live / recorded | Ask Claude · the state in words (table above) |
| `run_sql: 402 characters of SQL` | How it was answered · 1 SQL query, read-only (another tool: "Find elements, read-only") |
| `23 found · 23 shown · 0 without shape` | 23 walls, are external, of four types (counts of what isn't drawn, under "How it was answered") |
| ARC own colours / MEP tinted | Architecture · in their material colours / Mechanical · tinted blue |
| Scripted demo, no LLM (serve 11652, started 22:01:40) | Recorded answers · Claude doesn't run on this page |

The Duplex questions become questions, each with a one-line answer. The figures are those
the script already checks:

| # | Question | Answer |
|---|---|---|
| 1 | What is loaded? | 4 models, 1,795 elements |
| 2 | Which walls are on Level 1? | 21 walls, in the architectural model |
| 3 | Which walls are external, by type? | 23 walls are external, of four types |
| 4 | What does the door schedule say? | 14 doors of 4 types; every fire rating is the placeholder "Fire Rating" |
| 5 | Which rooms are there, and how big? | 21 rooms, 422.0 m² in all |
| 6 | Are any rooms counted twice? | Yes: 21 of the 42 spaces are MEP spaces over a room |
| 7 | What lacks a fire rating? | 78 walls, doors and slabs |
| 8 | What is stored about one wall? | Everything about one wall: its properties, materials and findings |
| 9 | Can it change the model? | No: a delete is refused before it reaches the database |

### Accessibility

Aim for WCAG 2.1 AA.

- **Contrast:** teal `#0E8192` on white is about 4.6:1. Use `#0a6b7a` for small teal text.
- **Keyboard:**
  - Everything is reachable.
  - The Ask Claude button has `aria-expanded`.
  - The explainer is a modal dialog: focus is kept inside, Esc closes it, and focus returns
    to "?".
  - The Model panel and Element details are labelled regions that Esc closes.
- **Screen readers:** a new answer is announced with `aria-live="polite"`.
- **Motion:** reduced motion turns every transition off.
- **Autoplay:** nothing plays by itself, which also settles WCAG 2.2.2.

### Loading and errors

- **Loading** shows in the model area, as today's status text in plain words, for example
  "Drawing the Duplex… 12 of 24 parts".
- **Errors** become a toast at the top centre (`role="alert"`), in place of the sidebar
  footer and `#message`.

## Where it touches the code

- `viewer/static/`:
  - `index.html`: the new structure. Keep the import map as the only inline script, and
    keep the hash test passing.
  - `viewer.css`: rewritten on the tokens above.
  - `main.js`: the sidebar logic becomes the card, the panels and the pill.
  - `feed.js`: becomes the history, with plain sentences per tool.
  - `details.js`: restyled.
  - `scene.js`: the `ACCENT` colour.
  - `api.js`: the static source hands over all recorded calls at once, and lights none by
    itself.
  - `vendor/`: Nunito and its licence; `SHA256SUMS`.
  - A still of the Duplex for the hero.
- `viewer/security.py`: `font-src 'self'`. The tests that pin the policy string, and
  `export.py`'s `<meta>` copy, follow.
- `viewer/http.py` and the export: serve and copy `.woff2` with its media type
  (`test_every_file_of_the_page_has_a_media_type`). Also check that the wheel includes the
  new files (`test_wheel.py`).
- `viewer/demo_script.py`:
  - The Duplex steps' titles become the questions above.
  - Each step gets its one-line answer, a new field beside `figure`, or `figure` rewritten.
    This is the builder's call; the answer must agree with `expected`, which
    `--check`/`--export` already verify.
  - The audit script's four titles may be rephrased the same way, also the builder's call.
- `export.py`: the static page no longer paces calls. Decide whether `pause_seconds` stays
  in `events.json`; it is still used by the live demo.
- **Docs:**
  - `docs/viewer.md`: the page, the feed, "The demo on GitHub Pages" (which today says calls
    play "one every `--pause` seconds"), and the steps table.
  - README: screenshot `docs/images/demo-duplex.png` and its caption;
    `docs/images/viewer-duplex.png`.
  - ADR 14: a new decision for the page's design, and decision 7's playback amended.
  - `SECURITY.md`, wherever it quotes the policy.

## Verification

- **Tests:**
  - The policy string, with `font-src 'self'`, in both the header and the `<meta>` copy.
  - The vendored files' SHA-256 sums, Nunito included.
  - Media types; the wheel's contents.
  - The Duplex titles and answers.
  - A new test that the explainer's numbers match the code: the server's tool count (11)
    and the rule catalogue (45). This way the copy can't drift.
- **In a browser:**
  - Check the live `ifc-sql view` and the exported static copy, at 1440×900 and 390×844.
  - Check with the keyboard alone, and with reduced motion on.
  - Open, merge, split, pick, re-light from the history, click an element, open the Model
    panel and the explainer, and resize between the layouts.
  - Kees's tip for this machine: the T3 preview freezes `requestAnimationFrame` when hidden,
    so measure in Edge over CDP, started from Bash.
- **Screenshots:** take new ones for the README and `docs/viewer.md`, with the browser,
  commit and date in the captions, as the docs do today.
- **CI:** `pages.yml` and the `samples` job export the copy unchanged. The artifact
  `duplex-demo-site` is the preview before publishing.

## Decided by Kees

- One design for the local app and the demo.
- Functionality first; the explanation sits behind a "?" button.
- Phones matter.
- Layout B: floating over a full-screen model. On desktop the answer card and the ask
  control stay separate, the button sits bottom left, and they merge into one panel when
  open, "dancing together".
- The ask control is named **Ask Claude**.
- A history of questions.
- The demo has preset questions to pick, with no autoplay.
- A question box exists now, filled with the presets, so it is clear how asking will work.
- The explainer covers the value, how it works, and how it is built, with a clear diagram
  from the Revit IFC export to the tool.
- The look follows his CV site.
- Order: this design pass first, the Claude Code box second.

## Calls Claude made (correct any)

- The demo opens on question 1, answered.
- Picking a question keeps the panel open.
- The clicked element is teal, if it stands apart from PLU's green.
- The labels of the view pill follow what is lit.
- Element details share the right side with the Model panel.
- A credit line sits at the bottom right in the demo.
- The cube mark is used instead of the K logo, and neither Kees's logo nor his name appears
  on the page. Swapping in the logo later is one SVG.
- The questions and answers are worded as in the table.
- All explainer copy, as drafted. Since the review, "the query behind it" reads "how it was
  found": only `run_sql` calls carry SQL.
- "How it was answered" shows the tool and its arguments, plus the SQL for `run_sql`.
- The Model panel lists each model under its discipline, with the "No 3D yet" hint.
- In the local app the answer card is built from the call.
- The phone breakpoint is about 760 px.
- Errors appear as a toast.
- The hero uses a Duplex still.

### While building (2026-11-24)

Where the build differs from this document, or decides what it left open:

- **An untrue answer made true.** Question 7's approved answer, "78 walls, doors and
  slabs", was untrue: the 78 are 57 walls and 21 slabs, and the 14 doors hold the
  placeholder "Fire Rating", which `missing_fire_rating` does not count. The answer and
  the figure now say "78 walls and slabs".
- **`answer` and `headline` beside `figure`** (`Step`, `DemoStep`), not `figure`
  rewritten: `--check` keeps citing the figures the docs cite. `pause_seconds` left
  `events.json`: the static copy no longer paces anything. The audit's four titles were
  kept, each given an answer.
- **Shape.** 6 px radius for the tinted pills (tabs, chips), as the CV has them; the
  controls that float over the model (Ask Claude, ☰ Model, the view pill, the round
  buttons) fully round, as the mockups draw them.
- **Wording this document did not give.** Beside the closed Ask Claude button in the
  demo, "Recorded answers" (the anti-goal: say so wherever Ask Claude appears). The
  scripted demo's line below the box, "Asked by ifc-sql demo through the real tools ·
  Claude doesn't run on this page.", and its empty card, "No question yet: the script
  asks its first in a moment." The diagram's key in the demo, "Each call Claude makes
  lights up on this page; in this demo, the calls are recorded." The hero's subtitle per
  tab, from the mockups. The local sentence per tool ("Found walls on Level 1", `words.js`).
  In `view`'s header, "Off" alone; the reason is in the line below the box.
- **What stays lit.** In the demos each question stands alone: "What is loaded?" clears
  the view. In `view`, a call that lights nothing by nature leaves the view and the card
  as they were, as the feed did. A click on a legend group lights that group alone.
- **The history and the answer scroll as one**, like a chat, with the answer's top kept
  in view; two scrollbars side by side read badly.
- **The credit line sits flush in the bottom-right corner**, as a map credits its tiles,
  below the pill's row: wherever the pill moves (the panel open, a side panel open, a
  narrow window), it never covers the credit.
- **Esc closes one thing**: the list of questions; the panel when the focus is in it;
  otherwise the newest of a clicked element, the Model panel, the panel, and what an
  answer lit. The focus goes back to the button or box it came from.
- **A click on nothing** clears a clicked element but leaves an answer lit; today's page
  cleared everything. On a phone a stray tap would otherwise throw the answer away.
- **The fit uses the narrower angle of view**, so the building fits a phone held upright,
  and on a phone the model is drawn above the sheet, so that it is fitted to what can be
  seen of it.
- **On a phone**, the legend's chips are two to a row, 34 px to see and 44 px to touch;
  there is no "Next:" row, as mockup 1 draws it (the list and ‹ › remain); the round
  button is Fit all.
- **The teal stays.** In Edge, a clicked plumbing boiler in `#0e8192` beside its twin in
  the plumbing's tint (`#16a085`, 60 %) is told apart at once: saturated and dark against
  pale sage.
- **The hero still** is a fresh render, not a crop: the page itself, question 3 lit, the
  building set on the canvas's grey at the right of a 1,560 × 300 WebP (9 KB). Its credit
  sits on a dark backing, and the hero's text keeps to the dark side of its gradient, for
  4.9:1 or more. `DATA_ATTRIBUTION.md` now says the repository ships this one picture
  made from the Duplex.
- **Every count of the explainer is tested**, "four models" in "About this demo"
  included; "0 changes to your models" holds because every tool is annotated read-only
  and not destructive.

## Left to the builder

- Exact component structure and file split in `static/`.
- Whether `figure` is rewritten or an `answer` field is added.
- The plain sentence per tool for local history rows.
- Whether `pause_seconds` stays in the export.
- Rephrasing the audit titles.
- The exact breakpoint.
- How the hero still is made: a crop of the existing screenshot, or a fresh render.

## Next: project 2, Ask Claude through Claude Code

This is not part of this pass. It is recorded so the design leaves the right room.

The local box would start the user's own installed, unmodified Claude Code headless with only
ifc-sql's read-only tools. The answer would stream into the panel, and the feed would light
the calls as today. Each user signs in to Claude Code themselves, through Anthropic's own
sign-in.

Anthropic's terms
([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)) allow this:
"an end user … signing in to the unmodified Claude Code binary with their own Claude
subscription". Shipping a product that runs Claude Code requires agreeing to Anthropic's
Commercial Terms. Not allowed:

- a "Log in with Claude" button inside the app;
- the app storing anyone's Claude login;
- using one person's subscription to pay for other people's questions;
- the Agent SDK on a subscription login.

The docs only imply that `claude -p` uses the subscription login: bare mode needs an API
key. Confirm this on the machine first. The public demo can't do this, since each visitor
would need their own Claude Code on a server.
