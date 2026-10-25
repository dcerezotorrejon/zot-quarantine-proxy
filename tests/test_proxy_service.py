"""Tests del servicio del proxy con Zot y skopeo simulados.

Ejecutar desde la raíz del repo:

    PYTHONPATH=shared:proxy python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.resolver import ServeSource
from app.service import ImageNotFound, ImageService, UpstreamNotAllowed
from zqp.image_ref import ImageRef, RepoName
from zqp.state import StateStore


class FakeZot:
    """Zot simulado: tags y digests por repositorio físico."""

    def __init__(self, repos: dict[str, list[str]] | None = None, digests: dict[str, set[str]] | None = None) -> None:
        self._repos = repos or {}
        self._digests = digests or {}

    def list_tags(self, repo: str) -> list[str]:
        return list(self._repos.get(repo, []))

    def get_digest(self, repo: str, reference: str) -> str | None:
        if reference in self._digests.get(repo, set()):
            return f"sha256:{reference}"
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

    def service(
        self,
        repos: dict[str, list[str]] | None = None,
        digests: dict[str, set[str]] | None = None,
    ) -> ImageService:
        return ImageService(self.settings, FakeZot(repos, digests), self.state)

    def ref(self, reference: str) -> ImageRef:
        return ImageRef(RepoName("docker.io", "library/nginx"), reference)

    def test_floating_tag_served_from_verified_without_touching_upstream(self):
        service = self.service({"verified/docker.io/library/nginx": ["2.0.0"]})
        with mock.patch("zqp.skopeo.list_tags") as list_tags, \
             mock.patch("zqp.quarantine.copy_to_quarantine") as copy_call:
            target = service.locate(self.ref("latest"))
        self.assertEqual((target.source, target.reference), (ServeSource.VERIFIED, "2.0.0"))
        list_tags.assert_not_called()
        copy_call.assert_not_called()

    def test_floating_tag_resolves_upstream_and_schedules_quarantine(self):
        service = self.service({})
        upstream = ["1.27.3", "1.27.4", "2.1.0"]
        with mock.patch("zqp.skopeo.list_tags", return_value=upstream), \
             mock.patch("app.service._spawn", side_effect=lambda fn: fn()), \
             mock.patch("zqp.quarantine.copy_to_quarantine") as copy_call:
            target = service.locate(self.ref("latest"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "2.1.0"))
        copy_call.assert_called_once()
        args = copy_call.call_args.args
        self.assertEqual(args[0], "zot:5000")
        self.assertIs(args[1], self.state)
        self.assertEqual(args[2].upstream_image, "docker.io/library/nginx")
        self.assertEqual(args[3], "2.1.0")

    def test_exact_tag_served_from_upstream_without_listing_upstream(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.list_tags") as list_tags, \
             mock.patch("app.service._spawn", side_effect=lambda fn: fn()), \
             mock.patch("zqp.quarantine.copy_to_quarantine") as copy_call:
            target = service.locate(self.ref("1.27.4"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "1.27.4"))
        list_tags.assert_not_called()
        self.assertEqual(copy_call.call_args.args[3], "1.27.4")

    def test_partial_tag_without_match_is_not_found(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.list_tags", return_value=["2.1.0"]):
            with self.assertRaises(ImageNotFound):
                service.locate(self.ref("1.27"))

    def test_digest_served_from_verified_when_present(self):
        digest = "sha256:abc"
        service = self.service(
            {}, digests={"verified/docker.io/library/nginx": {digest}}
        )
        target = service.locate(self.ref(digest))
        self.assertEqual((target.source, target.reference), (ServeSource.VERIFIED, digest))

    def test_digest_served_from_upstream_when_absent(self):
        service = self.service({})
        target = service.locate(self.ref("sha256:abc"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "sha256:abc"))

    def test_quarantine_copy_is_deduplicated_while_in_flight(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.list_tags", return_value=["2.1.0"]), \
             mock.patch("app.service._spawn") as spawn:
            service.locate(self.ref("latest"))
            service.locate(self.ref("latest"))
            service.locate(self.ref("latest"))
        spawn.assert_called_once()

    def test_disallowed_registry_is_rejected(self):
        service = self.service({})
        repo = RepoName("evil.example.com", "app")
        with self.assertRaises(UpstreamNotAllowed):
            service.locate(ImageRef(repo, "latest"))


if __name__ == "__main__":
    unittest.main()
