# Review: the viewer plan

- Date: 2026-11-22
- Reviewed: [`2026-11-ifc-viewer.md`](2026-11-ifc-viewer.md) as of commit `998c887`. It is
  revised in place, and its last section, "Changes after review", lists what changed.
- Core goal, which outranked everything else: **a clear demo on the reference sample
  files**, the four buildingSMART Duplex models and the audit's five Revit 2019–2022
  exports. Generality that does not serve that demo counted as a cost.

## Outcome

The plan knew the codebase. Its architecture was careful, but it would not have met the
core goal:

- the first demo in which the MCP queries light the model up needed a live LLM in a
  hand-configured client;
- its selection rules lit nothing for most of the questions the docs already answer;
- it showed the Duplex one discipline at a time;
- it put a web server inside the stdio MCP process.

The adopted design:

- a scripted demo first (`ifc-sql demo` drives the real server and CI checks it);
- `ifc-sql view` as the only port;
- `serve` publishing each call through PostgreSQL `NOTIFY` and otherwise staying
  stdio-only;
- the Duplex demo exported to GitHub Pages.

It scores 4.15 of 5, against 2.65 for the plan as written.

## Method

1. **Grounding, by the reviewer.** I read the plan, README.md, SECURITY.md, docs/mcp.md,
   ADRs 6, 10, 12 and 13, `server/app.py`, `ingest/geometry.py`, `ingest/pipeline.py`,
   `db/schema.py`, the Makefile and the CI workflow. I then numbered the plan's design
   decisions (D1–D44) and its factual claims (C1–C38); both lists, with their outcomes,
   are at the end.
2. **Eight read-only reviewers in parallel**, each briefed from scratch with the core goal,
   the rubric and the return format:
   1. a fact-check of every claim;
   2. three competing architectures, each built at its strongest and compared with the
      plan: a static demo folder, browser-side IFC parsing (web-ifc), and MCP Apps first;
   3. geometry and storage;
   4. security of the loopback server;
   5. fit, testability and scope;
   6. the demo experience.
3. **Comparison, then an adversarial pass.** Four new reviewers each had one job, to
   refute something:
   - the top-ranked option;
   - the two transports for the live feed;
   - the high findings about the repository;
   - the high findings about external tools.

   Where reviewers contradicted each other, I settled it from the evidence (below).
4. **Four questions to the user** about scope and architecture; nothing the evidence
   settled was asked.

**What could be checked, and how.** Nothing was run. The review machine has no uv, no
Docker, no `.venv` and none of the sample files.

- **The locked wheels** (mcp 2.3.0, starlette 1.10.0, uvicorn 0.54.0, sse-starlette 3.7.0,
  httpx2, psycopg 3.3.9, anyio 4.16.0, ifcopenshell 0.9.0.post1 for Windows and Linux)
  were downloaded, and their SHA-256 matched `uv.lock`. Their source was read, never
  executed.
- **External tools** were checked against their sources at the relevant tag: three.js,
  IfcOpenShell and PostgreSQL REL_16.
- **Primary documents:** RFC 6265, the HTML and MCP Apps specifications, and the
  PostgreSQL, Claude and MDN documentation.
- **Figures:** every figure here that is not quoted from the repository is marked as an
  estimate.

**The rubric.** Demo clarity on the reference samples 30 %, fit with the architecture and
conventions 20 %, security and operational risk 15 %, effort and time to the first
convincing demo 15 %, testability and CI on Linux and Windows 10 %, room to grow 10 %.

Five reviewers proposed moving 5 points from "room to grow" to "effort", and one proposed
moving them to "security". The ranking below is the same under all three weightings.

## Comparison

Scores are 1 to 5. The plan's and the alternatives' scores are the reviewers', settled by
me where they disagreed. "Hybrid" is the design adopted after the user's answers.

| Option | Demo 30 % | Fit 20 % | Security 15 % | Effort 15 % | Test 10 % | Grow 10 % | **Weighted** | Effort → +5 | Security → +5 |
|---|---|---|---|---|---|---|---|---|---|
| Plan as written | 2 | 3 | 3 | 2 | 3 | 4 | **2.65** | 2.55 | 2.60 |
| Plan with the surviving fixes, same order | 3 | 4 | 3 | 2 | 4 | 4 | **3.25** | 3.15 | 3.20 |
| Static demo folder | 4 | 4 | 4 | 3 | 4 | 3 | **3.75** | 3.75 | 3.80 |
| Browser-side IFC (web-ifc) | 2 | 2 | 3 | 4 | 2 | 2 | **2.45** | 2.55 | 2.50 |
| MCP Apps first | 3 | 4 | 4 | 3 | 3 | 3 | **3.35** | 3.35 | 3.40 |
| **Hybrid** (adopted) | 5 | 4 | 4 | 3 | 4 | 4 | **4.15** | 4.10 | 4.15 |

The evidence behind each row:

### Plan as written

- **Demo 2.**
  - No feed without an LLM (plan:436-437).
  - Nothing selected for `schedule`, `quantity_takeoff` or `compare_models` (plan:223).
  - In the six recorded Claude sessions, 12 of 29 calls are aggregate `run_sql` and none
    is `find_elements` (`docs/audit/evidence/recheck/llm_sessions.md`), so 4 of the 29
    would light anything.
  - One model at a time (plan:463-465).
- **Fit 3.** Separate schema and pure writer, but it misses the amendments to ADR 1,
  ADR 13 and ADR 6 decision 10, `include_name` (`db/migrate.py:41`), the privilege and
  migration pins, and the 85 % gate.
- **Security 3.**
  - The token cookie is shared by every port (RFC 6265 §8.5).
  - A taken port calls `sys.exit(3)` inside the MCP process (`uvicorn/server.py:184-192`).
  - The token goes into the transcript.
- **Effort 2.** The first lit-up demo comes after a spike and three PRs, the riskiest
  piece (uvicorn inside `serve`) among them.
- **Test 3.** TestClient is ruled out on a false premise; the Windows job measures no
  coverage (`ci.yml:130-180`).
- **Grow 4.** The `api.js` seam, the SSE feed and stored origins.

### Plan with the surviving fixes, same order

- Selections, disciplines, BatchedMesh and the token are fixed.
- But it still needs an LLM for the first lit-up demo, and it still has a port inside
  `serve`.

### Static demo folder

- **Demo 4.** Anyone sees it without installing. But it is a replay only, and only the
  Duplex may be published (licences below).
- **Fit 4.** No port and no ADR 6 change, but it adds a Pages job and an attribution
  change.
- **Effort 3.** It still needs Phase 1's scene builder and a recorder; the "one PR"
  estimate was refuted.
- **Grow 3.** Nothing is live.

### Browser-side IFC (web-ifc 0.0.80)

- **Demo 2.**
  - It parses on every open: an estimated tens of seconds on B and E.
  - Files B and E are web-ifc's own bug reports ([#98](https://github.com/ThatOpen/engine_web-ifc/issues/98):
    malformed windows; [#68](https://github.com/ThatOpen/engine_web-ifc/issues/68): a bad
    boolean), closed before the 0.79 CSG rewrite.
  - Its shapes could disagree with the IfcOpenShell quantities.
- **Fit 2.** It keeps the file's bytes, against the "no path or bytes" rule
  (`db/schema.py:200-208`), and adds a second kernel beside ADR 10's.
- **Security 3.** It needs `'wasm-unsafe-eval'` and 7.9 MB of vendored MPL-2.0 code.
- **Effort 4.** No writer and no migration.
- **Test 2.** The kernel bridge is JavaScript that pytest cannot cover.

### MCP Apps first

- **Demo 3.**
  - It works inline in Claude Desktop only: claude.ai cannot reach a local stdio server
    ([Claude: build an MCP server](https://claude.com/docs/connectors/building)), and
    Claude Code renders nothing.
  - The view gets notifications only for the call that opened it.
- **Fit 4, Security 4.** No port at all, and ADR 6 decision 1 holds.
- **Effort 3, Test 3.** Host limits are unknown (about 150,000 characters per tool
  result, documented), and there is no host in CI.

### Hybrid (adopted)

- **Demo 5.**
  - A reproducible, CI-checked scripted demo on the four Duplex disciplines together,
    plus a local audit script.
  - One page that shows every client's calls.
  - A Pages copy for readers.
- **Fit 4.**
  - ADR 6 decision 1 is unchanged, because `serve` stays stdio-only.
  - Decision 2 gains one committed READ ONLY transaction that only notifies.
  - ADR 1 and ADR 13 are amended.
- **Security 4.**
  - The port is in `view` only.
  - No cookie, and the token is never in the conversation.
  - The feed can be read by any database login, which is opt-in and documented.
- **Effort 3.** The same work as the plan, reordered, with the risky part after the
  first demo.
- **Test 4.** Pure selections, `demo --check` in the `samples` and `windows` jobs, and
  TestClient in-process.
- **Grow 4.** The live feed from any client, and the static export.

## Findings that survived

Severity is the one that held after the adversarial pass. "→" names where the revised
plan deals with it.

### High

1. **No feed without an LLM.**
   - The plan's first demo in which queries light the model up was a live Claude Code
     session (plan:436-437).
   - `scripts/mcp_smoke.py:48-86` shows the SDK's client can drive `serve` itself.
   - It was rated critical, then lowered: Phase 2 already gave an LLM-free 3D view, so
     only the feed was missing.
   - → decision 5 and Phase 3, `ifc-sql demo`.
2. **The selection rules lit little.**
   - Nothing for `schedule`, `quantity_takeoff` or `compare_models` (plan:223).
   - The README's flagship query aggregates with GROUP BY (README:167-173).
   - `data_quality_report` samples are the first findings by object id
     (`server/tools.py:1510`), 3 by default (`tools.py:39`).
   - Space pages hold at most 150 spaces (`tools.py:35`), not "up to 500" (plan:219).
   - The recorded sessions favour aggregates (12 of 29 calls).
   - → decision 4's table, the `object_ids` array column and the description sentence.
3. **Openings and aggregates.**
   - `list_storeys` counts every element on a storey, openings included
     (`tools.py:262-265`; ARC has 50 of 268, `tests/samples/test_duplex_mcp.py:37`).
   - The database stores no link from a part to its aggregate (`db/schema.py:469-551`;
     `host_global_id` is used only to resolve the storey, `ingest/transform.py:386-390`).
   - Whether Revit's `IfcCurtainWall` and `IfcStair` carry a body of their own is
     unverified. A hint that they do not: neither is measured (`geometry.py:46-60`).
   - → decision 1: the scene's own counts, parts nested under their aggregate, a stair
     in the fixture, and a Phase 0 count.
4. **The token cookie is shared by every port on 127.0.0.1.**
   - RFC 6265 §8.5: "Cookies do not provide isolation by port".
   - The HTML specification's same-site check ignores the port.
   - Two viewers overwrite each other's cookie, other local servers receive the token,
     and `__Host-` does not isolate ports either.
   - The plan claimed "two clients never collide" (plan:168).
   - → decision 3: the token goes in the fragment and a Bearer header, with `?t=` only on
     the SSE route.
5. **File B needs at least about 11,300 draw calls with one Mesh per primitive.**
   - GLTFLoader makes one Mesh per primitive (r170 `GLTFLoader.js:3778-3797`), and B has
     11,298 building elements (`llm_sessions.md:149`).
   - BatchedMesh's `setColorAt` arrived in r165 and `addInstance` in r166. Code written
     for r165 breaks on r166, so "r159 or later" (plan:248) was too loose.
   - At r170 the per-instance colour is RGB only (`three.module.js:13998, 14026`), so
     ghosting at 15 % per instance is not one draw call.
   - → decision 6: BatchedMesh from the start, opaque grey ghosting, r171 or later.
6. **The coverage gate is 85 %, not 100 %.**
   - `pyproject.toml:133` against README:187 and plan:279.
   - Child processes are not measured, and the windows job runs no coverage.
   - → Phase 1's first commit, and in-process tests.
7. **Phase 0 and the sample pins covered three and one of the nine reference files**
   (plan:287-289, 339).
   - → Phase 0 and the Phase 1 pins cover all nine.
8. **The listener inside `serve` had runtime gaps.**
   - A bind failure calls `sys.exit(STARTUP_FAILURE)`, which is 3 (`uvicorn/server.py:184-192`;
     `config.py:81`).
   - Nothing stops uvicorn when stdio ends unless `should_exit` is set.
   - `timeout_graceful_shutdown` defaults to None (`config.py:236`).
   - uvicorn captures SIGINT and SIGTERM on the main thread (`server.py:332-349`).
   - A taken fixed port would end the MCP server too.
   - Narrowed: sse-starlette's `EventSourceResponse` ends its streams within 0.5 s once
     `should_exit` is set (`sse.py:94-150`).
   - → no web server in `serve`. `view` uses a pre-bound socket, `lifespan="off"` and a
     2-second graceful shutdown.

### Medium

9. **One model at a time loses the Duplex.**
   - The four files share `project_group` and storey elevations
     (`docs/data-model.md:335-358`).
   - Whether they line up in plan is unverified.
   - → decision 6 shows the project's models together; Phase 0 checks the alignment.
10. **Duplicate spaces and Revit Areas would overlap as translucent boxes.**
    - The 21 copies on Duplex MEP and 75 of 163 spaces on file C
      (`docs/data-quality.md:165`).
    - 25 Areas spaces of 22,905.9 m² on file E (`docs/mcp.md:416-418`).
    - → the `space_copies` and `areas` chunk kinds.
11. **Starlette's TestClient runs on the locked httpx2** (`starlette/testclient.py:25-44`;
    httpx2 2.16.2 in `uv.lock:464-465`). The plan's premise that it needs httpx was
    false.
    - → in-process route tests, plus one real-socket test.
12. **Tests and settings the plan did not list.**
    - `include_name` admits only `ifc` (`db/migrate.py:41-49`).
    - The constraint test reads constraints in `ifc` only (`tests/integration/test_migrations.py:157-175`).
    - One default ACL is pinned (`:121-124`), and so is the revision list (`:72-92`).
    - The privilege test pins what the reader may read outside `ifc`
      (`tests/integration/test_privileges.py`).
    - SECURITY.md:26-31 would become false in PR 1.
    - → the Phase 1 file list and tests.
13. **The documented sample paths built no scene.**
    - `Makefile:67-71` has no `--viewer`, and `docs/windows.md` never downloads the
      samples.
    - Partly refuted: the plan had a 404 hint and an in-place build.
    - → `make ingest-samples --viewer` and `scripts/demo.py`.
14. **Model ids depend on the load order.** The docs' figures come from three layouts:
    README:58-61, `docs/mcp.md:569-571` and `docs/audit/2026-11-recheck.md:80`.
    - → the demo finds models by SHA-256 and pins counts, never ids.
15. **Decision 7 rested on wrong or dated facts about MCP Apps.**
    - claude.ai cannot use a local stdio server.
    - A view may call app-only tools (`apps-2026-01-26.mdx:398-402`).
    - `connectDomains` may name localhost (ext-apps `docs/csp-cors.md:11`), but the host
      may restrict it (`apps.mdx:286`), and the request would need CORS.
    - → out of this plan, with the corrected notes.
16. **Ids in selections are untrusted.**
    - `run_sql` can alias any text as `global_id`.
    - With NOTIFY, any login can forge an event.
    - → `view` validates every event against `GLOBAL_ID_PATTERN` (`db/schema.py:71`)
      and the tools' own limits.
17. **The 20-session limit is shared**, and the viewer's database work draws on the same
    thread pool as the MCP tools. Sync tools run in `anyio.to_thread`
    (`mcp/server/mcpserver/utilities/func_metadata.py:155-163`); the limit is in
    `0003:110`.
    - → `CapacityLimiter(3)`.
18. **Licences for anything published.**
    - The Duplex is CC BY 4.0. It requires the changes to be stated, and
      `DATA_ATTRIBUTION.md` says "Changes made: none".
    - IfcSampleFiles (files A, C and D) states no licence.
    - B is a user's upload to a web-ifc issue.
    - → Duplex only, with the attribution updated.
19. **Missing ADR amendments.**
    - ADR 1 decides on one schema.
    - ADR 13 decision 3 lists the commands that attach to the embedded database.
    - ADR 6 decision 10 is affected, because the page shows SQL text.
    - → the plan's header and Phases 1–3.
20. **Pre-commit and the vendored files.**
    - `check-added-large-files --maxkb=500` and the whitespace fixers would block or
      rewrite three.js: `three.module.js` is 1,314,681 bytes at r170.
    - From r172 the minified module build is split, 339,437 + 366,322 bytes, each under
      the limit.
    - Partly refuted: the hooks run only locally, and they fail loudly.
    - → the exclude, and minified r171 or later.

### Low

21. The file list missed `ingest/extract.py`: only `read_model` opens the file
    (`extract.py:139-167`).
    - `_triangulated` yields shapes in thread order (`geometry.py:138-147`).
    - Migration 0019 only adds columns (`0019:35-108`).
    - The mypy comment in `pyproject.toml:97-100` is stale.
22. `scene_chunk.storey_id` should be deferrable, as `_within_model` is
    (`db/schema.py:164-173`). "Fail halfway" was refuted: the DELETE fails whole.
23. `Cache-Control: no-store` on `/api` made the chunks' ETag useless (RFC 9111 §5.2.2.5).
24. The vendored file list missed `three.core.js` and whatever GLTFLoader imports at the
    pinned release.
25. "A colour per class where the file has none" is already done by IfcOpenShell's
    `apply-default-materials`, which is on by default.
26. IfcOpenShell's glTF serializer is compiled into the locked wheel, but:
    - it writes only to a file path (`geom/main.py:718, 743-745`);
    - it ignores `y-up` (`conversion_settings.h:707-709`; `gltf_serializer.cpp:224-235`);
    - it has no `extras`.

    So the plan's own writer stands, and Phase 0 no longer compares the two.
27. ADR 14 did not name browser-side parsing as a rejected alternative.
28. SECURITY.md says the validator "is not the boundary" (SECURITY.md:15-18). For
    `ifc_viewer`, the validator alone keeps `run_sql` off it; the data is derived, and
    SECURITY.md now says so.
29. Migration 0020 makes `serve` refuse an un-migrated database (`cli.py:149-157, 446`).
    This is routine; migrations 0010 to 0019 did the same. The plan needed an upgrade
    note.
30. Calls the SDK refuses never reach `_Calls.tool`, so the feed misses them.
31. `sse-starlette` and `anyio` were not direct dependencies; `anyio` is a dev dependency
    only (`pyproject.toml:36`).

### Findings that shaped the NOTIFY design

The transport refuter found these, and they shaped decision 4.

- **NOTIFY in a read-only transaction.** It is allowed (REL_16 `utility.c`,
  `ClassifyUtilityCommandAsReadOnly`), and `pg_notify` checks only for recovery
  (`async.c:605-606`).
- **The payload limit.** A payload of 8,000 bytes or more raises an error
  (`async.c:653-656`).
- **Delivery and order.** Delivery happens on commit, in commit order. Notifications are
  per database and per channel (`async.c:28-33`).
- **Who can listen and send.** Listening and sending need no privilege: "Notifications
  are visible to all users" ([NOTIFY](https://www.postgresql.org/docs/16/sql-notify.html)).
  The LLM cannot use either: `run_sql` refuses LISTEN, NOTIFY and `pg_notify`
  (`tests/unit/server/test_sql_guard.py:154-155, 240`).
- **Storage and cost.** The queue is disk-based, under `pg_notify/` (`async.c:20-23`), so
  the plan's "never written to disk" no longer holds. Each notify assigns a transaction id
  (`async.c:931-937`).
- **psycopg.** `Connection.notifies(timeout=…)` holds the connection lock
  (`psycopg/connection.py:372-435`). `AsyncConnection` refuses Windows' Proactor loop
  (`connection_async.py:101-109`), so the listener is a sync thread with autocommit.
  `ifc_reader` has `idle_in_transaction_session_timeout` 30 s (`0003:47`).

## Refuted or narrowed

- **A browser opened by `serve` writes into the JSON-RPC stdout** (security and fit
  reviewers). Refuted: while serving, mcp 2.3.0's `stdio_server` points fd 1 at stderr
  and fd 0 at the null device, "so handlers and children read EOF and their stray output
  misses the wire" (`mcp/server/stdio.py:163-169`). On Windows, `webbrowser` uses
  `os.startfile`. It is moot anyway: `serve` no longer opens a browser.
- **"NOTIFY cannot work, because every serve transaction rolls back"** (refuter of the
  top option). Refuted: the notify runs in its own committed READ ONLY transaction, which
  PostgreSQL allows (above).
- **"`view` would have to re-run the LLM's SQL to resolve a `run_sql` selection."**
  Refuted: `serve` sends the ids from the rows it already holds.
- **"`pg_notify` broadcasts to every `ifc_reader` session"** (security reviewer). Wrong
  as stated: a notification goes only to sessions listening on that channel in that
  database. It survives in a narrower form: any login can listen.
- **"The hybrid is less work than the plan"** (my first scoring). Refuted by the refuter
  of the top option: it is the plan's Phases 1–3 plus the demo, reordered. Its effort
  score fell from 4 to 3, and its weighted score from 4.3 to 4.15.
- **"An open EventSource always hangs shutdown."** Narrowed: with `EventSourceResponse`
  and uvicorn on the main thread, streams end within 0.5 s of `should_exit`.
- **"The MCP Apps specification says nothing about localhost"** (MCP Apps reviewer).
  Refuted by ext-apps `docs/csp-cors.md:11`, which says to declare it. Whether Claude
  Desktop honours it is unverified.
- **"There is no downgrade command."** Partly refuted: `uv run alembic downgrade 0019`
  works with the IFC_SQL_* variables (`pyproject.toml:45-49`;
  `tests/integration/test_migrations.py:186-197`).
- **"The scene's foreign key makes a model DELETE fail halfway."** Refuted: the DELETE is
  atomic and would fail whole; the plan's own test would catch it.
- **"An SDK stdio client and uvicorn in one process fail on Windows."** The attack
  failed: `mcp_smoke.py` already runs the same client in the windows job
  (`ci.yml:176-177`).
- **"The static demo must open from `file://`, which pins three.js r147."** Narrowed: the
  export is served by Pages or `python -m http.server`, so modern ES modules stay.
  `file://` is out of scope.
- **"Files B and E may be published under MPL-2.0"** (refuter of the top option). Not
  adopted: B is a user's upload to an issue, with no licence statement.

## Contradictions, and how they were settled

1. **NOTIFY.** One refuter called it unworkable; the other found it workable.
   - Settled for workable, on the PostgreSQL source and documentation above.
   - Kept from the first refuter: the payload limit and the queue on disk.
2. **MCP Apps and localhost.** One reviewer said the iframe can fetch from localhost;
   another, that the specification is silent.
   - The ext-apps documentation allows declaring it; the host may restrict it.
   - Unverified for Claude Desktop, so both routes stay open if MCP Apps is ever spiked.
3. **The plan's demo score**, given as 2, 3 and 4 by different reviewers.
   - Settled at 2: no LLM-free feed, the selections in plan:223, and 4 of the 29 recorded
     calls lit.
   - The 4s came from reviewers comparing the plan with a weaker alternative, not from
     counter-evidence.
4. **Static folder first, or the hybrid?**
   - The hybrid: a scripted demo against the real server covers the audit files locally
     and is checked in CI.
   - The static export comes right after it, since the recorded events are its data.
5. **What may be published.** Duplex only (above).
6. **Shutdown.** Narrowed as above. It applies to `view` only, now that `serve` has no
   web server.
7. **Where scenes live.** One reviewer would build them in memory when `view` starts, with
   no migration; another would keep them in PostgreSQL.
   - PostgreSQL. With `--geometry`, B's 8,285 shapes took 54 s and 59.5 s
     (`docs/audit/2026-11-implementation.md:617`; `docs/audit/evidence/figures_after.md:53`),
     so building on every start would cost about a minute on B.
   - Migrations are routine here (finding 29).
8. **BatchedMesh now, or after a measurement?** Now: GLTFLoader's one Mesh per primitive
   gives the draw-call floor from code, and writing the highlighting twice is avoided.
9. **A feed in `view` through the database.** The security reviewer judged every IPC
   option worse, through the "broadcast" claim; the fit reviewer favoured NOTIFY.
   - Settled by the transport refuter's verification.
   - What remains, that any login can listen, was put to the user as a trade-off.

## Decisions, and who made them

**By the user** (step 4; each chose the recommended option):

| Question | Choice |
|---|---|
| Order | The scripted demo first, the live feed from the user's own client after. |
| Live feed | NOTIFY into `view`; `serve` stays stdio-only, and there is no `show_in_viewer`. |
| Publishing | GitHub Pages, Duplex only. |
| Optional parts | Keep the Playwright browser test; drop picking and the MCP Apps spike. |

**By the reviewer, from the evidence** (not asked):

- **Scenes and storage.**
  - Scenes stay in PostgreSQL, in `ifc_viewer`, with explicit grants and a deferrable
    foreign key.
  - Our own GLB writer; IfcOpenShell's serializer is not compared again.
- **What the page shows.**
  - The current models of a project are shown together.
  - Copies and Areas get their own chunk kinds.
  - Parts are nested under their aggregates, and the scene keeps its own counts.
  - BatchedMesh from the start, three.js r171 or later, opaque ghosting, and the storeys
    above a selection hidden.
- **Security.** The token goes in the URL fragment and a Bearer header, with no cookie.
- **Pipeline and checks.**
  - `--viewer` stays opt-in, and every demo path passes it.
  - Phase 0 covers all nine reference files.
  - `fail_under = 100` first.
  - TestClient in-process.
- **The feed.**
  - Selections are resolved in `view` from the arguments, and from ids `serve` sends for
    `run_sql` and `get_element`.
  - The scripted demo publishes through the same NOTIFY path as a live client, so there
    is one path to test. The refuter of the top option had flagged two publishing paths
    as a cost.
  - The notify runs in its own short session. That leaves `reader_transaction`, a safety
    layer, unchanged, at the cost of one session per call with the feed on.

## The plan's decisions, before and after

| | Decision in the original plan | After review |
|---|---|---|
| D1 | Scenes built at ingest and stored | kept |
| D2 | `--viewer` opt-in | kept; the demo paths pass it |
| D3 | Migration 0020, `scene` and `scene_chunk`, default privileges | changed: explicit grants, a deferrable foreign key, `opening_count`, four chunk kinds |
| D4 | `ifc_viewer` hidden from `run_sql` and `describe_schema` | kept; SECURITY.md states why |
| D5 | GLB, Y-up, float32 relative to a per-model origin | kept; no NORMAL |
| D6 | A node per element, extras `{ifc_class, object_id}`, a colour per class | changed: no `object_id`; parts nested; class colours left to IfcOpenShell |
| D7 | Every non-feature IfcElement; spaces hidden | changed: copies and Areas apart |
| D8 | Chunks by storey, split at a cap | kept; Phase 0 sets the cap |
| D9 | Our own writer | kept; the serializer comparison dropped |
| D10 | Build after commit, in place, format_version, failure leaves no row, watch | kept, plus the extract flag and sorting; no automatic rebuild |
| D11 | Not an ingestion_run | kept |
| D12 | No new dq rule | kept; counts include openings |
| D13 | One app run by `view` and inside `serve` | changed: `view` (and `demo`) only |
| D14 | A port and token per server process | changed: per `view` start |
| D15 | Handlers in `to_thread`, `reader_transaction` | kept, plus `CapacityLimiter(3)` |
| D16 | 127.0.0.1 only | kept |
| D17 | Host check, maybe the SDK's middleware | changed: our own, exact port, 421 |
| D18 | Token cookie through a redirect | changed: fragment, Bearer, `?t=` on SSE |
| D19 | GET only, a POST in Phase 4 | changed: GET and HEAD only |
| D20 | CSP with an import-map hash, headers | changed: hash computed, `img-src 'self'`, chunk caching |
| D21 | `textContent`, a grep test | kept; the list extended |
| D22 | Token in the transcript; `IFC_SQL_VIEWER_OPEN` | dropped |
| D23 | Events in memory only | changed: in `view`'s memory; the notification queue passes through `pg_notify/` |
| D24 | Feed deque, SSE polling, Last-Event-ID | kept, in `view`, with its own numbering |
| D25 | `_Calls` publishes after every call | changed: through `pg_notify`, opt-in |
| D26 | Summary from arguments only | kept |
| D27 | Selections per tool | changed: resolved in `view` for every filtered tool |
| D28 | A sentence in `run_sql`'s description | changed: names `object_ids` |
| D29 | `show_in_viewer` | dropped; still 11 tools |
| D30 | ES modules, `api.js` as the seam | kept; two sources, live and static |
| D31 | three.js vendored, r159 or later, fetch script | changed: committed, r171 or later, minified, no script |
| D32 | Accent colour, 15 % ghosting | changed: colours per group, opaque grey, storeys above hidden |
| D33 | Measure, then BatchedMesh | changed: BatchedMesh from the start |
| D34 | MCP Apps spike | dropped from the plan (user) |
| D35 | Phases 0–5 | changed: 0–6, demo first |
| D36 | starlette and uvicorn as direct dependencies | changed: plus sse-starlette and anyio |
| D37 | Real uvicorn and `http.client` for every test | changed: TestClient, plus one real socket |
| D38 | Playwright | kept (Phase 6) |
| D39 | `IFC_SQL_VIEWER_PORT`, `IFC_SQL_VIEWER_OPEN` | changed: `IFC_SQL_VIEWER_FEED`; `view --port` |
| D40 | ADR 14, ADR 6 decision 1 amended | changed: ADR 1, ADR 6 decisions 2 and 10, ADR 13 amended; ADR 6 decision 1 not |
| D41 | One model at a time | changed: the project's models together |
| D42 | A new port and token every start | kept, for `view` |
| D43 | A viewer smoke test in `check` and `windows` | changed: `demo --check` in `samples` and `windows`; a `view` smoke step in `windows` |
| D44 | Routes | changed: no cookie route; `/api/feed` added |

## The plan's claims, checked

| | Claim | Verdict | Evidence |
|---|---|---|---|
| C1 | `model` keeps no path or bytes | confirmed | `db/schema.py:200-208` |
| C2 | ADR 6 and SECURITY.md: stdio only | confirmed | ADR 6 decision 1; SECURITY.md:108-110 |
| C3 | stdout carries only JSON-RPC | confirmed | `test_serve_stdio.py`; the SDK also diverts fd 1 (`mcp/server/stdio.py:163-169`) |
| C4 | 0003 sets default privileges on `ifc` | confirmed | `0003_create_reader_role.py:131` |
| C5 | `ifc_viewer` hidden from `run_sql` and `describe_schema` | confirmed | `sql_guard.py:180-183`; `catalogue.py:41-43` |
| C6 | Only extract and geometry touch IfcOpenShell | confirmed | `geometry.py:27`; the mypy comment is stale |
| C7 | `_triangulated(world=True)` exists | confirmed | `geometry.py:132-147` |
| C8 | Every tool call goes through `_Calls.tool` | wrong at the edges | calls the SDK refuses never reach it; resources use `_Calls.resource` (`app.py:350`) |
| C9 | GlobalId unique per model | confirmed | `db/schema.py:408` |
| C10 | starlette 1.10.0, uvicorn 0.54.0, sse-starlette 3.7.0 come with mcp | confirmed | `uv.lock:912-930`, `1910-1934`, `2000-2006` |
| C11 | `MCPServer.run_stdio_async()` | confirmed | `mcp/server/mcpserver/server.py:1074-1081` |
| C12 | A scene costs about what `--geometry` costs | unverifiable; likely more | `--geometry` triangulates only the measured classes (`geometry.py:46-60`) |
| C13 | 100 % branch coverage | wrong as a gate | `fail_under = 85` (`pyproject.toml:133`) |
| C14 | Openings are cut by default | confirmed | IfcOpenShell `conversion_settings.h:265-267` at tag `ifcopenshell-python-0.9.0post1` |
| C15 | A chunk holds what `list_storeys` counts | wrong | openings are counted (`tools.py:262-265`) |
| C16 | The serializer exists in the wheel | confirmed | the `ifcopenshell_geometry_glb` plugin in both wheels; it ignores `y-up` |
| C17 | `_measure_in_place` locks the model row | confirmed | `pipeline.py:274-279` |
| C18 | `reader_transaction` with limits | confirmed | `db/engine.py:70-98` |
| C19 | The SDK's `TransportSecurityMiddleware` can be reused | confirmed, with caveats | a `validate_request` helper, off by default, any port allowed (`mcp/server/transport_security.py:43-123`) |
| C20 | Tools pass only `log_fields` | confirmed | 2 of 11 pass any (`app.py:639, 670-671`) |
| C21 | A shareable WHERE builder | confirmed | inline code, not a function (`tools.py:333-355`) |
| C22 | r159 or later has per-element colour and visibility | wrong | `setColorAt` r165, `addInstance` r166 (`BatchedMesh.js` at those tags) |
| C23 | `mcp.server.apps.Apps` in the locked SDK | confirmed | `mcp/server/apps.py` |
| C24 | Geometry must come through the host | partly wrong | `connectDomains` (ext-apps `docs/csp-cors.md:11`), subject to the host and CORS |
| C25 | A terminal client cannot show MCP Apps | confirmed | [Claude Code: MCP](https://code.claude.com/docs/en/mcp) |
| C26 | TestClient needs httpx | wrong | it imports httpx2 first (`starlette/testclient.py:25-44`) |
| C27 | `log_config=None`, `access_log=False` keep stdout clean | confirmed | `uvicorn/config.py:390-429` |
| C28 | 20 connections; 5 s timeout | confirmed | `0003:110`; ADR 6 decision 2 |
| C29 | Bodies that fail are not fatal | confirmed | `geometry.py:88-95` |
| C30 | The geometry fixture's contents | confirmed | `tests/support/geometry_model.py`; no aggregate, door or material |
| C31 | Races staged with blocked transactions | confirmed | `tests/integration/test_ingest.py:248-285` |
| C32 | 0019 as the template | wrong | it only adds columns (`0019:35-108`) |
| C33 | The windows job covers `serve`'s task group | wrong | no coverage there (`ci.yml:130-180`) |
| C34 | Throwaway databases; PostgreSQL 16 in CI | confirmed | `tests/conftest.py`; `ci.yml:22-24` |
| C35 | 11 tools today | confirmed | `app.py:1`; `test_serve_stdio.py:51` |
| C36 | The `claude mcp add` syntax | confirmed | [Claude Code: MCP](https://code.claude.com/docs/en/mcp); `docs/mcp.md:86` |
| C37 | Tool calls do not block the event loop | confirmed | sync tools run in `anyio.to_thread.run_sync` (`func_metadata.py:155-163`) |
| C38 | The Windows setup commands | confirmed as written; not run | `ci.yml:149-166`; `docs/windows.md` |

Claims the first list missed:

- "The viewer stops when stdin closes" (plan:166-167) was wrong as written.
- "Two clients never collide" (plan:168) was wrong.
- `no-store` contradicted the ETag.
- The space cap was 150, not 500.
- The vendored file list was incomplete.
- A taken port exits 3, not 1.

## What could not be verified

Nothing was run, so none of these is known:

- triangulation times, triangle counts, GLB sizes, frame rates or browser memory on any
  reference file;
- whether the four Duplex models line up in plan;
- which Revit aggregates carry a body of their own;
- the iterator's output order;
- whether IfcOpenShell shares geometry between mapped items.

Also unverified:

- How Chrome, Firefox and Safari treat a CSP hash on an inline import map. The HTML
  specification says it applies.
- Whether Claude Desktop honours a localhost `connectDomains` entry, and its size limit
  for app-only results.
- That the `?t=` on the SSE URL never reaches a log the user keeps. The access log is off
  and the request is not a navigation; the test in Phase 2 is the check.
- Whether pydantic refuses lone surrogates in arguments, which would otherwise make
  encoding an event fail. Phase 3's worst-case test should include one.
