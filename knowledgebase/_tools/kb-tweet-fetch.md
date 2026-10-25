# Structured social-source inventory

Run `python kb-tweet-fetch.py <approved-locator> --inventory status` (or an
explicitly approved `--inventory thread`) after dedup and approval. This fetches
one bounded endpoint, inventories its full returned channels and performs no
companion/media downloads. The shared publication gate review must inspect substantive
article, quote, continuation, link and media channels; compact output is not the
evidence packet. Missing channels remain explicit.

`kb-fxtwitter-extract.py` inventories a supplied JSON payload offline.
Neither tool proves whole-thread coverage, factual correctness or retained raw
HTTP bytes. No model or API-key route is required. Old `--out` sinks are retired.
See `README.md` and executable `--help` for current bounds and flags.
