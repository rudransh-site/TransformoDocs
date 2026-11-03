"""Regression tests for the V1 fixes shipped with the V2/V5 update (Hindi, ligatures)."""
from django.test import SimpleTestCase

from core.services import extractor, native
from core.services.classify import score_type
from core.services.ocr import is_texty
from core.services.text_utils import parse_date, shape


class LigatureTests(SimpleTestCase):
    def test_ligatures_and_invisible_spaces_are_normalised(self):
        self.assertEqual(native.normalize_text("Certi\ufb01cate \ufb02ow o\ufb03ce"), "Certificate flow office")
        self.assertEqual(native.normalize_text("a\u00a0b\u200bc"), "a bc")
        self.assertEqual(native.normalize_text("plain"), "plain")


class HindiSupportTests(SimpleTestCase):
    SCHEMA = {"fields": [
        {"key": "recipient_name", "label": "Name", "type": "string", "required": True,
         "strategies": [{"kind": "label", "anchors": ["यह प्रमाणित किया जाता है कि"], "next_line": True}]},
        {"key": "issue_date", "label": "Date", "type": "date", "required": True,
         "strategies": [{"kind": "label", "anchors": ["दिनांक"]}]},
        {"key": "full_name", "label": "Name", "type": "string", "required": False,
         "strategies": [{"kind": "label", "anchors": ["नाम"]}]},
        {"key": "skills", "label": "Skills", "type": "list", "required": False,
         "strategies": [{"kind": "section", "headings": ["कौशल"], "stop_headings": ["अनुभव"]}]},
    ]}

    def lines(self, *texts):
        return [{"text": t, "conf": 90.0} for t in texts]

    def test_hindi_labels_and_values(self):
        data, _ = extractor.extract_fields(self.SCHEMA, self.lines(
            "प्रमाणपत्र", "यह प्रमाणित किया जाता है कि", "प्रिया शर्मा", "दिनांक: 12/09/2026"))
        self.assertEqual(data["recipient_name"], "प्रिया शर्मा")
        self.assertEqual(data["issue_date"], "2026-09-12")

    def test_anchor_does_not_match_inside_a_longer_hindi_word(self):
        data, _ = extractor.extract_fields(self.SCHEMA, self.lines("नामांकन संख्या: 23BT0412"))
        self.assertNotIn("full_name", data)  # 'नाम' must not match inside 'नामांकन'
        data, _ = extractor.extract_fields(self.SCHEMA, self.lines("नाम: अंकित जैन"))
        self.assertEqual(data["full_name"], "अंकित जैन")

    def test_hindi_section_headings(self):
        data, _ = extractor.extract_fields(self.SCHEMA, self.lines("कौशल", "Python, Django, SQL", "अनुभव", "Intern"))
        self.assertEqual(data["skills"], ["Python", "Django", "SQL"])

    def test_devanagari_digits_parse_as_dates(self):
        self.assertEqual(parse_date("१२/०९/२०२६"), "2026-09-12")
        self.assertIsNotNone(shape("string", "प्रिया"))

    def test_combining_marks_count_as_text(self):
        self.assertTrue(all(is_texty(c) for c in "प्रिया शर्मा".replace(" ", "")))
        self.assertFalse(is_texty("!"))

    def test_classification_terms_match_hindi_phrases(self):
        rules = {"terms": [{"t": "प्रमाणित किया जाता है", "w": 4}, {"t": "प्रमाणपत्र", "w": 3}]}
        self.assertEqual(score_type("यह प्रमाणपत्र प्रमाणित किया जाता है", rules), 7)
        self.assertEqual(score_type("इसका कोई संबंध नहीं", rules), 0)
