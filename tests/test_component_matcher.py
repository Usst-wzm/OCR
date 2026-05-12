import unittest

from circuit_ocr.component_matcher import extract_components_from_ocr, names_from_text
from circuit_ocr.models import ComponentCandidate


class ComponentMatcherTest(unittest.TestCase):
    def test_extracts_component_names_from_table_text(self):
        names = names_from_text("X1:1 排放故障警报- 低电平")
        self.assertIn("X1:1", names)
        self.assertIn("排放故障警报", names)

    def test_filters_measurement_text(self):
        self.assertEqual(names_from_text("294.8±0.5"), [])
        self.assertEqual(names_from_text("4xΦ6"), [])

    def test_extracts_from_ocr_items(self):
        result = extract_components_from_ocr(
            [
                ComponentCandidate(page=1, component_name="X2:11 室外温度信号输入", raw_text="X2:11 室外温度信号输入"),
                ComponentCandidate(page=1, component_name="35°", raw_text="35°"),
            ]
        )
        names = {item.component_name for item in result}
        self.assertIn("X2:11", names)
        self.assertTrue(any("温度" in name or "信号" in name for name in names))


if __name__ == "__main__":
    unittest.main()
