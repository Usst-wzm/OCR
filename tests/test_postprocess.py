import unittest

from circuit_ocr.models import ComponentCandidate, repair_mojibake
from circuit_ocr.postprocess import dedupe_candidates, looks_like_component, normalize_name


class PostprocessTest(unittest.TestCase):
    def test_candidate_uses_fallback_page_for_bad_model_page(self):
        candidate = ComponentCandidate.from_mapping(
            {"page": "page_001_r01_c02", "component_name": "车速传感器"},
            page=1,
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.page, 1)

    def test_normalize_name(self):
        self.assertEqual(normalize_name(" X1：185226－1（黄） "), "X1:185226-1(黄)")

    def test_normalize_strips_measurement_fragments(self):
        self.assertEqual(normalize_name("35°, 指示灯, 35°"), "指示灯")

    def test_repair_mojibake(self):
        self.assertEqual(repair_mojibake("绾挎潫"), "线束")
        self.assertEqual(repair_mojibake("294.8卤0.5"), "294.8±0.5")

    def test_component_whitelist(self):
        self.assertTrue(looks_like_component("车速传感器"))
        self.assertTrue(looks_like_component("X1:1"))
        self.assertTrue(looks_like_component("CAN-H"))
        self.assertFalse(looks_like_component("294.8±0.5"))
        self.assertFalse(looks_like_component("4xΦ6"))

    def test_dedupe_keeps_best_confidence(self):
        items = [
            ComponentCandidate(page=1, component_name="发动机 ECU", source_tile="a", confidence=0.6),
            ComponentCandidate(page=1, component_name="发动机ECU", source_tile="b", confidence=0.9),
        ]
        result = dedupe_candidates(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].confidence, 0.9)
        self.assertIn("a", result[0].source_tile)
        self.assertIn("b", result[0].source_tile)

    def test_filters_obvious_non_components(self):
        items = [ComponentCandidate(page=1, component_name="123"), ComponentCandidate(page=1, component_name="车速传感器")]
        result = dedupe_candidates(items)
        self.assertEqual([item.component_name for item in result], ["车速传感器"])


if __name__ == "__main__":
    unittest.main()
