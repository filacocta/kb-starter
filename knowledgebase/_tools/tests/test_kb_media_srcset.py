"""Bot-authored offline regressions for srcset media discovery."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


# author: kb-bot
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-media-capture.py"


def load_media_module():
    spec = importlib.util.spec_from_file_location("kb_media_capture_srcset", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse(html: str):
    media = load_media_module()
    parser = media.MediaHTMLParser("https://example.test/articles/page.html")
    parser.feed(html)
    parser.close()
    return parser.media


SUBSTACK_URL = (
    "https://substackcdn.com/image/fetch/$s_!YptM!,w_1456,c_limit,"
    "f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media."
    "s3.amazonaws.com%2Fpublic%2Fimages%2Fe37fc0ad-0235-4f73-8986-341a9d0d52e2_1277x1000.png"
)


def test_substack_srcset_prefix_is_suppressed_for_each_direct_and_srcset_attribute() -> None:
    for direct_attribute in ("src", "data-src", "data-original"):
        for srcset_attribute in ("srcset", "data-srcset"):
            items = parse(
                f'<img {direct_attribute}="{SUBSTACK_URL}" '
                f'{srcset_attribute}="{SUBSTACK_URL}">'
            )

            assert [item["origin_url"] for item in items] == [SUBSTACK_URL]


def test_standard_srcset_first_candidates_remain_unchanged() -> None:
    cases = [
        ("first.jpg 480w, second.jpg 800w", "first.jpg"),
        ("first.jpg 1x, second.jpg 2x", "first.jpg"),
        ("first.jpg, second.jpg", "first.jpg"),
    ]

    for srcset, expected in cases:
        items = parse(f'<img srcset="{srcset}">')
        assert [item["origin_url"] for item in items] == [
            f"https://example.test/articles/{expected}"
        ]


def test_srcset_candidate_not_prefix_of_direct_url_is_retained() -> None:
    direct = "https://cdn.example/direct.png"
    alternate = "https://cdn.example/alternate.png"

    items = parse(f'<img src="{direct}" srcset="{alternate} 1x">')

    assert [item["origin_url"] for item in items] == [direct, alternate]


def test_prefix_coincidence_does_not_suppress_complete_alternate() -> None:
    direct = "https://cdn.example/image,large.png"
    alternate = "https://cdn.example/image"

    for srcset_attribute in ("srcset", "data-srcset"):
        items = parse(
            f'<img src="{direct}" {srcset_attribute}="{alternate} 1x">'
        )

        assert [item["origin_url"] for item in items] == [direct, alternate]


def test_direct_variant_in_later_candidate_does_not_suppress_first_alternate() -> None:
    direct = "https://cdn.example/image,large.png"
    alternate = "https://cdn.example/image"

    for srcset_attribute in ("srcset", "data-srcset"):
        items = parse(
            f'<img src="{direct}" {srcset_attribute}="{alternate} 1x, {direct} 2x">'
        )

        assert [item["origin_url"] for item in items] == [direct, alternate]


def test_prefix_from_another_element_is_not_suppressed() -> None:
    prefix = SUBSTACK_URL.split(",", 1)[0]

    items = parse(f'<img src="{SUBSTACK_URL}"><img srcset="{prefix}">')

    assert [item["origin_url"] for item in items] == [SUBSTACK_URL, prefix]


def test_placeholder_and_absent_src_still_use_srcset_fallback() -> None:
    placeholder_items = parse(
        '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP" '
        'srcset="fallback.png 1x, fallback-large.png 2x">'
    )
    absent_items = parse('<img srcset="fallback.png 1x, fallback-large.png 2x">')

    expected = "https://example.test/articles/fallback.png"
    assert [item["origin_url"] for item in placeholder_items] == [expected]
    assert [item["origin_url"] for item in absent_items] == [expected]
