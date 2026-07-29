from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.setup_config import SetupConfigError, update_setup_config


def estimator_source(root: Path, name: str) -> Path:
    source = root / name
    package = source / "estimator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return source


class SetupConfigTests(unittest.TestCase):
    def test_creates_defaults_with_paths_containing_spaces(self) -> None:
        with TemporaryDirectory(prefix="easy lattice ") as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard estimator")
            enhanced = estimator_source(root, "enhanced estimator")
            config_path = root / "local config.json"

            result = update_setup_config(
                config_path,
                "/Applications/SageMath 10.7.app/Contents/MacOS/sage",
                str(standard),
                str(enhanced),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "created")
        self.assertEqual(
            result.supplemented_fields,
            ("lattice_estimator_path", "enhanced_lattice_estimator_path"),
        )
        self.assertEqual(
            saved["estimator"]["sage_binary"],
            "/Applications/SageMath 10.7.app/Contents/MacOS/sage",
        )
        self.assertEqual(
            saved["estimator"]["lattice_estimator_path"],
            str(standard.resolve()),
        )
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(enhanced.resolve()),
        )
        self.assertFalse(saved["llm"]["enabled"])

    def test_supplements_only_empty_profile_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "detected-standard")
            enhanced = estimator_source(root, "detected-enhanced")
            config_path = root / "config.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "sage_binary": "/existing/sage",
                            "lattice_estimator_path": "  ",
                            "enhanced_lattice_estimator_path": "/keep/enhanced",
                            "remote_url": "https://worker.example",
                            "remote_timeout_seconds": 99,
                        },
                        "llm": {"enabled": True, "model": "keep-model"},
                        "scripts": {"decrypt_error": ["keep"]},
                        "unrelated": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )

            result = update_setup_config(
                config_path,
                "/new/sage",
                str(standard),
                str(enhanced),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "updated")
        self.assertEqual(result.supplemented_fields, ("lattice_estimator_path",))
        self.assertEqual(saved["estimator"]["sage_binary"], "/existing/sage")
        self.assertEqual(
            saved["estimator"]["lattice_estimator_path"],
            str(standard.resolve()),
        )
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            "/keep/enhanced",
        )
        self.assertEqual(saved["estimator"]["remote_url"], "https://worker.example")
        self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 99)
        self.assertEqual(saved["llm"], {"enabled": True, "model": "keep-model"})
        self.assertEqual(saved["scripts"], {"decrypt_error": ["keep"]})
        self.assertEqual(saved["unrelated"], {"keep": True})

    def test_supplements_an_explicit_null_estimator_object(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "detected-standard")
            config_path = root / "config.local.json"
            config_path.write_text('{"estimator": null, "keep": true}\n', encoding="utf-8")

            result = update_setup_config(config_path, "sage", str(standard), None)
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "updated")
        self.assertEqual(
            saved["estimator"]["lattice_estimator_path"],
            str(standard.resolve()),
        )
        self.assertTrue(saved["keep"])

    def test_preserves_and_reports_nonempty_invalid_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            detected = estimator_source(root, "detected")
            config_path = root / "config.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "lattice_estimator_path": "/missing/standard",
                            "enhanced_lattice_estimator_path": None,
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = update_setup_config(
                config_path,
                "sage",
                str(detected),
                str(detected),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(
            saved["estimator"]["lattice_estimator_path"],
            "/missing/standard",
        )
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(detected.resolve()),
        )
        self.assertEqual(
            result.preserved_invalid_fields,
            ("lattice_estimator_path",),
        )

    def test_force_regenerates_the_complete_default_object(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard")
            config_path = root / "config.local.json"
            config_path.write_text('{"unrelated": true}\n', encoding="utf-8")

            result = update_setup_config(
                config_path,
                "/explicit/sage",
                str(standard),
                None,
                force=True,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "regenerated")
        self.assertNotIn("unrelated", saved)
        self.assertEqual(saved["estimator"]["sage_binary"], "/explicit/sage")
        self.assertIsNone(saved["estimator"]["enhanced_lattice_estimator_path"])

    def test_replace_failure_keeps_original_bytes_and_removes_temp_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard")
            config_path = root / "config.local.json"
            original = b'{"estimator":{"lattice_estimator_path":null}}\n'
            config_path.write_bytes(original)

            with mock.patch(
                "app.setup_config.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(SetupConfigError):
                    update_setup_config(config_path, "sage", str(standard), None)

            leftovers = list(root.glob(f".{config_path.name}.*.tmp"))
            self.assertEqual(config_path.read_bytes(), original)

        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
