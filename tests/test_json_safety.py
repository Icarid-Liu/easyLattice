import json
import unittest

from app.json_safety import sanitize_json_value


class JsonSafetyTests(unittest.TestCase):
    def test_lone_surrogates_in_nested_keys_and_values_are_replaced(self):
        value = {
            "valid": {"中文键": "中文值"},
            "bad\ud800key": ["value\udfff", {"nested\ud800": "ok"}],
        }

        safe = sanitize_json_value(value)
        encoded = json.dumps(
            safe,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", errors="strict")

        self.assertEqual(safe["valid"], {"中文键": "中文值"})
        self.assertIn("bad\\ud800key", safe)
        self.assertEqual(safe["bad\\ud800key"][0], "value\\udfff")
        self.assertEqual(safe["bad\\ud800key"][1], {"nested\\ud800": "ok"})
        self.assertNotIn(b"\\xed\\xa0\\x80", encoded)


if __name__ == "__main__":
    unittest.main()
