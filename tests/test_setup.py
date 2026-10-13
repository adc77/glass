"""SDK selection is explicit and never shadowed by a stale sibling checkout."""

import os
import unittest
from unittest.mock import patch

from tests.support import _find_seam


class SetupTests(unittest.TestCase):
    def test_installed_sdk_is_used_even_when_sibling_checkouts_exist(self):
        with (
            patch.dict(os.environ, {"SEAM_SDK_PATH": ""}),
            patch("os.path.isdir", return_value=True) as probe,
        ):
            self.assertIsNone(_find_seam())
            probe.assert_not_called()

    def test_explicit_source_override_is_respected(self):
        with (
            patch.dict(os.environ, {"SEAM_SDK_PATH": "/explicit/sdk"}),
            patch("os.path.isdir", return_value=True),
        ):
            self.assertEqual(_find_seam(), "/explicit/sdk")

    def test_invalid_override_fails_instead_of_using_installed_sdk(self):
        with (
            patch.dict(os.environ, {"SEAM_SDK_PATH": "/missing/sdk"}),
            patch("os.path.isdir", return_value=False),
        ):
            with self.assertRaisesRegex(AssertionError, "has no seam/ package"):
                _find_seam()
