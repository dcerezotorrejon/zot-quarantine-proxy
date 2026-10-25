"""Tests del servicio del proxy con Zot y skopeo simulados.

Ejecutar desde la raíz del repo:

    PYTHONPATH=shared:proxy python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.service import ImageNotFound, ImageService, UpstreamNotAllowed
from zqp.image_ref import ImageRef, RepoName
from zqp.state import StateStore


class FakeZot:
    """Zot simulado: tags por repositorio físico."""

    def __init__(self, repos: dict[str, list[str]]) -> None:
        self._repos = repos

    def list_tags(self, repo: str) -> list[str]:
        return list(self._repos.get(repo, []))

    def get_digest(self, repo: str, reference: str) -> str | None:
        return None


class ImageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.sqlite3")
        self.settings = Settings(
            zot_url="http://zot:5000",
            upstream_registries=frozenset({"docker.io"}),
            state_dir=Path(self._tmp.name),
            log_level="INFO",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def service(self, repos: dict[str, list[str]]) -> ImageService:
        return ImageService(self.settings, FakeZot(repos), self.state)

    def ref(self, reference: str) -> ImageRef:
        return ImageRef(RepoName("docker.io", "library/nginx"), reference)

    def test_floating_tag_served_from_verified_without_touching_upstream(self):
        service = self.service(
            {"verified/docker.io/library/nginx": ["2.0.0"]}
        )
        with mock.patch("zqp.skopeo.list_tags") as list_tags:
            copy = service.locate(self.ref("latest"))
        self.assertEqual((copy.namespace, copy.reference), ("verified", "2.0.0"))
        list_tags.assert_not_called()

    def test_floating_tag_delegates_to_zot_and_registers_quarantine(self):
        service = self.service({})
        upstream = ["1.27.3", "1.27.4", "2.1.0"]
        with mock.patch("zqp.skopeo.list_tags", return_value=upstream), \
             mock.patch("zqp.skopeo.copy") as copy_call:
            copy = service.locate(self.ref("latest"))
        self.assertEqual((copy.namespace, copy.reference), ("quarantine", "2.1.0"))
        copy_call.assert_not_called()
        pending = [(r.path, r.tag) for r in self.state.list_quarantine()]
        self.assertEqual(pending, [("library/nginx", "2.1.0")])

    def test_exact_tag_is_requested_without_listing_upstream(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.list_tags") as list_tags:
            copy = service.locate(self.ref("1.27.4"))
        self.assertEqual((copy.namespace, copy.reference), ("quarantine", "1.27.4"))
        list_tags.assert_not_called()

    def test_partial_tag_without_match_is_not_found(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.list_tags", return_value=["2.1.0"]):
            with self.assertRaises(ImageNotFound):
                service.locate(self.ref("1.27"))

    def test_disallowed_registry_is_rejected(self):
        service = self.service({})
        repo = RepoName("evil.example.com", "app")
        with self.assertRaises(UpstreamNotAllowed):
            service.locate(ImageRef(repo, "latest"))


if __name__ == "__main__":
    unittest.main()
