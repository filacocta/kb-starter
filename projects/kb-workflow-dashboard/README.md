# KB Workflow Dashboard

Local read-only web dashboard for the user's KB workflow.

The MVP binds to `127.0.0.1`, reads the KB live from disk, and does not mutate markdown or run KB commands. Future write actions should call shared KB tools such as `_tools/kb-checkoff.py`; the dashboard should never edit queue files directly.

## Run

```bash
python3 server.py
```

Open `http://127.0.0.1:8787`.

Optional flags:

```bash
python3 server.py --kb-root /path/to/your/vault/knowledgebase --port 8787
```

## Panels

- Active checkpoint
- Global URL inbox
- Inspiration / ideas backlog
- Topic reading-list queue counts
- Research dive queue counts
- kb-feed SQLite state summary
- Recent KB log entries

