import unittest

from circuit_ocr.json_utils import component_list_from_payload, extract_json_payload


class JsonUtilsTest(unittest.TestCase):
    def test_extracts_fenced_json(self):
        payload = extract_json_payload('```json\n{"components":[{"component_name":"发动机ECU"}]}\n```')
        self.assertEqual(payload["components"][0]["component_name"], "发动机ECU")

    def test_extracts_embedded_json(self):
        payload = extract_json_payload('结果如下：{"components":[{"component_name":"X1:1"}]}')
        self.assertEqual(component_list_from_payload(payload)[0]["component_name"], "X1:1")


if __name__ == "__main__":
    unittest.main()
