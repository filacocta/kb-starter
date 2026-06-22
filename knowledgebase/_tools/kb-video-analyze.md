# kb-video-analyze.py

Analyze a video URL into a structured markdown/JSON artifact for KB ingest. The
tool does not create source notes and does not write into `_media/`; keyframes
stay in the working directory.

```bash
_tools/.venv/bin/python _tools/kb-video-analyze.py "https://www.youtube.com/watch?v=..." --json
```

Default output is `<workdir>/analysis.md`. Use `--out PATH` to choose a stable
artifact path and `--workdir DIR` to keep downloads/keyframes.

Useful modes:

```bash
# Captions first, Whisper fallback; no frame work.
_tools/.venv/bin/python _tools/kb-video-analyze.py URL --transcript-only --out /tmp/video-analysis.md

# Add OCR over deduped keyframes.
_tools/.venv/bin/python _tools/kb-video-analyze.py URL --workdir /tmp/video-work --max-frames 40

# Optional Claude CLI figure descriptions and DeepSeek transcript summary.
source ~/.config/ai/deepseek.env
_tools/.venv/bin/python _tools/kb-video-analyze.py URL --vision --summary
```

Flags:

- `--json`: emit the same structured data to stdout.
- `--transcript-only`: skip video/keyframe/OCR/vision work.
- `--no-ocr`: keep distinct keyframes but skip tesseract OCR.
- `--vision`: describe likely figures/charts with `claude -p` and local image
  reads; capped to 15 candidate frames.
- `--summary`: summarize transcript text with DeepSeek
  (`DEEPSEEK_API_KEY`, model `deepseek-v4-flash`).
- `--whisper-model`, `--device`: configure faster-whisper fallback; defaults are
  `base` and `cpu`.
- `--max-frames`: cap distinct saved keyframes; default `40`.

The script degrades gracefully: missing captions, ffmpeg, tesseract, model
downloads, Claude CLI, or DeepSeek credentials produce stderr warnings and a
partial artifact. It exits non-zero only when it cannot get any useful metadata
or analysis content.
