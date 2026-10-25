from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-media-capture.py"


def load_media_module():
    spec = importlib.util.spec_from_file_location("kb_media_capture_discovery", SCRIPT)
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


def test_caption_requires_figcaption_and_keeps_preceding_text_as_context() -> None:
    items = parse(
        "<p>Unrelated preceding paragraph.</p>"
        "<img src='figure.png'>"
        "<figure><img src='../figure-2.png'><figcaption>Real figure caption</figcaption></figure>"
        "<img src='figure-3.png'>"
    )

    first, second, third = items
    assert first["caption"] == ""
    assert first["context"] == "Unrelated preceding paragraph."
    assert second["caption"] == "Real figure caption"
    # The preceding paragraph, when retained, must not overwrite the explicit
    # figcaption or be emitted in the caption field.
    assert "context" not in second
    assert "context" not in third


def test_context_clears_across_unrelated_block_gaps() -> None:
    items = parse(
        "<p>Paragraph context.</p><div><img src='block-gap.png'></div>"
    )

    assert items[0]["caption"] == ""
    assert "context" not in items[0]


def test_figure_captions_associate_before_or_after_media_including_nested_text() -> None:
    items = parse(
        "<figure><figcaption>Caption before media</figcaption><img src='before.png'></figure>"
        "<figure><img src='after.png'><figcaption>Caption <span>after</span> media</figcaption></figure>"
    )

    assert [item["caption"] for item in items] == [
        "Caption before media",
        "Caption after media",
    ]


def test_nested_figures_keep_captions_associated_with_their_own_media() -> None:
    items = parse(
        "<figure><img src='outer.png'>"
        "<figure><img src='inner.png'><figcaption>Inner caption</figcaption></figure>"
        "<figcaption>Outer caption</figcaption></figure>"
    )

    by_url = {item["origin_url"]: item for item in items}
    assert by_url["https://example.test/articles/outer.png"]["caption"] == "Outer caption"
    assert by_url["https://example.test/articles/inner.png"]["caption"] == "Inner caption"


def test_html5_video_src_and_nested_sources_are_discovered_and_deduplicated() -> None:
    items = parse(
        "<video src='direct.mp4' poster='direct-poster.jpg'></video>"
        "<video poster='nested-poster.jpg'>"
        "<source src='nested.webm' type='video/webm'>"
        "<source src='nested.webm' type='video/webm'>"
        "<source src='alternate.mp4'>"
        "</video>"
        "<video src='no-poster.m4v'></video>"
    )

    by_url = {item["origin_url"]: item for item in items}
    assert set(by_url) == {
        "https://example.test/articles/direct.mp4",
        "https://example.test/articles/direct-poster.jpg",
        "https://example.test/articles/nested.webm",
        "https://example.test/articles/nested-poster.jpg",
        "https://example.test/articles/alternate.mp4",
        "https://example.test/articles/no-poster.m4v",
    }
    assert by_url["https://example.test/articles/direct.mp4"]["kind"] == "video"
    assert by_url["https://example.test/articles/direct-poster.jpg"]["from_video"] == (
        "https://example.test/articles/direct.mp4"
    )
    assert by_url["https://example.test/articles/nested.webm"]["kind"] == "video"
    assert by_url["https://example.test/articles/nested-poster.jpg"]["from_video"] == (
        "https://example.test/articles/nested.webm"
    )
    assert by_url["https://example.test/articles/alternate.mp4"]["kind"] == "video"


def test_video_poster_uses_first_recognized_video_candidate() -> None:
    items = parse(
        "<video poster='late-poster.jpg'>"
        "<source src='audio.mp3' type='audio/mpeg'>"
        "<source src='unknown-stream'>"
        "<source src='late-video.mp4'>"
        "</video>"
    )

    by_url = {item["origin_url"]: item for item in items}
    assert set(by_url) == {
        "https://example.test/articles/late-poster.jpg",
        "https://example.test/articles/late-video.mp4",
    }
    assert by_url["https://example.test/articles/late-poster.jpg"]["from_video"] == (
        "https://example.test/articles/late-video.mp4"
    )


def test_duplicate_poster_fill_preserves_long_video_identity_url() -> None:
    parent_url = "https://video.example/media/clip.mp4?token=" + ("x" * 260)
    items = parse(
        "<img src='long-poster.jpg'>"
        f"<video poster='long-poster.jpg'><source src='{parent_url}'></video>"
    )

    by_url = {item["origin_url"]: item for item in items}
    assert len(parent_url) > 200
    assert by_url["https://example.test/articles/long-poster.jpg"]["from_video"] == parent_url


def test_video_mime_conflicts_do_not_override_a_later_valid_candidate() -> None:
    items = parse(
        "<video poster='mime-poster.jpg'>"
        "<source src='claimed-video.mp4' type='audio/mp4'>"
        "<source src='claimed-audio.mp3' type='video/mp4'>"
        "<source src='extensionless-token' type='video/mp4'>"
        "<source src='extensionless-token' type='audio/mpeg'>"
        "</video>"
    )

    by_url = {item["origin_url"]: item for item in items}
    assert set(by_url) == {
        "https://example.test/articles/mime-poster.jpg",
        "https://example.test/articles/extensionless-token",
    }
    assert by_url["https://example.test/articles/mime-poster.jpg"]["from_video"] == (
        "https://example.test/articles/extensionless-token"
    )


def test_iframe_src_and_lazy_data_src_use_supported_hosts_only() -> None:
    items = parse(
        "<iframe src='https://www.youtube.com/embed/from-src' title='Source title'></iframe>"
        "<iframe data-src='https://player.vimeo.com/video/from-data' title='Lazy title'></iframe>"
        "<iframe src='https://unsupported.example/video.mp4'></iframe>"
        "<iframe data-src='https://unsupported.example/lazy'></iframe>"
    )

    assert [item["origin_url"] for item in items] == [
        "https://www.youtube.com/embed/from-src",
        "https://player.vimeo.com/video/from-data",
    ]
    assert all(item["kind"] == "video-embed" for item in items)
    assert all(item["caption"] == "" for item in items)
    assert [item["title"] for item in items] == ["Source title", "Lazy title"]


def test_duplicate_urls_fill_missing_relationships_but_keep_first_metadata() -> None:
    items = parse(
        "<img src='shared-poster.jpg' alt='Standalone poster'>"
        "<video poster='shared-poster.jpg'><source src='shared.mp4'></video>"
        "<figure><img src='repeated.png'><figcaption>First observed caption</figcaption></figure>"
        "<figure><img src='repeated.png'><figcaption>Conflicting later caption</figcaption></figure>"
    )

    by_url = {item["origin_url"]: item for item in items}
    poster = by_url["https://example.test/articles/shared-poster.jpg"]
    assert poster["alt"] == "Standalone poster"
    assert poster["from_video"] == "https://example.test/articles/shared.mp4"
    assert by_url["https://example.test/articles/repeated.png"]["caption"] == (
        "First observed caption"
    )


def test_static_resolution_rejects_unsafe_schemes_credentials_and_lookalikes() -> None:
    items = parse(
        "<img src='javascript://example.test/bad.png'>"
        "<img src='ftp://example.test/bad.png'>"
        "<img src='https://user:pass@example.test/bad.png'>"
        "<img src='https://example.test:8080/bad.png'>"
        "<img src='https://example.test:443/good.png'>"
        "<video src='javascript://example.test/bad.mp4' poster='ftp://example.test/bad.jpg'></video>"
        "<iframe src='ftp://youtube.com/embed/ftp'></iframe>"
        "<iframe src='https://user@youtube.com/embed/credential'></iframe>"
        "<iframe src='https://youtube.com.evil/embed/lookalike'></iframe>"
        "<iframe src='https://youtube.com:8080/embed/nonstandard-port'></iframe>"
        "<iframe src='https://youtube.com:443/embed/standard-port'></iframe>"
    )

    assert [item["origin_url"] for item in items] == [
        "https://example.test:443/good.png",
        "https://youtube.com:443/embed/standard-port",
    ]


def test_existing_image_audio_and_relative_urls_remain_supported() -> None:
    items = parse(
        "<img src='../images/figure.png' alt='Figure'>"
        "<audio src='/audio/clip.mp3'></audio>"
        "<a href='recording.wav'>recording</a>"
        "<a href='recording.wav'>duplicate recording</a>"
    )

    assert [(item["kind"], item["origin_url"]) for item in items] == [
        ("image", "https://example.test/images/figure.png"),
        ("audio", "https://example.test/audio/clip.mp3"),
        ("audio", "https://example.test/articles/recording.wav"),
    ]
    assert items[0]["alt"] == "Figure"
