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
from app.service import ImageService, UpstreamNotAllowed
from zqp.image_ref import ImageRef, RepoName
from zqp.malware import MalwareResult, MalwareVerdict
from zqp.state import StateStore


class FakeZot:
    """Zot simulado: tags y digests por repositorio físico.

    ``repos`` mapea repo físico a lista de tags; ``digests`` mapea repo físico a
    un dict ``{reference: digest}``.
    """

    def __init__(
        self,
        repos: dict[str, list[str]] | None = None,
        digests: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._repos = repos or {}
        self._digests = digests or {}

    def list_tags(self, repo: str) -> list[str]:
        return list(self._repos.get(repo, []))

    def get_digest(self, repo: str, reference: str) -> str | None:
        return self._digests.get(repo, {}).get(reference)


class FakeMalware:
    """Escáner de malware simulado: devuelve siempre un veredicto fijo."""

    def __init__(
        self, verdict: MalwareVerdict = MalwareVerdict.PENDING, infected_files: tuple[str, ...] = ()
    ) -> None:
        self._result = MalwareResult(verdict, infected_files)

    def evaluate(self, repo_path: str, tag: str) -> MalwareResult:
        return self._result


class ImageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.sqlite3")
        self.settings = Settings(
            zot_url="http://zot:5000",
            upstream_registries=frozenset({"docker.io"}),
            state_dir=Path(self._tmp.name),
            umoci_bin="umoci",
            clamdscan_bin="clamdscan",
            clamd_config="/etc/clamd.d/scan.conf",
            log_level="INFO",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def service(
        self,
        repos: dict[str, list[str]] | None = None,
        digests: dict[str, dict[str, str]] | None = None,
        malware: FakeMalware | None = None,
    ) -> ImageService:
        return ImageService(self.settings, FakeZot(repos, digests), self.state, malware or FakeMalware())

    def ref(self, reference: str) -> ImageRef:
        return ImageRef(RepoName("docker.io", "library/nginx"), reference)

    def test_tag_served_from_verified_without_touching_upstream(self):
        service = self.service({"verified/docker.io/library/nginx": ["latest"]})
        with mock.patch("zqp.skopeo.inspect_digest") as inspect, \
             mock.patch("zqp.quarantine.ensure_quarantined") as ensure:
            target = service.locate(self.ref("latest"))
        self.assertEqual((target.source, target.reference), (ServeSource.VERIFIED, "latest"))
        inspect.assert_not_called()
        ensure.assert_not_called()

    def test_tag_not_verified_is_served_literal_and_quarantined(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.inspect_digest", return_value="sha256:abc") as inspect, \
             mock.patch("app.service._spawn", side_effect=lambda fn: fn()), \
             mock.patch("zqp.quarantine.ensure_quarantined") as ensure:
            target = service.locate(self.ref("latest"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "latest"))
        inspect.assert_called_once_with("docker.io/library/nginx:latest")
        ensure.assert_called_once()
        args = ensure.call_args.args
        self.assertEqual(args[1], "zot:5000")
        self.assertIs(args[2], self.state)
        self.assertEqual(args[3].upstream_image, "docker.io/library/nginx")
        self.assertEqual(args[4], "latest")
        self.assertEqual(args[5], "sha256:abc")

    def test_partial_tag_is_served_literal_not_resolved(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.inspect_digest", return_value="sha256:abc"), \
             mock.patch("app.service._spawn", side_effect=lambda fn: fn()), \
             mock.patch("zqp.quarantine.ensure_quarantined"):
            target = service.locate(self.ref("1.27"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "1.27"))

    def test_verified_digest_is_not_re_quarantined(self):
        service = self.service(
            {}, digests={"verified/docker.io/library/nginx": {"latest": "sha256:abc"}}
        )
        with mock.patch("zqp.skopeo.inspect_digest", return_value="sha256:abc") as inspect, \
             mock.patch("app.service._spawn", side_effect=lambda fn: fn()), \
             mock.patch("zqp.quarantine.ensure_quarantined") as ensure:
            target = service.locate(self.ref("latest"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "latest"))
        inspect.assert_called_once()
        ensure.assert_not_called()

    def test_digest_served_from_verified_when_present(self):
        digest = "sha256:abc"
        service = self.service(
            {}, digests={"verified/docker.io/library/nginx": {digest: digest}}
        )
        target = service.locate(self.ref(digest))
        self.assertEqual((target.source, target.reference), (ServeSource.VERIFIED, digest))

    def test_digest_served_from_upstream_when_absent(self):
        service = self.service({})
        target = service.locate(self.ref("sha256:abc"))
        self.assertEqual((target.source, target.reference), (ServeSource.UPSTREAM, "sha256:abc"))

    def test_quarantine_copy_is_deduplicated_while_in_flight(self):
        service = self.service({})
        with mock.patch("zqp.skopeo.inspect_digest", return_value="sha256:abc"), \
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

    def test_fast_track_promotes_when_malware_clean(self):
        service = self.service({}, malware=FakeMalware(MalwareVerdict.CLEAN))
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        with mock.patch("zqp.quarantine.promote_to_verified") as promote, \
             mock.patch("zqp.quarantine.remove_from_quarantine") as remove:
            service._fast_track(self.ref("latest").repo, "sha256:abc")
        promote.assert_called_once()
        remove.assert_not_called()

    def test_fast_track_deletes_when_malware_infected(self):
        service = self.service({}, malware=FakeMalware(MalwareVerdict.INFECTED, ("/x", "/y")))
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        with mock.patch("zqp.quarantine.promote_to_verified") as promote, \
             mock.patch("zqp.quarantine.remove_from_quarantine") as remove:
            service._fast_track(self.ref("latest").repo, "sha256:abc")
        promote.assert_not_called()
        remove.assert_called_once()

    def test_fast_track_does_nothing_when_malware_pending(self):
        service = self.service({}, malware=FakeMalware(MalwareVerdict.PENDING))
        with mock.patch("zqp.quarantine.promote_to_verified") as promote, \
             mock.patch("zqp.quarantine.remove_from_quarantine") as remove:
            service._fast_track(self.ref("latest").repo, "sha256:abc")
        promote.assert_not_called()
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
