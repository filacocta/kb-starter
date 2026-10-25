#!/usr/bin/env python3
"""Record reviewed contract bytes. This is an explicit release mutation."""

import argparse
import hashlib
import json
from pathlib import Path

from kb_config import atomic_write_text, read_regular_text, resolve_kb_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1] / "knowledgebase"))
    args = parser.parse_args()
    root = resolve_kb_root(args.kb_root)
    specification = json.loads(read_regular_text(Path(__file__).with_name("kb_contract_spec.json")))
    manifest = {"author": "kb-bot", "version": 1, "release": specification["release"], "files": {}}
    for relative in specification["paths"]:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            parser.error("contract path must stay in the KB root")
        text = read_regular_text(root / path)
        manifest["files"][relative] = hashlib.sha256(text.encode()).hexdigest()
    target = root / "_contract.json"
    if target.exists() and json.loads(read_regular_text(target)).get("author") != "kb-bot":
        parser.error("existing contract manifest is not managed by kb-bot")
    atomic_write_text(target, json.dumps(manifest, indent=2) + "\n")
    print(f"Recorded {len(manifest['files'])} contract files for {manifest['release']}.")


if __name__ == "__main__":
    main()
