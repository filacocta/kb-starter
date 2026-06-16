#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "playwright>=1.44,<2",
# ]
# ///
"""Headless-Chromium media discovery and element-screenshot capture for the KB.

Sibling tool to kb-media-capture.py. The static-HTML scraper handles 95% of cases
fast (~200ms); this tool is the post-JS fallback for pages where the static fetch
returns 0 contextually-relevant items but the page is content-bearing
(single-page apps, canvas-rendered charts, shadow-DOM, JS-deferred lazy-loads
that don't use data-* attributes).

Two modes:

  --discover <url>
      Render the page in headless Chromium, wait for network-idle, then enumerate
      <img> elements from the post-JS DOM. Returns JSON in the same schema as
      kb-media-capture.py --discover output (source_url, mirror_used, fetch_url,
      fetched, media[], errors). Downstream tooling (chrome filter, capture step)
      then consumes the output identically.

  --screenshot <url> --selector <css> [--out <path>]
      Render the page, locate the first element matching the CSS selector, and
      save a PNG screenshot of just that element's bounding box. Useful for
      canvas-rendered figures and chart dashboards.

This tool runs in foreground from a uv-managed environment via PEP 723 inline
script metadata. The first invocation downloads the playwright Python package
(small); the Chromium binary is shared at ~/.cache/ms-playwright/ from any
previous install. The user MUST have run `playwright install chromium` at least
once (stonkbot bootstraps this; see your project README).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urldefrag

try:
    from playwright.sync_api import (
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        f"playwright not importable: {exc}\n"
        "If running outside uv, install with: pip install 'playwright>=1.44'\n"
        "Then once per machine: playwright install chromium\n"
    )
    sys.exit(2)


# Match the kb-media-capture.py user-agent + viewport conventions where possible.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
NAV_TIMEOUT_MS = 45_000
NETWORK_IDLE_TIMEOUT_MS = 30_000


def discover(url: str, viewport: dict, wait: str, settle_ms: int) -> dict:
    """Render the page in headless Chromium and enumerate post-JS images.

    Returns a dict matching kb-media-capture.py --discover JSON schema:
      {source_url, mirror_used, fetch_url, fetched, media[], errors[]}
    Each media entry has: kind, origin_url, alt, caption, position_hint, from_video.
    """
    result: dict = {
        "source_url": url,
        "mirror_used": None,
        "fetch_url": None,
        "fetched": False,
        "media": [],
        "errors": [],
        "engine": "headless-chromium",
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=viewport, user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()
        try:
            page.goto(url, wait_until=wait, timeout=NAV_TIMEOUT_MS)
            if settle_ms > 0:
                page.wait_for_timeout(settle_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # Not all pages reach networkidle (long-polling sites, ads); proceed anyway.
                result["errors"].append("networkidle-timeout (continuing)")

            result["fetched"] = True

            # Enumerate images from the post-JS DOM. Use currentSrc to pick the
            # actual rendered source (resolves <picture>/srcset to the chosen URL).
            # naturalWidth/Height tells us whether the browser has actually loaded
            # the image (>0) — drop placeholders and broken refs.
            images = page.evaluate(
                """
                () => {
                  const items = [];
                  const seen = new Set();
                  document.querySelectorAll('img').forEach(img => {
                    const src = img.currentSrc || img.src || '';
                    if (!src || src.startsWith('data:') || seen.has(src)) return;
                    seen.add(src);
                    // Best-effort caption: enclosing <figcaption>, else previous text node.
                    const fig = img.closest('figure');
                    let caption = '';
                    if (fig) {
                      const fc = fig.querySelector('figcaption');
                      if (fc) caption = (fc.textContent || '').trim();
                    }
                    // Position hint: nearest heading text walked up the tree.
                    let pos = '';
                    let node = img.parentElement;
                    while (node && !pos) {
                      const h = node.querySelector('h1,h2,h3,h4');
                      if (h && h.textContent) {
                        pos = `near "${h.textContent.trim().slice(0, 60)}"`;
                        break;
                      }
                      node = node.parentElement;
                    }
                    items.push({
                      kind: 'image',
                      origin_url: src,
                      alt: (img.alt || '').trim(),
                      caption: caption.slice(0, 200),
                      position_hint: pos,
                      from_video: null,
                      width: img.naturalWidth || null,
                      height: img.naturalHeight || null,
                      visible: img.offsetParent !== null,
                    });
                  });
                  return items;
                }
                """
            )

            # Also collect <video poster> as image entries (matches kb-media-capture.py).
            video_posters = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('video[poster]')).map(v => ({
                    kind: 'image',
                    origin_url: v.poster,
                    alt: '',
                    caption: '',
                    position_hint: 'video poster',
                    from_video: v.currentSrc || v.src || null,
                    width: null,
                    height: null,
                    visible: v.offsetParent !== null,
                }))
                """
            )
            # Also collect video iframes (YouTube, Vimeo) — matches kb-media-capture.py.
            video_embeds = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('iframe[src]')).filter(f => {
                    const s = f.src || '';
                    return /youtube\\.com|youtube-nocookie\\.com|youtu\\.be|vimeo\\.com|player\\.vimeo\\.com|twitch\\.tv|player\\.twitch\\.tv/.test(s);
                }).map(f => ({
                    kind: 'video-embed',
                    origin_url: f.src,
                    alt: '',
                    caption: (f.title || '').trim().slice(0, 200),
                    position_hint: 'iframe embed',
                    from_video: null,
                    width: null,
                    height: null,
                    visible: f.offsetParent !== null,
                }))
                """
            )

            for entry in images + video_posters + video_embeds:
                entry["origin_url"] = urldefrag(entry["origin_url"]).url
                result["media"].append(entry)

        except PlaywrightTimeoutError as exc:
            result["errors"].append(f"goto-timeout: {exc}")
        except PlaywrightError as exc:
            result["errors"].append(f"playwright-error: {exc}")
        finally:
            context.close()
            browser.close()
    return result


def screenshot_element(url: str, selector: str, out_path: Path, viewport: dict, wait: str, settle_ms: int) -> dict:
    """Capture a PNG screenshot of the first element matching `selector`.

    Returns metadata: {origin_url, local_path, bytes, width, height, selector_used, errors[]}.
    """
    result: dict = {
        "origin_url": url,
        "selector": selector,
        "local_path": str(out_path),
        "bytes": None,
        "width": None,
        "height": None,
        "errors": [],
        "engine": "headless-chromium",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=viewport, user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()
        try:
            page.goto(url, wait_until=wait, timeout=NAV_TIMEOUT_MS)
            if settle_ms > 0:
                page.wait_for_timeout(settle_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                pass

            locator = page.locator(selector).first
            box = locator.bounding_box()
            if box is None:
                result["errors"].append(f"selector matched but element has no visible bounding box: {selector}")
            else:
                result["width"] = int(box["width"])
                result["height"] = int(box["height"])
            locator.scroll_into_view_if_needed(timeout=5_000)
            locator.screenshot(path=str(out_path), timeout=10_000)
            result["bytes"] = out_path.stat().st_size if out_path.exists() else None
        except PlaywrightTimeoutError as exc:
            result["errors"].append(f"timeout: {exc}")
        except PlaywrightError as exc:
            result["errors"].append(f"playwright-error: {exc}")
        finally:
            context.close()
            browser.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless-Chromium media discovery and element-screenshot capture for the KB.",
    )
    subgroup = parser.add_mutually_exclusive_group(required=True)
    subgroup.add_argument("--discover", metavar="URL", help="Enumerate post-JS images from URL")
    subgroup.add_argument("--screenshot", metavar="URL", help="Screenshot a specific element")

    parser.add_argument("--selector", help="CSS selector (required with --screenshot)")
    parser.add_argument("--out", type=Path, help="Output PNG path (required with --screenshot)")
    parser.add_argument("--viewport-width", type=int, default=DEFAULT_VIEWPORT["width"])
    parser.add_argument("--viewport-height", type=int, default=DEFAULT_VIEWPORT["height"])
    parser.add_argument(
        "--wait",
        default="domcontentloaded",
        choices=("load", "domcontentloaded", "networkidle", "commit"),
        help="Page-load state to wait for before extraction (default: domcontentloaded)",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=500,
        help="Extra wait after load to allow JS-deferred mount (default: 500)",
    )
    args = parser.parse_args()

    viewport = {"width": args.viewport_width, "height": args.viewport_height}

    if args.discover:
        result = discover(args.discover, viewport, args.wait, args.settle_ms)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["fetched"] else 1

    if args.screenshot:
        if not args.selector or not args.out:
            parser.error("--screenshot requires --selector and --out")
        result = screenshot_element(args.screenshot, args.selector, args.out, viewport, args.wait, args.settle_ms)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["bytes"] else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
