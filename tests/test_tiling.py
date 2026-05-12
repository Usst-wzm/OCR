import tempfile
import unittest
from pathlib import Path

from PIL import Image

from circuit_ocr.tiling import iter_tiles


class TilingTest(unittest.TestCase):
    def test_page_mode_creates_single_resized_image(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.png"
            Image.new("RGB", (5000, 3000), "white").save(source)
            tiles = iter_tiles(source, 1, Path(directory) / "tiles", mode="page", page_max_side=1000)
            self.assertEqual(len(tiles), 1)
            with Image.open(tiles[0].path) as image:
                self.assertEqual(max(image.size), 1000)

    def test_tiles_mode_keeps_overlapping_crops(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.png"
            Image.new("RGB", (200, 100), "white").save(source)
            tiles = iter_tiles(source, 1, Path(directory) / "tiles", mode="tiles", tile_size=120, overlap=20)
            self.assertGreater(len(tiles), 1)

    def test_grid_mode_creates_configured_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.png"
            Image.new("RGB", (600, 400), "white").save(source)
            tiles = iter_tiles(
                source,
                1,
                Path(directory) / "tiles",
                mode="grid",
                grid_rows=2,
                grid_cols=3,
                grid_overlap=20,
            )
            self.assertEqual(len(tiles), 6)


if __name__ == "__main__":
    unittest.main()
