# Compatibility and drift

The archive format (`info` + `messages`) is OpenCode's export format. The parts
that move between OpenCode releases are:

1. the **CLI spelling** (`opencode export` vs `opencode session export`);
2. the **SQLite table/column names** used for enumeration and the offline
   fallback;
3. **message/part types** (new types are warnings, not failures).

Everything version-specific is isolated in a few places so a future change is a
small, testable edit.

## Version matrix

| OpenCode | Session table | Message / part tables | Export command | Status here |
|---|---|---|---|---|
| 1.18.x | `session` | CLI export only | `opencode export <id>` | Not exercised by this repo; vm-continuity tested it |
| 2.0.x | `session` | CLI export only | `opencode session export <id>` | vm-continuity tested it; probes still present |
| 2.4.1 | `session_v2` | `session_message` / `session_part` | `opencode session export <id>` | **Verified** (Linux, Python 3.13) |

The `sqlite` backend reconstructs exports only for the table layout it can map.
If it cannot map a layout, `doctor` reports the missing table/column and `auto`
keeps using the CLI/API.

## 2.4.1 specifics

- Session rows live in `session_v2`; the exported `info.outcome` comes from
  `session_v2.idle_outcome`.
- `session_message.data` is a JSON object. Assistant messages store
  `"content": []` there; the actual parts live in `session_part` and the export
  command assembles them into `messages[].content`.
- `session_v2` has `tokens_input`, `tokens_output`, `tokens_reasoning`,
  `tokens_cache_read`, `tokens_cache_write`; the export nests cache counts under
  `tokens.cache`.
- `opencode session list --format json` is **project-scoped** and top-level-only.
  Enumerating `session_v2` is what includes other projects and child sessions.
- `opencode session export` has a `--sanitize` flag. The HTTP API and raw SQLite
  paths do not; the tool refuses to silently use them when `--sanitize` is set.
- OpenCode has produced truncated exports when stdout was a pipe. `CliSource`
  always redirects export stdout to a real file.

## Code touch points

| Concern | Location |
|---|---|
| CLI export/list spelling and probing | `src/opencode_chat_export/sources/cli.py` |
| SQLite table/column detection | `src/opencode_chat_export/sources/sqlite.py` (`_detect_schema`, `_missing_critical`) |
| Known version → expected schema | `src/opencode_chat_export/doctor.py` (`KNOWN_SCHEMAS`) |
| Export shape/type checks | `src/opencode_chat_export/contract.py` |
| Fixtures | `tests/fixtures/<version>/` and `tools/make_fixture_db.py` |

## Updating for a new OpenCode release

1. Capture the live surface:

   ```bash
   opencode-chat-export doctor --dump-schema /tmp/doctor.json
   ```

2. Add a fixture directory, e.g. `tests/fixtures/v2.5.0/`:
   - `schema.sql` — `CREATE TABLE` statements for `project`, the session table,
     the message table, and the part table;
   - `sessions.json` — synthetic projects/sessions/messages/parts;
   - optionally a recorded `export_sample.json`.

3. Update `KNOWN_SCHEMAS` and, if names moved, the detection code.

4. Add a drift test under `tests/test_schema_drift.py` that renames/drops the
   changed column and asserts `doctor` reports it.

5. Run:

   ```bash
   python3 -m pytest
   python3 scripts/check_upstream.py
   ```

## What "doctor" checks

- OpenCode binary and version (unknown versions warn, not fail).
- Data directory and database presence.
- Session/message/part table detection and critical columns.
- Expected session table for the detected version.
- CLI export command spelling and list output.
- A sample session exported through the normal fallback chain and validated.

`doctor` exits non-zero only for errors. Unknown message types and an untested
OpenCode version are warnings so a new release can still be used to make a
backup before the contract is updated.

## Canary

`scripts/check_upstream.py` is dependency-free and can run in CI or by hand:

```bash
OPENCODE_BIN=opencode python3 scripts/check_upstream.py
```

It checks `--version`, both export spellings, `session list --format json`, and
the live DB tables/columns against the recorded 2.4.1 contract. It exits non-zero
on a missing critical table/column or missing export command; an unknown version
is a warning.
