"""Synthetic checks; never import app.py or open business data/secrets."""
import ast
import io
import logging
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, flash, redirect, request, url_for
from werkzeug.utils import secure_filename
from utils.upload_archive import preserve_upload


class UploadArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.imports = self.root / "imports"
        self.imports.mkdir()
        self.archive = self.root / "upload_archive"
        self.source = self.imports / "0123456789ab_example.xlsx"
        self.source.write_bytes(b"synthetic workbook bytes")

    def test_preserved_after_temporary_source_removed(self):
        result = preserve_upload(self.source, self.archive)
        self.source.unlink()
        self.assertEqual(result.read_bytes(), b"synthetic workbook bytes")
        self.assertFalse(list(self.archive.glob(".partial-*")))

    def test_collision_never_overwrites(self):
        result = preserve_upload(self.source, self.archive)
        self.source.write_bytes(b"different upload")
        with self.assertRaises(FileExistsError):
            preserve_upload(self.source, self.archive)
        self.assertEqual(result.read_bytes(), b"synthetic workbook bytes")
        self.assertEqual(self.source.read_bytes(), b"different upload")
        self.assertFalse(list(self.archive.glob(".partial-*")))

    def test_verification_failure_publishes_nothing(self):
        with patch("utils.upload_archive._digest", side_effect=[b"a", b"b"]):
            with self.assertRaises(OSError):
                preserve_upload(self.source, self.archive)
        self.assertEqual(list(self.archive.iterdir()), [])
        self.assertTrue(self.source.exists())

    def test_copy_failure_preserves_existing_archive(self):
        result = preserve_upload(self.source, self.archive)
        with patch("utils.upload_archive.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                preserve_upload(self.source, self.archive)
        self.assertEqual(result.read_bytes(), self.source.read_bytes())
        self.assertFalse(list(self.archive.glob(".partial-*")))

    def route_app(self, preserve=preserve_upload, preview=None):
        # Execute only the two real route definitions, excluding startup code.
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8"))
        definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                       and n.name in ("imports_page", "mapped_upload")]
        app = Flask(__name__)
        app.secret_key = "synthetic-test-only"
        app.add_url_rule("/review/<profile>", endpoint="mapped_review", view_func=lambda profile: "review")
        app.add_url_rule("/confirm", endpoint="import_upload_confirm", view_func=lambda: "confirm")
        namespace = dict(app=app, admin_required=lambda f: f, request=request,
                         flash=flash, redirect=redirect, url_for=url_for,
                         secure_filename=secure_filename, secrets=secrets,
                         ROOT=self.root, IMPORTS=self.imports, logging=logging,
                         mapped_refusal=lambda name: False,
                         detect_country=lambda name: "Synthetic",
                         mapped_profile=lambda name: {"name": name},
                         mapped_options=lambda form: {},
                         current_user=lambda: {"username": "synthetic-admin"},
                         preserve_upload=preserve,
                         preview_import=preview or (lambda *args: "preview"))
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "upload-routes", "exec"), namespace)
        return app

    def test_both_upload_routes_preserve_before_processing(self):
        for route in ("/admin/imports", "/admin/imports/mapped/demo/upload"):
            with self.subTest(route=route):
                client = self.route_app().test_client()
                result = client.post(route, data={"file": (io.BytesIO(b"new workbook"), "new.xlsx")})
                self.assertIn(result.status_code, (200, 302))
        archives = list(self.archive.glob("*_new.xlsx"))
        self.assertEqual(len(archives), 2)
        self.assertTrue(all(p.read_bytes() == b"new workbook" for p in archives))

    def test_failure_blocks_preview_and_mapped_review(self):
        for route in ("/admin/imports", "/admin/imports/mapped/demo/upload"):
            with self.subTest(route=route):
                preview = unittest.mock.Mock(return_value="preview")
                failed = unittest.mock.Mock(side_effect=OSError("disk full"))
                client = self.route_app(failed, preview).test_client()
                with self.assertLogs(level="ERROR"):
                    result = client.post(route, data={"file": (io.BytesIO(b"new workbook"), "new.xlsx")})
                self.assertEqual(result.status_code, 302)
                self.assertEqual(result.headers["Location"], "/admin/imports")
                preview.assert_not_called()

    def test_failed_preview_keeps_archived_upload(self):
        client = self.route_app(preview=lambda *args: None).test_client()
        client.post("/admin/imports", data={"file": (io.BytesIO(b"bad workbook"), "bad.xlsx")})
        self.assertFalse(list(self.imports.glob("*_bad.xlsx")))
        self.assertEqual(next(self.archive.glob("*_bad.xlsx")).read_bytes(), b"bad workbook")


if __name__ == "__main__":
    unittest.main()
