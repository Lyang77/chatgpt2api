from __future__ import annotations

import unittest

from pydantic import ValidationError

from api.ai import ImageGenerationRequest
from utils.helper import parse_image_count


class ImageCountLimitTests(unittest.TestCase):
    def test_generation_accepts_sixteen_images(self) -> None:
        request = ImageGenerationRequest(prompt="generate", n=16)

        self.assertEqual(request.n, 16)

    def test_generation_rejects_more_than_sixteen_images(self) -> None:
        with self.assertRaises(ValidationError):
            ImageGenerationRequest(prompt="generate", n=17)

    def test_chat_image_count_accepts_sixteen_images(self) -> None:
        self.assertEqual(parse_image_count(16), 16)

    def test_chat_image_count_rejects_more_than_sixteen_images(self) -> None:
        with self.assertRaisesRegex(Exception, "n must be between 1 and 16"):
            parse_image_count(17)


if __name__ == "__main__":
    unittest.main()
