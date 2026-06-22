# KB Workflow Dashboard

Local read-only web dashboard for the user's KB workflow.

The MVP binds to `127.0.0.1`, reads the KB live from disk, and does not mutate markdown or run KB commands. Future write actions should call shared KB tools such as `_tools/kb-checkoff.py`; the dashboard should never edit queue files directly.

## Run

```bash
python3 server.py
```

Open `http://127.0.0.1:8787`.

The dashboard header links to `http://127.0.0.1:8787/getting-started.html`, which explains the KB workflow, safe future operations, external skill-artifact review, and source-cluster-to-skill promotion path.

Optional flags:

```bash
python3 server.py --kb-root /path/to/vault/knowledgebase --port 8787
```

The server refuses non-loopback hosts unless `--allow-non-loopback` is set. Keep the default unless you deliberately want to expose local KB metadata to another machine.

## API

- `GET /api/status` returns dashboard summaries, short queue previews, and kb-feed read-only state.
- `GET /api/queue?path=<relative-kb-path>` returns full checkbox items for an allowlisted queue path only.

## Panels

- Active checkpoint
- Global URL inbox
- Inspiration / ideas backlog as a parking lot, not an ingestion queue
- Topic reading-list queue counts
- Generated research dive view counts
- Needs Attention triage cards for failed, active, review, open, stale, and kb-feed signals
- Queue item previews with source path and line numbers
- `[?]` testing/review checkbox rows counted separately from blocked rows
- Topic queue filters and sortable topic/project tables
- kb-feed SQLite state summary
- Recent KB log entries
- Light/dark theme toggle

## Future Write Actions

Keep the dashboard read-only until a guarded command runner exists. First candidates are lint, generated dive-queue regeneration, locked queue checkoff, path helpers, and server start/stop after showing port ownership. External skill artifacts should stay as scratch-reviewed candidates until package validation, KB comparison, and task testing are complete.
