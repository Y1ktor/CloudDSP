"""Tests for the strict linked-media source URL policy."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


CLOUD_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "Cloud"
sys.path.insert(0, str(CLOUD_SOURCE_ROOT))

from media_url_policy import MediaUrlPolicyError, validate_allowlisted_media_url


class MediaUrlPolicyTests(unittest.TestCase):
    def test_default_allowlist_accepts_reviewed_provider_pages(self):
        accepted_urls = (
            "https://www.youtube.com/watch?v=abc123",
            "https://music.youtube.com/watch?v=abc123",
            "https://youtu.be/abc123",
            "https://www.bilibili.com/video/BV1mw411o7jB",
            "https://www.b23.tv/abc123",
            "https://soundcloud.com/artist/track",
            "https://on.soundcloud.com/abc123",
        )
        with patch.dict(os.environ, {}, clear=True):
            for url in accepted_urls:
                self.assertEqual(validate_allowlisted_media_url(url), url)

    def test_rejects_unapproved_or_unsafe_urls(self):
        rejected_urls = (
            "http://youtube.com/watch?v=abc123",
            "https://youtube.com.evil.example/watch?v=abc123",
            "https://example.com/song",
            "https://user:password@youtube.com/watch?v=abc123",
            "https://youtube.com:8443/watch?v=abc123",
        )
        with patch.dict(os.environ, {}, clear=True):
            for url in rejected_urls:
                with self.assertRaises(MediaUrlPolicyError):
                    validate_allowlisted_media_url(url)

    def test_deployment_can_add_a_reviewed_provider(self):
        with patch.dict(os.environ, {"ALLOWED_MEDIA_HOSTS": "bandcamp.com,audius.co"}, clear=True):
            self.assertEqual(
                validate_allowlisted_media_url("https://artist.bandcamp.com/track/song"),
                "https://artist.bandcamp.com/track/song",
            )
            self.assertEqual(
                validate_allowlisted_media_url("https://audius.co/artist/track"),
                "https://audius.co/artist/track",
            )
            with self.assertRaises(MediaUrlPolicyError):
                validate_allowlisted_media_url("https://youtube.com/watch?v=abc123")


if __name__ == "__main__":
    unittest.main()
