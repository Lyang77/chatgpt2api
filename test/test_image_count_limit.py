from __future__ import annotations

import unittest

from pydantic import ValidationError

from api.ai import ImageGenerationRequest
from utils.helper import parse_image_count


class ImageCountLimitTests(unittest.TestCase):
    def test_generation_accepts_four_images(self) -> None:
        request = ImageGenerationRequest(prompt="generate", n=4)

        self.assertEqual(request.n, 4)

    def test_generation_rejects_more_than_four_images(self) -> None:
        with self.assertRaises(ValidationError):
            ImageGenerationRequest(prompt="generate", n=5)

    def test_chat_image_count_accepts_four_images(self) -> None:
        self.assertEqual(parse_image_count(4), 4)

    def test_chat_image_count_rejects_more_than_four_images(self) -> None:
        with self.assertRaisesRegex(Exception, "n must be between 1 and 4"):
            parse_image_count(5)


if __name__ == "__main__":
    unittest.main()
