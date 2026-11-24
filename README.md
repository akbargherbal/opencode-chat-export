# opencode-chat-export

Export every OpenCode chat on this machine into **one portable zip** of JSON
files, plus human-readable Markdown, an index, and a manifest. Designed for
**ephemeral VMs**: run it once before shutdown, download the zip, and read or
re-import the sessions later without OpenCode or the original VM.

- **JSON first.** `sessions/*.json` is the same shape as
  `opencode session export <id>` — lossless and re-importable.
- **All sessions, all projects.** It enumerates the database, so child/subagent
  sessions and sessions from other project directories are included (the CLI's
  `session list` is project-scoped and top-level-only).
- **Tested against OpenCode 2.4.1**, with a `doctor` command and a canary script
  so a future schema/CLI change is visible instead of silently producing a
  truncated archive.
- **No runtime dependencies.** Python 3.9+ standard library only.

## Quick start

```bash
git clone https://github.com/akbargherbal/opencode-chat-export.git
cd opencode-chat-export

# One command: every session, every project, with Markdown transcripts.
./bin/opencode-chat-export export -o /content/opencode-chats.zip

# Check the archive before downloading it.
./bin/opencode-chat-export validate /content/opencode-chats.zip
```

Download `/content/opencode-chats.zip` before the VM stops. To install it as a
command instead:

```bash
python3 -m pip install --user .
opencode-chat-export export -o ~/opencode-chats.zip
```

`opencode-chat-export` with no arguments is the same as `export`. A bare session
id is shorthand for `--session <id>`.

> OpenCode's TUI `/export` writes **one** session. This tool automates all of
> them, uses the CLI's `opencode session export` as the authoritative serializer,
> and packages the results for offline use.

## What is in the zip

```
opencode-chats/
  README.md                 provenance + parsing examples
  manifest.json             OpenCode version, backends, checksums, warnings
  index.json                machine-readable catalog
  index.md                  human-readable catalog with links
  schema/                   JSON Schemas for the export/manifest/index
  sessions/
    20261124-fix-widget-...-ses_abc.json    lossless session export
    20261124-fix-widget-...-ses_abc.md      readable transcript
  db/opencode.db            optional consistent DB snapshot (--include-db)
```

The Markdown transcript includes user messages, assistant text, reasoning
(collapsed), and tool calls with their inputs/outputs. Embedded file bytes
(`files[].data`, often base64) are deliberately left out of Markdown; they remain
in the JSON.

## Read it offline

```python
import json
import zipfile

with zipfile.ZipFile("opencode-chats.zip") as z:
    root = next(
        name.split("/")[0]
        for name in z.namelist()
        if name.endswith("/manifest.json")
    )
    index = json.load(z.open(f"{root}/index.json"))
    for session in index["sessions"]:
        doc = json.load(z.open(f"{root}/{session['file']}"))
        print(session["created"], session["title"], len(doc["messages"]))
```

`index.md` is readable in any Markdown viewer and links directly to each
transcript.

## Commands

| Command | Purpose |
|---|---|
| `export` | Create the zip (default command) |
| `list` | List sessions the tool can see |
| `doctor` | Diagnose OpenCode version, tables/columns, CLI command, and a sample export |
| `validate` | Validate a JSON export or an archive zip (shape + checksums + index) |
| `version` | Print the tool version |

Useful `export` options:

| Option | Meaning |
|---|---|
| `-o, --out PATH` | Output zip (default `opencode-chats-<timestamp>.zip`) |
| `--session ID` | Export selected session(s); repeatable |
| `--directory PATH` | Only sessions under this project/directory |
| `--since`, `--until` | Filter by creation date (`YYYY-MM-DD` or ISO-8601) |
| `--top-level-only` | Exclude child/subagent sessions |
| `--no-transcripts` | JSON only, no Markdown |
| `--include-db` | Also store a consistent `opencode.db` snapshot |
| `--sanitize` | Use OpenCode's own redaction (CLI backend only) |
| `--backend auto\|cli\|api\|sqlite` | Force a source (default `auto`) |
| `--allow-empty` | Create an archive even if no sessions match |
| `--force` | Overwrite an existing output file |
| `--deterministic` | Reproducible archive timestamps (`SOURCE_DATE_EPOCH`) |
| `--json` | Machine-readable summary on stdout |

`list`, `doctor`, and `validate` also accept `--json`.

## How it works

1. **Snapshot** the live `opencode.db` with `sqlite3.Connection.backup()` (never
   copy a live WAL database file-by-file).
2. **Enumerate** every session id from the snapshot. This is what catches child
   sessions and other projects.
3. **Fetch** each session with `opencode session export <id>`, writing stdout to
   a file. OpenCode has produced truncated output when stdout was a pipe, so the
   file redirect is intentional.
4. **Validate** the document. If the CLI returns a broken/truncated document, try
   the HTTP API, then the SQLite snapshot.
5. **Package** JSON, Markdown, index, manifest, and schemas into the zip.

### Backends

| Backend | Role | Notes |
|---|---|---|
| `cli` | Authoritative fetch | Uses `opencode session export`; supports `--sanitize` |
| `api` | Fallback fetch | `opencode api get /api/experimental/session/<id>/export` |
| `sqlite` | Enumeration + offline fallback | Reconstructs the export from `session_v2` / `session_message` / `session_part` |

`auto` enumerates from SQLite, fetches from the CLI, and falls back to the API
and then SQLite. A structurally invalid document is never written; the next
source is tried.

## Compatibility and drift

Verified on **OpenCode 2.4.1** (Linux):

| Probe | 2.4.1 |
|---|---|
| Session table | `session_v2` |
| Message/part tables | `session_message` / `session_part` |
| Export command | `opencode session export <id>` |
| List command | `opencode session list --format json` |
| `info.outcome` source | `session_v2.idle_outcome` |

Run this after upgrading OpenCode:

```bash
opencode-chat-export doctor
```

It reports the binary/version, data directory, table/column detection, CLI
command spelling, session counts, and a sample export. Exit code is non-zero only
for problems that would block a usable archive; unknown versions and unknown
message types are warnings.

For CI or a scheduled check, use the dependency-free canary:

```bash
python3 scripts/check_upstream.py
```

It checks the CLI surface and the live DB schema against the recorded 2.4.1
contract. See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for the version
matrix and the exact code touch points when OpenCode changes.

### Relationship to `vm-continuity`

An earlier tool, [vm-continuity](https://github.com/akbargherbal/vm-continuity),
captures per-session exports and ships them to GCS. It was tested against
OpenCode 1.18.x/2.0.x; its probes still work on 2.4.1, but it has no zip, index,
or transcript and is built around a GCS backup loop.

This repo is the focused successor for the **download-one-zip / read-offline**
use case. The `sessions/*.json` files it writes are the same format
vm-continuity captures, so they can still be imported with
`opencode session import`. If you need GCS shipping or DB restore, keep using
vm-continuity.

## Development

```bash
python3 -m pytest
```

The test suite uses:

- a checked-in **schema + synthetic session fixture** (`tests/fixtures/v2.4.1/`)
  that builds a real SQLite DB at test time;
- a **fake `opencode` executable** for CLI/API contract tests;
- **drift tests** that rename/drop columns and assert `doctor` reports the
  breakage;
- deterministic-archive, checksum, and tampered-archive tests.

Regenerate the fixture database manually if useful:

```bash
python3 tools/make_fixture_db.py
```

When OpenCode changes:

1. Run `opencode-chat-export doctor --dump-schema /tmp/doctor.json`.
2. Add/update a fixture under `tests/fixtures/<version>/` (schema + sessions).
3. Update `KNOWN_SCHEMAS` in `doctor.py` and, if table/column names moved,
   `_detect_schema()` in `sources/sqlite.py`.
4. Add a test that pins the new behavior, then run the suite.

## Security

Archives are **not encrypted**. They can contain prompts, source code, tool
output, file contents, and credentials that appeared in a transcript. Store and
transfer them accordingly. `--sanitize` delegates to OpenCode's redaction and is
only available through the CLI backend; the tool never silently substitutes a
raw database/API export when sanitization was requested.

## Limitations

- The CLI/API backends require an OpenCode install. The SQLite fallback can work
  from a copied database alone, but its reconstruction targets the 2.4.x schema.
- `opencode session list` is project-scoped; if the database is unavailable and
  only the CLI is usable, enumeration is limited to the current project.
- The API endpoint used as a fallback is marked experimental upstream.
- Every run writes a full archive; there is no incremental/delta mode.

## License

MIT. See [LICENSE](LICENSE).
