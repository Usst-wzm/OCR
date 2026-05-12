import tempfile
import unittest
from pathlib import Path

from circuit_ocr.llm import VisionExtractor
from circuit_ocr.models import Tile


class BrokenJsonExtractor(VisionExtractor):
    def __init__(self):
        self.max_retries = 0

    def _call_tile(self, tile):
        raise ValueError("Expecting ',' delimiter")


class LlmTest(unittest.TestCase):
    def test_bad_tile_response_is_cached_as_error_and_skipped(self):
        extractor = BrokenJsonExtractor()
        tile = Tile(page=1, row=0, col=0, x=0, y=0, width=100, height=100, path="unused.png")
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "tile.json"
            result = extractor.extract_tile(tile, cache_path)
            self.assertEqual(result, [])
            self.assertTrue(cache_path.with_suffix(".error.json").exists())


if __name__ == "__main__":
    unittest.main()
