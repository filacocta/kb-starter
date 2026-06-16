# kb-tweet-fetch.py

Fetch structured tweet data from the unauthenticated fxtwitter API for KB ingestion.

```bash
_tools/.venv/bin/python _tools/kb-tweet-fetch.py "https://x.com/user/status/123" --json --out /tmp/tweet.md
```

The JSON includes:

- `main`: the requested tweet with author, preferred raw text, photos, videos, and timestamp.
- `quote`: the full quoted tweet in the same compact shape, when present.
- `thread`: an ordered root-to-given self-reply chain found by walking upward through `replying_to_status`.
- `flags`: thread-start detection and a forward-walk note when the requested tweet appears to start a thread.
- `video_urls`: de-duplicated video URLs across the main tweet, quoted tweet, and thread.

Use `--max-thread` to cap upward thread hops. The default is `30`.
