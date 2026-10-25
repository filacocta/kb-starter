# Video helper compatibility

The old URL/downloader and direct-model summary/vision entrypoints are retired.
They must fail before network, model or output side effects. Pure offline helper
functions remain for bounded inspection; this file does not authorize fetching.

For a specifically approved video, use `kb-media-capture.py` temporary staging
and `kb-media-inspect.py` with the expected hash. See their contracts and media capture contract.
Extraction uses explicitly installed local dependencies/cache, without implicit
model downloads. Review sampled coverage and omissions before writing a note.
A transcript or a few frames do not establish a complete visual review.

Retention and cleanup are separate explicit decisions. Never run the historical
unbounded URL analyzer as a fallback.
