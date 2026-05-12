import unittest

from circuit_ocr.aliyun_ocr import parse_data_payload, payload_to_candidates
from circuit_ocr.models import Tile


class AliyunOcrTest(unittest.TestCase):
    def test_parse_data_payload_accepts_sdk_body_shape(self):
        payload = {
            "Data": '{"content":"车速传感器\\nCAN-H","prism_wordsInfo":[{"word":"车速传感器","pos":[{"x":1,"y":2}],"prob":98.5}]}'
        }

        result = parse_data_payload(payload)

        self.assertEqual(result["content"], "车速传感器\nCAN-H")
        self.assertEqual(result["prism_wordsInfo"][0]["word"], "车速传感器")

    def test_payload_to_candidates_uses_word_coordinates(self):
        tile = Tile(page=2, row=0, col=1, x=10, y=20, width=300, height=400, path="unused.png")
        payload = {
            "content": "ignored when words exist",
            "prism_wordsInfo": [
                {"word": "X1:185226-1", "x": 11, "y": 22, "width": 33, "height": 44, "prob": 0.92}
            ],
        }

        result = payload_to_candidates(payload, tile=tile)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].page, 2)
        self.assertEqual(result[0].component_name, "X1:185226-1")
        self.assertEqual(result[0].source_tile, "page_002_r00_c01")
        self.assertEqual(result[0].bbox_or_region, "x=11,y=22,w=33,h=44")
        self.assertEqual(result[0].confidence, 0.92)

    def test_payload_to_candidates_accepts_recognize_all_text_blocks(self):
        tile = Tile(page=1, row=1, col=2, x=10, y=20, width=300, height=400, path="unused.png")
        payload = {
            "Content": "车速传感器",
            "SubImages": [
                {
                    "BlockInfo": {
                        "BlockDetails": [
                            {
                                "BlockContent": "CAN-H",
                                "BlockRect": {"x": 1, "y": 2, "width": 3, "height": 4},
                                "BlockConfidence": 87.0,
                            }
                        ]
                    }
                }
            ],
        }

        result = payload_to_candidates(payload, tile=tile)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].component_name, "CAN-H")
        self.assertEqual(result[0].bbox_or_region, '{"x":1,"y":2,"width":3,"height":4}')
        self.assertEqual(result[0].confidence, 0.87)


if __name__ == "__main__":
    unittest.main()
