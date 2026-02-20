from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import update_server.config as app_config
import update_server.security as app_security
import update_server.storage as app_storage
from update_server.main import app


class UpdateServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="update-server-tests-"))
        app_config.UPDATE_STORAGE_DIR = self.temp_dir
        app_storage.UPDATE_STORAGE_DIR = self.temp_dir
        app_security.ADMIN_API_KEY = None
        app_security.ALLOW_UNAUTHENTICATED_UPLOADS = True
        self.client = TestClient(app)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _standard_files(self) -> list[tuple[str, tuple[str, bytes, str]]]:
        return [
            ("files", ("bootloader.bin", b"boot", "application/octet-stream")),
            ("files", ("partitions.bin", b"part", "application/octet-stream")),
            ("files", ("boot_app0.bin", b"app0", "application/octet-stream")),
            ("files", ("firmware.bin", b"firm", "application/octet-stream")),
        ]

    def test_upload_check_manifest_and_binary(self) -> None:
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.get("/api/channels/stable/check", params={"controller_version": "0.9.0"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["updates"]["controller"]["updateAvailable"])
        self.assertEqual(payload["updates"]["controller"]["latest"], "1.0.0")

        response = self.client.get("/api/channels/stable/releases/1.0.0/controller/manifest")
        self.assertEqual(response.status_code, 200, response.text)
        manifest = response.json()
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["builds"][0]["parts"][3]["path"], "/api/channels/stable/releases/1.0.0/controller/firmware.bin")
        self.assertIn("sha256", manifest["builds"][0]["parts"][3])
        self.assertIn("size", manifest["builds"][0]["parts"][3])

        response = self.client.get("/api/channels/stable/releases/1.0.0/controller/firmware.bin")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"firm")

    def test_auth_returns_404_on_wrong_api_key(self) -> None:
        app_security.ADMIN_API_KEY = "secret"
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=[("files", ("firmware.bin", b"x", "application/octet-stream"))],
            headers={"x-api-key": "wrong"},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_upload_disabled_if_no_api_key_and_unauth_not_allowed(self) -> None:
        app_security.ADMIN_API_KEY = None
        app_security.ALLOW_UNAUTHENTICATED_UPLOADS = False
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_upload_rejects_invalid_filename(self) -> None:
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=[("files", ("../evil.bin", b"x", "application/octet-stream"))],
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_upload_rejects_overwriting_existing_release_target(self) -> None:
        first = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "1.0.0", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn("cannot be overwritten", second.json()["detail"])

    def test_upload_rejects_invalid_version_segment(self) -> None:
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "../1.0.0", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_download_rejects_invalid_filename(self) -> None:
        response = self.client.get("/api/channels/stable/releases/1.0.0/controller/../evil.bin")
        self.assertIn(response.status_code, [400, 404])

    def test_upload_supports_version_specific_offsets_and_variable_parts(self) -> None:
        response = self.client.post(
            "/api/admin/releases/upload",
            data={
                "channel": "stable",
                "version": "2.0.0",
                "target": "controller",
            },
            files=[
                ("files", ("bootloader_v2.bin", b"boot2", "application/octet-stream")),
                ("files", ("factory_v2.bin", b"factory2", "application/octet-stream")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.get("/api/channels/stable/releases/2.0.0/controller/manifest")
        self.assertEqual(response.status_code, 200, response.text)
        parts = response.json()["builds"][0]["parts"]
        self.assertEqual(parts, [])

    def test_release_version_details_includes_all_targets_and_files(self) -> None:
        controller_upload = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "3.0.0", "target": "controller"},
            files=self._standard_files() + [("files", ("readme.txt", b"hello", "text/plain"))],
        )
        self.assertEqual(controller_upload.status_code, 200, controller_upload.text)

        display_upload = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "3.0.0", "target": "display"},
            files=[("files", ("display.bin", b"disp", "application/octet-stream"))],
        )
        self.assertEqual(display_upload.status_code, 200, display_upload.text)

        details_response = self.client.get("/api/channels/stable/releases/3.0.0")
        self.assertEqual(details_response.status_code, 200, details_response.text)
        details = details_response.json()

        self.assertEqual(set(details["targets"].keys()), {"controller", "display"})
        controller_files = details["targets"]["controller"]["files"]
        controller_file_names = {item["filename"] for item in controller_files}
        self.assertIn("firmware.bin", controller_file_names)
        self.assertIn("readme.txt", controller_file_names)

        readme_file = [item for item in controller_files if item["filename"] == "readme.txt"][0]
        self.assertIsNone(readme_file["offset"])
        self.assertEqual(readme_file["path"], "/api/channels/stable/releases/3.0.0/controller/readme.txt")

    def test_upload_accepts_signed_sha256_and_exposes_it(self) -> None:
        signed_hashes = """-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA256

"""
        signed_hashes += "3f62550731d942f8bb6b5deb60037847212ca7092c2f2d0d932bce230a0caf26  firmware.bin\n"
        signed_hashes += "-----BEGIN PGP SIGNATURE-----\nabc\n-----END PGP SIGNATURE-----\n"

        response = self.client.post(
            "/api/admin/releases/upload",
            data={
                "channel": "stable",
                "version": "4.0.0",
                "target": "controller",
                "signed_sha256": signed_hashes,
            },
            files=[("files", ("firmware.bin", b"firm", "application/octet-stream"))],
        )
        self.assertEqual(response.status_code, 200, response.text)

        details = self.client.get("/api/channels/stable/releases/4.0.0").json()
        self.assertIn("PGP SIGNATURE", details["targets"]["controller"]["signedSha256"])

    def test_release_listing_uses_semver_order_for_stable_and_git_describe_versions(self) -> None:
        for version in ["v1.7.3", "v1.7.3-16-g011559ea", "v1.8.0", "v1.7.10"]:
            response = self.client.post(
                "/api/admin/releases/upload",
                data={"channel": "stable", "version": version, "target": "controller"},
                files=self._standard_files(),
            )
            self.assertEqual(response.status_code, 200, response.text)

        response = self.client.get("/api/channels/stable/releases")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["versions"],
            ["v1.8.0", "v1.7.10", "v1.7.3-16-g011559ea", "v1.7.3"],
        )

    def test_check_endpoint_compares_git_describe_versions_using_semver_order(self) -> None:
        response = self.client.post(
            "/api/admin/releases/upload",
            data={"channel": "stable", "version": "v1.7.3-16-g011559ea", "target": "controller"},
            files=self._standard_files(),
        )
        self.assertEqual(response.status_code, 200, response.text)

        up_to_date = self.client.get("/api/channels/stable/check", params={"controller_version": "v1.7.3-16-g011559ea"})
        self.assertEqual(up_to_date.status_code, 200, up_to_date.text)
        self.assertFalse(up_to_date.json()["updates"]["controller"]["updateAvailable"])

        behind_tag = self.client.get("/api/channels/stable/check", params={"controller_version": "v1.7.3"})
        self.assertEqual(behind_tag.status_code, 200, behind_tag.text)
        self.assertTrue(behind_tag.json()["updates"]["controller"]["updateAvailable"])

    def test_root_serves_previous_versions_collapsed(self) -> None:
        for version in ["v1.2.0", "v1.1.0", "v1.0.0"]:
            response = self.client.post(
                "/api/admin/releases/upload",
                data={"channel": "stable", "version": version, "target": "controller"},
                files=self._standard_files(),
            )
            self.assertEqual(response.status_code, 200, response.text)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Latest release: <strong>v1.2.0</strong>", response.text)
        self.assertIn("<details class=\"versions\">", response.text)
        self.assertIn("<summary>Previous versions (2)</summary>", response.text)
        self.assertIn("<li>v1.1.0</li>", response.text)
        self.assertIn("<li>v1.0.0</li>", response.text)

    def test_root_serves_html_flasher_page(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Device Firmware Flasher", response.text)


if __name__ == "__main__":
    unittest.main()
