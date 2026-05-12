import tempfile
import unittest
import zipfile
from pathlib import Path

from circuit_ocr.exporters import write_clean_name_list, write_name_list, write_ocr_texts, write_xlsx
from circuit_ocr.models import ComponentCandidate


class ExportersTest(unittest.TestCase):
    def test_write_xlsx_creates_valid_zip_package(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "components.xlsx"
            ok = write_xlsx([ComponentCandidate(page=1, component_name="车速传感器")], path)
            self.assertTrue(ok)
            self.assertTrue(path.exists())
            self.assertTrue(zipfile.is_zipfile(path))

    def test_write_name_list_dedupes_names(self):
        with tempfile.TemporaryDirectory() as directory:
            txt_path = Path(directory) / "names.txt"
            csv_path = Path(directory) / "names.csv"
            write_name_list(
                [
                    ComponentCandidate(page=1, component_name="车速传感器"),
                    ComponentCandidate(page=2, component_name="车速传感器"),
                    ComponentCandidate(page=2, component_name="CAN-H"),
                ],
                txt_path,
                csv_path,
            )
            self.assertEqual(txt_path.read_text(encoding="utf-8").splitlines(), ["CAN-H", "车速传感器"])
            self.assertTrue(csv_path.exists())

    def test_write_clean_name_list_filters_descriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            txt_path = Path(directory) / "names.txt"
            csv_path = Path(directory) / "names.csv"
            write_clean_name_list(
                [
                    ComponentCandidate(page=1, component_name="CAN-H", category="电气信号/控制", confidence=0.99),
                    ComponentCandidate(page=1, component_name="通过点按按键1实现亮度调节。", category="开关/按钮", confidence=0.99),
                    ComponentCandidate(page=1, component_name="武通对长护按键2千头洁男", category="开关/按钮", confidence=0.67),
                ],
                txt_path,
                csv_path,
            )
            self.assertEqual(txt_path.read_text(encoding="utf-8").splitlines(), ["CAN-H"])

    def test_write_ocr_texts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_ocr_texts(
                [ComponentCandidate(page=1, component_name="车速传感器", raw_text="车速传感器")],
                root / "ocr.json",
                root / "ocr.csv",
                root / "ocr.txt",
            )
            self.assertIn("车速传感器", (root / "ocr.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
