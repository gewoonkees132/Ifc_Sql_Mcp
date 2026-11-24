# Security

This project is a local tool: one user loads IFC files into a PostgreSQL database on
their own machine and lets an LLM read them through an MCP server that their MCP client
starts over stdio, and looks at them in 3D with `ifc-sql view`, a page served on
127.0.0.1. This page says what is protected, how, and what is not. The design
behind it is in [ADR 6](docs/adr/0006-mcp-server.md) and
[the read-only role](docs/data-model.md#the-read-only-role).

## Threat model

### SQL written by an LLM

`run_sql` executes SQL that an LLM wrote, which may have been steered by anything in
its context. Four layers stand between that SQL and the data, and each is tested on its
own against PostgreSQL (`tests/integration/test_run_sql.py`):

1. **A validator** (sqlglot) refuses anything but one read-only query on schema `ifc`
   with allowed functions, before a session is opened. It exists for clear refusals and
   depth; it is not the boundary.
2. **The executor** declares a cursor for the query over the extended protocol, which
   PostgreSQL accepts only for one SELECT or VALUES, with no parameters, and fetches at
   most `max_rows + 1` rows.
3. **The transaction** is read-only, has a 5-second statement timeout and a 1-second
   lock timeout set with `SET LOCAL`, runs in a session opened for this call alone, and
   is rolled back.
4. **The privileges of `ifc_reader`**, the boundary: USAGE on schema `ifc` and SELECT on
   its tables and views, SELECT on the migration version table, and SELECT on the two
   tables of schema `ifc_viewer` (below). Beyond that it has only what PostgreSQL gives
   every role (reading the system catalogues, executing most built-in functions, which
   the validator restricts): no other table, no sequence, no file function, not even a
   temporary table. The tests check this with the read-only transaction switched off.

**The viewer's scenes** (schema `ifc_viewer`, [ADR 14](docs/adr/0014-viewer.md)) are
derived data: the triangulated bodies of the elements and spaces, as glTF chunks, which
`ingest --viewer` writes and `ifc-sql view` reads as `ifc_reader`. They hold
nothing the file's model in schema `ifc` does not already say, except the shapes. The
validator keeps `run_sql` to schema `ifc` anyway, for size: one chunk can be megabytes,
and a query that read them would fill a result with hex. `ifc_reader` may not write them,
create anything in the schema, or read a table a later migration adds there without
granting it.

The server holds no other credentials: it connects only as `ifc_reader`, also at
startup.

What an LLM can still do through the server:

- **Read everything in schema `ifc`**, including file names and the errors of failed
  ingest runs. That is the purpose of the server. Not schema `ifc_viewer`: the
  validator refuses it, although `ifc_reader` may read it.
- **Use time and sessions.** Each query can run for up to 5 seconds, and every call
  opens a session; `ifc_reader` allows 20 at once, shared by every server on the
  cluster. There is no rate limit.

### Prompt injection through IFC content

An IFC file is written by whoever authored or edited the model. Its element names,
property values and classification titles, and the finding messages that quote them,
can hold text aimed at an LLM ("ignore your instructions and …"). The server:

- returns such text only as values in data fields, never inside a tool description,
  its instructions, the prompt or a message it composes;
- cuts each value at 2,000 characters, so a file cannot fill the context;
- tells the LLM, in its instructions, in every tool description that returns file
  content and in the briefing prompt, that this text is data and not instructions;
- escapes it in its own logs, so a crafted name cannot forge a log line.

This lowers the risk; it cannot remove it. An LLM reads the text whatever the server
does, and may follow it. What limits the damage is that this server cannot write
anything or reach anything beyond schema `ifc`. The risk that remains is in what else the
conversation can do: a client that also has tools able to act (send mail, write files,
fetch URLs) could be steered into using them. When you analyse models you did not
author, do not enable such tools in the same conversation, and read the tool calls your
client asks you to approve.

The ingest side parses untrusted files too: `ifc-sql ingest` runs IfcOpenShell on the
file, as the owning role. A file crafted to exploit the parser would run with the
owner's credentials. Load files from unknown sources only in an isolated environment.

### Credentials and the public development defaults

- **The defaults are public.** `docker-compose.yml`, `config.py` and CI use the owner
  password `ifc-dev-only` and the reader password `ifc-reader-dev-only`. They are safe
  only because Docker Compose binds PostgreSQL to `127.0.0.1`. Anywhere else, set
  `IFC_SQL_DATABASE_URL` and `IFC_SQL_READER_PASSWORD` (see `.env.example`, and never
  commit `.env`) before the first `make migrate`.
- **The MCP server needs only the reader password.** An MCP client stores its server
  configuration in plain text (`claude_desktop_config.json`, `~/.claude.json`), so that
  is the one secret it should hold. Leave the owner's password out of the URL there: the
  server does not use it. In a shared `.mcp.json`, let Claude Code expand
  `${IFC_SQL_READER_PASSWORD}` from the environment rather than writing it in.
- **Passwords stay out of output.** Connection errors show the URL with the password
  masked (`ifc_reader:***@…`); tool errors carry no URL at all; the logs show only the
  masked URL.
  The migration sends PostgreSQL a SCRAM verifier of the reader password, computed on
  the client, never the password itself.
- **Rotating** the reader password: `ALTER ROLE ifc_reader PASSWORD …` as the owner (or
  `\password ifc_reader` in psql), then update `IFC_SQL_READER_PASSWORD` in each client.

### The viewer's port (`ifc-sql view`)

`ifc-sql view` is the only command that listens on a port, and `serve` keeps none
([ADR 14](docs/adr/0014-viewer.md), decisions 2 and 3; [docs/viewer.md](docs/viewer.md)).
What the port serves is the scenes, `list_models` and `get_element`: read-only, as
`ifc_reader`, in read-only transactions with the MCP server's limits. No route writes, and
no route runs SQL that came from a request: ids reach the database as bind parameters.
Against the other programs on the computer, and the web pages the browser has open:

- **127.0.0.1 only.** It binds the loopback address, and no setting changes that, so no
  other machine can reach it.
- **The Host must name it.** A request is served only when its `Host` is `127.0.0.1:<port>`
  or `localhost:<port>`; anything else gets 421 before any route runs. A web page whose
  name an attacker points at 127.0.0.1 (DNS rebinding) sends its own name as the Host, so
  it reaches nothing.
- **A token on every data request.** `secrets.token_urlsafe(32)`, new on every start,
  compared in constant time. It travels in the URL's fragment (`#t=…`), which the browser
  sends nowhere: not to the server, not in a Referer. The page moves it to
  `sessionStorage` (per tab and per port) and drops it from the address bar; the
  browser's own list of visited pages may still hold the first address, which is of no
  use once `view` stops. The page sends it as `Authorization: Bearer`. Without it, every `/api/` route answers
  401. `/` and `/static/` hold no data and need none. There is no cookie: cookies are
  scoped by host, not port, so one would reach every other server on 127.0.0.1.
- **The token stays out of logs.** It is printed once, on stdout, because the user asked
  for the address; stderr, the log and the access log (switched off) never carry it, and
  a test checks stderr.
- **GET and HEAD only**, and **no CORS headers**: another origin may send a request, but
  it cannot read the answer, and without the token it gets 401 anyway.
- **What the page may do.** Every response carries a content security policy that allows
  scripts only from the server itself plus the page's one inline script, its import map,
  by its SHA-256; styles, pictures and the font only from the server too; no other origin
  for anything, no framing, no forms, no `<base>`: `default-src 'none'; script-src 'self'
  'sha256-<import map>'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src
  'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`. Also
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; the data is
  `no-store`. The page has no inline style and no inline handler, which a test checks.
- **IFC text stays text.** Names and property values come from the file, so the page sets
  them only as text (`textContent`), never as HTML. A test fails if the page's own
  JavaScript uses `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`,
  `srcdoc`, `createContextualFragment`, an `on…` attribute, a `javascript:` URL, `eval`
  or `new Function`.
- **three.js and the font are vendored**: three.js r187 in `vendor/three/`, Nunito
  (`@fontsource-variable/nunito` 5.3.0, two `.woff2` files) in `vendor/nunito/`. The
  page's pictures, its icon and the explainer's still of the Duplex, are its own files in
  `images/`. Each folder pins its files by `SHA256SUMS`, with their licences beside them,
  and tests check the sums; the page loads nothing from the network. Its links to other
  sites (the sources, the licences, GitHub) are `https`, open in a new tab, and send no
  Referer (`rel="noopener noreferrer"`).

What it does not protect against:

- **Another program of the same user.** Anything running as you can read the URL from
  the terminal, or the database directly. With `ifc-sql view` opening the browser, the
  URL is on the browser's command line for a moment; on a computer shared with other
  users, start it with `--no-open` and paste the URL.
- **Anyone with the URL.** Until `view` stops, the URL gives what `list_models` and
  `get_element` give, for every model in the database, and the calls of the feed below.
  Do not paste it into a chat.

### The viewer's feed (`IFC_SQL_VIEWER_FEED`)

With `IFC_SQL_VIEWER_FEED=1`, `ifc-sql serve` sends one PostgreSQL notification per tool
call, which every `ifc-sql view` on the database receives and shows
([ADR 14](docs/adr/0014-viewer.md), decision 4; [docs/viewer.md](docs/viewer.md#the-mcp-servers-calls-the-feed)).
**It is off unless that variable is set**: without it, `serve` sends nothing and opens no
session for it.

- **Any login to the database can read the feed and send to it.** PostgreSQL shows a
  notification to every session that listens on its channel, whatever its role, and lets
  any role send one ("Notifications are visible to all users",
  [NOTIFY](https://www.postgresql.org/docs/16/sql-notify.html)). On the embedded database
  on Windows, any local user of the computer can connect, so any local user can.
- **What it carries**: the tool, its arguments, how the call ended, and for `run_sql` the
  query's text, clipped to what fits in 8,000 bytes. Never text from an IFC file. The
  query carries whatever the conversation put into it, which the log keeps only at
  `DEBUG` (ADR 6, decision 10): with the feed on, whoever listens on the database reads
  it, and PostgreSQL keeps it in its notification queue (`pg_notify/` in the data folder)
  until every session listening has read it.
- **`view` validates every event**: its version, every field's type, the tool, the
  arguments with the tool's own checks, every id and GlobalId, at most 500 ids and 10
  events a second. It never runs SQL that came from an event: what it lights comes from
  fixed queries with the arguments as bind parameters, read-only, as `ifc_reader`. So the
  worst a forged event does is a wrong highlight, or a line in the feed that no MCP
  client sent; the page labels each line with the process that claims to have sent it.
- **What `serve` does for it**: after a call's own transaction has rolled back, one more
  session as `ifc_reader`, one committed `READ ONLY` transaction that only calls
  `pg_notify`. It can write nothing else: the role's privileges still hold.

### The static demo (`ifc-sql demo --export`, GitHub Pages)

`ifc-sql demo --export DIR` writes a copy of the scripted Duplex demo that any static
server can serve, and `.github/workflows/pages.yml` publishes it from `main`
([ADR 14](docs/adr/0014-viewer.md), decision 7;
[docs/viewer.md](docs/viewer.md#the-demo-on-github-pages)). It has no server of ours, no
token and no database: it is files.

- **What it publishes**: the four Duplex models' scenes, their objects' class, name and
  storey, `get_element` of the one wall the script looks up, the script's nine calls as
  the feed resolved them (with the clipped SQL of its two `run_sql` calls), and the page
  with three.js, Nunito and its pictures, the explainer's still of the Duplex among them
  (CC BY 4.0, credited beside it). Of the models, only the script's: another model in the same database is
  left out, whatever it is. The Duplex is CC BY 4.0, and the page credits it
  ([DATA_ATTRIBUTION.md](DATA_ATTRIBUTION.md)). A script whose files' licence does not
  allow a copy has no credit, and `--export` refuses it: the audit's files are never
  exported.
- **What it does not publish**: the database's address (the recorded feed's `database`
  is empty), the token, and any other model's name or file.
- **IFC text stays in JSON values.** The page is `view`'s own, marked static; every file
  name is a fixed name or an id; a test checks both. In the page, the text is set as
  text, as above.
- **What the page may do**: the same content security policy, as a `<meta>` tag, since
  GitHub Pages sends no header of ours; without `frame-ancestors`, which browsers ignore
  in a `<meta>` tag, so the copy can be framed. `no-referrer` is a `<meta>` tag too. Its
  links, the credits' and the explainer's, go to `https` addresses only (a test checks
  the page's).
- **An export never overwrites**: it writes only into a new or empty folder.

### The embedded database

With `IFC_SQL_DATA_DIR`, ifc-sql runs PostgreSQL itself, in that folder
([ADR 13](docs/adr/0013-embedded-postgresql.md)). It is the same PostgreSQL, with the same
read-only role and transactions for the MCP server, but **no passwords**: the cluster
trusts every local connection.

- **Linux and macOS:** it listens on a Unix socket in the data folder, which only its
  owner can open. If the folder's path is too long for a socket, the socket goes under
  `XDG_RUNTIME_DIR` (private to the user) or, without it, the temporary directory, where
  other local users may reach it. Keep the path short.
- **Windows:** it listens on a free port of 127.0.0.1. Any local user of the computer can
  connect, as the database superuser.

Use it on a computer only you use. On a shared one, or for a team, run PostgreSQL in
Docker or as a service, with your own passwords (above).

### What is not protected

- **No authentication.** The server trusts whoever starts it. It speaks only stdio, to
  the process that launched it; it has no network transport. (`ifc-sql view`, above, is
  a separate command, with a token and no MCP.) Do not put it behind a
  stdio-to-HTTP bridge: that would hand the database to anyone who can reach the port.
- **One role per cluster.** PostgreSQL roles are cluster-wide, so every database
  migrated on the same server shares `ifc_reader` and its password.
- **No TLS and no encryption at rest** are configured: the database is on localhost.
- **Availability** within the limits above: no quotas beyond the timeouts, the row and
  size caps and the role's connection limit.
- **The validator alone.** It is defence in depth. Anything it misses must still pass
  the executor, the read-only transaction and the privileges, and the tests show each of
  them refusing on its own.

Dependencies are pinned in `uv.lock`, the CI actions by commit SHA, and Dependabot
proposes updates.

## Reporting a problem

Please do not describe a vulnerability in a public issue. If the repository's
**Security** tab offers **Report a vulnerability**, use it; otherwise open an issue that
only says you have found a security problem, and a private channel will be arranged.
Include what you ran, what happened and what you expected. Only the latest commit on
`main` is supported.
