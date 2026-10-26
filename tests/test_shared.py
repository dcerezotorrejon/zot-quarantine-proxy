"""Tests de la lógica pura compartida. Ejecutar desde la raíz del repo:

    PYTHONPATH=shared python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path

from zqp.env import registry_host
from zqp.image_ref import RepoName, digest_to_tag
from zqp.malware import _parse_infected, format_infected
from zqp.skopeo import _parse_created
from zqp.state import StateStore


class RepoNameTests(unittest.TestCase):
    def test_explicit_registry(self):
        repo = RepoName.from_name("docker.io/library/nginx")
        self.assertEqual((repo.registry, repo.path), ("docker.io", "library/nginx"))

    def test_implicit_docker_hub_library(self):
        self.assertEqual(RepoName.from_name("nginx").upstream_image, "docker.io/library/nginx")

    def test_implicit_docker_hub_user_repo(self):
        self.assertEqual(RepoName.from_name("myuser/app").upstream_image, "docker.io/myuser/app")

    def test_custom_registry_with_path(self):
        repo = RepoName.from_name("ghcr.io/owner/tool")
        self.assertEqual((repo.registry, repo.path), ("ghcr.io", "owner/tool"))

    def test_registry_with_port(self):
        self.assertEqual(RepoName.from_name("localhost:5000/team/app").registry, "localhost:5000")

    def test_physical_name(self):
        repo = RepoName.from_name("docker.io/library/nginx")
        self.assertEqual(repo.physical("verified"), "verified/docker.io/library/nginx")

    def test_invalid_names(self):
        for name in ["", "/nginx", "docker.io/", "docker.io//nginx"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                RepoName.from_name(name)


class DigestToTagTests(unittest.TestCase):
    def test_uses_q_prefix_and_strips_algorithm(self):
        self.assertEqual(digest_to_tag("sha256:abc123"), "q-abc123")

    def test_strips_only_the_algorithm(self):
        self.assertEqual(digest_to_tag("sha256:ab:cd"), "q-ab:cd")


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_quarantine_records_digest_and_reference(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        records = self.state.list_quarantine()
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].registry, records[0].path, records[0].digest), ("docker.io", "library/nginx", "sha256:abc"))
        self.assertEqual(self.state.quarantine_refs("docker.io", "library/nginx", "sha256:abc"), ["latest"])

    def test_add_quarantine_keeps_first_timestamp_for_same_digest(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "2.16.0", 300)
        records = self.state.list_quarantine()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].quarantined_at, 100)
        self.assertEqual(
            self.state.quarantine_refs("docker.io", "library/nginx", "sha256:abc"),
            ["2.16.0", "latest"],
        )

    def test_add_quarantine_stores_published_at(self):
        self.state.add_quarantine(
            "docker.io", "library/nginx", "sha256:abc", "latest", 100, published_at=50
        )
        self.assertEqual(self.state.list_quarantine()[0].published_at, 50)

    def test_clock_start_prefers_published_at(self):
        self.state.add_quarantine(
            "docker.io", "library/nginx", "sha256:abc", "latest", 100, published_at=50
        )
        self.assertEqual(self.state.list_quarantine()[0].clock_start, 50)

    def test_clock_start_falls_back_to_quarantined_at(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.assertEqual(self.state.list_quarantine()[0].clock_start, 100)

    def test_distinct_digests_are_distinct_records(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:aaa", "latest", 100)
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:bbb", "latest", 200)
        self.assertEqual(len(self.state.list_quarantine()), 2)

    def test_quarantine_refs_by_repo_groups(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "2.16.0", 100)
        self.state.add_quarantine("docker.io", "other/app", "sha256:xyz", "v1", 100)
        grouped = self.state.quarantine_refs_by_repo()
        self.assertEqual(
            sorted(grouped[("docker.io", "library/nginx")]),
            [("sha256:abc", "2.16.0"), ("sha256:abc", "latest")],
        )
        self.assertEqual(grouped[("docker.io", "other/app")], [("sha256:xyz", "v1")])

    def test_remove_quarantine_ref_keeps_digest_when_other_ref_remains(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "2.16.0", 100)
        self.state.remove_quarantine_ref("docker.io", "library/nginx", "sha256:abc", "latest")
        self.assertEqual(self.state.quarantine_refs("docker.io", "library/nginx", "sha256:abc"), ["2.16.0"])
        self.assertEqual(len(self.state.list_quarantine()), 1)

    def test_remove_quarantine_removes_digest_and_refs(self):
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.state.remove_quarantine("docker.io", "library/nginx", "sha256:abc")
        self.assertEqual(self.state.list_quarantine(), [])
        self.assertEqual(self.state.quarantine_refs("docker.io", "library/nginx", "sha256:abc"), [])

    def test_delete_repo_removes_refs_and_quarantine(self):
        self.state.upsert_ref("docker.io", "library/nginx", "latest")
        self.state.add_quarantine("docker.io", "library/nginx", "sha256:abc", "latest", 100)
        self.state.delete_repo("docker.io", "library/nginx")
        self.assertEqual(self.state.refs_by_repo(), {})
        self.assertEqual(self.state.list_quarantine(), [])
        self.assertEqual(self.state.quarantine_refs_by_repo(), {})


class ParseCreatedTests(unittest.TestCase):
    def test_parses_rfc3339_with_nanoseconds(self):
        self.assertEqual(_parse_created("2024-05-29T06:55:28.566254104Z"), 1716965728)

    def test_parses_rfc3339_without_fraction(self):
        self.assertEqual(_parse_created("2024-05-29T06:55:28Z"), 1716965728)

    def test_returns_none_for_empty_or_placeholder(self):
        for raw in ["", "   ", "<no value>", "<nil>"]:
            with self.subTest(raw=raw):
                self.assertIsNone(_parse_created(raw))

    def test_returns_none_for_garbage(self):
        self.assertIsNone(_parse_created("no-es-una-fecha"))


class MalwareFormatTests(unittest.TestCase):
    def test_parse_infected_extracts_paths(self):
        stdout = "/a/x: Eicar-Signature FOUND\n/b/y: Trojan.Something FOUND\n"
        self.assertEqual(_parse_infected(stdout), ("/a/x", "/b/y"))

    def test_parse_infected_ignores_non_found_lines(self):
        self.assertEqual(_parse_infected("/clean: OK\n/summary\n"), ())

    def test_format_infected_limits_shown_paths(self):
        files = tuple(f"/f{i}" for i in range(25))
        self.assertIn("(+5 más)", format_infected(files))
        self.assertNotIn("/f24", format_infected(files).split(" (+")[0])


class RegistryHostTests(unittest.TestCase):
    def test_strips_scheme(self):
        self.assertEqual(registry_host("http://localhost:5000"), "localhost:5000")


if __name__ == "__main__":
    unittest.main()
