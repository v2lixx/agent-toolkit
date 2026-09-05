from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import configure_runtime, continuity_runtime


class RuntimeConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "Diaries"
        self.protocol = self.root / "PROTOCOL.md"
        self.protocol.write_text("# Test protocol\n", encoding="utf-8")
        self.config = self.root / "runtime.json"
        self.package = self.root / "copied-package"
        self.package.mkdir()
        self.write_config()
        self.env = patch.dict(os.environ, {"CONTINUITY_CONFIG": str(self.config)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def write_config(self, **overrides) -> None:
        data = {"schema_version": 1, "diary_base": str(self.base), "protocol_path": str(self.protocol)}
        data.update(overrides)
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def configure(self, *extra: str, protocol: Path | None = None, base: Path | None = None) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(configure_runtime, "SOURCE_ROOT", self.package), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = configure_runtime.main([
                "--diary-base", str(base or self.base), "--protocol-path", str(protocol or self.protocol),
                "--python", sys.executable, *extra,
            ])
        return code, output.getvalue(), errors.getvalue()

    def test_valid_config_accepts_new_base_without_creating_it(self) -> None:
        loaded = continuity_runtime.load_runtime_config()
        self.assertEqual(loaded.diary_base, self.base)
        self.assertEqual(loaded.protocol_path, self.protocol)
        self.assertEqual(loaded.config_path, self.config)
        self.assertFalse(self.base.exists())

    def test_valid_existing_directory_is_not_changed(self) -> None:
        self.base.mkdir()
        sentinel = self.base / "private-unrelated"
        sentinel.write_bytes(b"untouched")
        continuity_runtime.load_runtime_config()
        self.assertEqual(list(self.base.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"untouched")

    def test_existing_base_file_or_file_ancestor_is_rejected(self) -> None:
        self.base.write_bytes(b"not a directory")
        for target in (self.base, self.base / "nested"):
            self.write_config(diary_base=str(target))
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "not a directory"):
                continuity_runtime.load_runtime_config()
        self.assertEqual(self.base.read_bytes(), b"not a directory")

    def test_base_symlink_is_rejected_even_if_dangling(self) -> None:
        target = self.root / "target"
        for exists in (False, True):
            if exists:
                target.mkdir()
            self.base.symlink_to(target, target_is_directory=True)
            with self.subTest(exists=exists), self.assertRaisesRegex(ValueError, "symlink"):
                continuity_runtime.load_runtime_config()
            self.base.unlink()

    def test_missing_or_directory_protocol_is_rejected(self) -> None:
        missing = self.root / "missing"
        directory = self.root / "directory"
        directory.mkdir()
        for target in (missing, directory):
            self.write_config(protocol_path=str(target))
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "regular"):
                continuity_runtime.load_runtime_config()
        self.assertFalse(self.base.exists())

    def test_protocol_symlink_is_rejected_even_if_target_is_valid(self) -> None:
        link = self.root / "protocol-link"
        link.symlink_to(self.protocol)
        self.write_config(protocol_path=str(link))
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            continuity_runtime.load_runtime_config()

    def test_unreadable_protocol_is_rejected(self) -> None:
        # Mock access rather than chmod: privileged test runners can still read
        # mode 000 files and make a permission test misleading.
        with patch.object(continuity_runtime.os, "access", return_value=False), self.assertRaisesRegex(ValueError, "readable"):
            continuity_runtime.load_runtime_config()
        self.assertFalse(self.base.exists())

    def test_relative_and_parent_traversal_paths_are_rejected(self) -> None:
        for field in ("diary_base", "protocol_path"):
            for value in ("relative/path", str(self.root / ".." / "elsewhere"), "", None, 42):
                self.write_config(**{field: value})
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    continuity_runtime.load_runtime_config()

    def test_schema_rejects_nonobject_unknown_missing_and_boolean_version(self) -> None:
        bad = (
            [], {"schema_version": 1},
            {"schema_version": 1, "diary_base": str(self.base), "protocol_path": str(self.protocol), "thread_id": "wrong"},
            {"schema_version": True, "diary_base": str(self.base), "protocol_path": str(self.protocol)},
        )
        for value in bad:
            self.config.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(ValueError):
                continuity_runtime.load_runtime_config()

    def test_malformed_or_oversized_config_is_rejected(self) -> None:
        for value in ("{not JSON", " " * 65_537):
            self.config.write_text(value, encoding="utf-8")
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                continuity_runtime.load_runtime_config()

    def test_missing_or_nonregular_config_never_falls_back(self) -> None:
        for target in (self.root / "missing-config", self.root / "directory-config"):
            if target.name == "directory-config":
                target.mkdir()
            with patch.dict(os.environ, {"CONTINUITY_CONFIG": str(target)}), self.subTest(target=target), self.assertRaises(ValueError):
                continuity_runtime.load_runtime_config()
        self.assertFalse(self.base.exists())

    def test_symlink_config_is_rejected(self) -> None:
        link = self.root / "config-link"
        link.symlink_to(self.config)
        with patch.dict(os.environ, {"CONTINUITY_CONFIG": str(link)}), self.assertRaisesRegex(ValueError, "non-symlink"):
            continuity_runtime.load_runtime_config()

    def test_generated_launcher_pins_local_config_despite_inherited_override(self) -> None:
        with patch.object(configure_runtime, "SOURCE_ROOT", self.package):
            runtime, mcp = configure_runtime.build_configuration(str(self.base), str(self.protocol), sys.executable)
        server = mcp["mcpServers"]["continuity-journal"]
        self.assertEqual(server["env"], {"CONTINUITY_CONFIG": str(self.package / "runtime.json")})
        self.assertNotEqual(server["env"]["CONTINUITY_CONFIG"], os.environ["CONTINUITY_CONFIG"])
        self.assertEqual(server["args"], [str(self.package / "scripts" / "continuity_mcp.py")])
        self.assertEqual(server["cwd"], str(self.package))
        self.assertEqual(runtime["diary_base"], str(self.base))

    def test_configure_preview_has_no_writes(self) -> None:
        code, output, error = self.configure()
        self.assertEqual(code, 0, error)
        self.assertFalse(json.loads(output)["written"])
        self.assertEqual(list(self.package.iterdir()), [])
        self.assertFalse(self.base.exists())

    def test_configure_writes_only_package_configs_and_loads_them(self) -> None:
        protocol_before = self.protocol.read_bytes()
        code, output, error = self.configure("--write")
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)["written"])
        self.assertEqual({path.name for path in self.package.iterdir()}, {"runtime.json", ".mcp.json"})
        launcher = json.loads((self.package / ".mcp.json").read_text(encoding="utf-8"))
        with patch.dict(os.environ, launcher["mcpServers"]["continuity-journal"]["env"]):
            loaded = continuity_runtime.load_runtime_config()
        self.assertEqual(loaded.config_path, self.package / "runtime.json")
        self.assertEqual(loaded.diary_base, self.base)
        self.assertEqual(self.protocol.read_bytes(), protocol_before)
        self.assertFalse(self.base.exists())
        if os.name == "posix":
            self.assertEqual((self.package / "runtime.json").stat().st_mode & 0o777, 0o600)

    def test_invalid_paths_fail_before_overwriting_existing_configs(self) -> None:
        for name in ("runtime.json", ".mcp.json"):
            (self.package / name).write_bytes(b"existing configuration")
        code, _, error = self.configure("--write", protocol=self.root / "missing")
        self.assertEqual(code, 1)
        self.assertIn("regular", error)
        for path in self.package.iterdir():
            self.assertEqual(path.read_bytes(), b"existing configuration")

    def test_nonregular_second_destination_is_rejected_before_first_write(self) -> None:
        original = self.package / "runtime.json"
        original.write_bytes(b"existing configuration")
        outside = self.root / "outside"
        outside.write_bytes(b"outside unchanged")
        (self.package / ".mcp.json").symlink_to(outside)
        code, _, error = self.configure("--write")
        self.assertEqual(code, 1)
        self.assertIn("non-regular", error)
        self.assertEqual(original.read_bytes(), b"existing configuration")
        self.assertEqual(outside.read_bytes(), b"outside unchanged")

    def create_manifest(self, **overrides) -> Path:
        directory = self.package / ".codex-plugin"
        directory.mkdir(exist_ok=True)
        manifest = {"name": "continuity-journal", "version": "0.1.0", "skills": "./skills/"}
        manifest.update(overrides)
        path = directory / "plugin.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_configure_plugin_requires_explicit_write(self) -> None:
        code, _, error = self.configure("--configure-plugin")
        self.assertEqual(code, 1)
        self.assertIn("requires --write", error)
        self.assertEqual(list(self.package.iterdir()), [])

    def test_plain_configure_does_not_modify_manifest(self) -> None:
        manifest = self.create_manifest()
        before = manifest.read_bytes()
        code, output, error = self.configure("--write")
        self.assertEqual(code, 0, error)
        self.assertFalse(json.loads(output)["plugin_configured"])
        self.assertEqual(manifest.read_bytes(), before)

    def test_configure_plugin_updates_only_mcp_pointer_and_is_repeatable(self) -> None:
        manifest = self.create_manifest(description="Keep this metadata")
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        expected["mcpServers"] = "./.mcp.json"
        for _ in range(2):
            code, output, error = self.configure("--write", "--configure-plugin")
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["plugin_configured"])
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), expected)
        self.assertFalse(self.base.exists())

    def test_invalid_plugin_fails_before_any_configuration_writes(self) -> None:
        for overrides in ({"name": "unrelated"}, {"mcpServers": {"another-server": {}}}):
            manifest = self.create_manifest(**overrides)
            before = manifest.read_bytes()
            with self.subTest(overrides=overrides):
                code, _, error = self.configure("--write", "--configure-plugin")
                self.assertEqual(code, 1)
                self.assertIn("Refusing", error)
                self.assertEqual(manifest.read_bytes(), before)
                self.assertFalse((self.package / "runtime.json").exists())
                self.assertFalse((self.package / ".mcp.json").exists())

    def test_missing_plugin_manifest_fails_before_any_configuration_writes(self) -> None:
        code, _, error = self.configure("--write", "--configure-plugin")
        self.assertEqual(code, 1)
        self.assertIn("regular", error)
        self.assertEqual(list(self.package.iterdir()), [])

    def test_symlink_plugin_manifest_is_not_followed(self) -> None:
        manifest = self.create_manifest()
        target = self.root / "outside-manifest.json"
        manifest.replace(target)
        manifest.symlink_to(target)
        before = target.read_bytes()
        code, _, error = self.configure("--write", "--configure-plugin")
        self.assertEqual(code, 1)
        self.assertIn("regular", error)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.package / "runtime.json").exists())


if __name__ == "__main__":
    unittest.main()
