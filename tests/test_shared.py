"""Tests de la lógica pura compartida. Ejecutar desde la raíz del repo:

    PYTHONPATH=shared python -m unittest discover -s tests -v
"""

import unittest

from zqp.env import registry_host
from zqp.image_ref import RepoName
from zqp.versions import TagKind, alias_targets, parse_tag, pick_tag, tags_to_keep


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


class TagClassificationTests(unittest.TestCase):
    def test_kinds(self):
        self.assertIs(parse_tag("latest").kind, TagKind.FLOATING)
        self.assertIs(parse_tag("stable").kind, TagKind.FLOATING)
        self.assertIs(parse_tag("1").kind, TagKind.PARTIAL)
        self.assertIs(parse_tag("1.27").kind, TagKind.PARTIAL)
        self.assertIs(parse_tag("1.27.4").kind, TagKind.EXACT)
        self.assertIs(parse_tag("1.27.4-alpine").kind, TagKind.EXACT)
        self.assertIs(parse_tag("alpine").kind, TagKind.EXACT)


class PickTagTests(unittest.TestCase):
    TAGS = ["1.26.9", "1.27.3", "1.27.4", "1.27.4-alpine", "2.0.0", "2.1.0", "alpine", "latest"]

    def test_partial_minor_picks_highest_patch(self):
        self.assertEqual(pick_tag(parse_tag("1.27"), self.TAGS), "1.27.4")

    def test_partial_major_picks_highest_minor(self):
        self.assertEqual(pick_tag(parse_tag("1"), self.TAGS), "1.27.4")

    def test_partial_ignores_suffixed_variants(self):
        self.assertNotEqual(pick_tag(parse_tag("1.27"), ["1.27.4-alpine"]), "1.27.4-alpine")

    def test_floating_picks_highest_overall(self):
        self.assertEqual(pick_tag(parse_tag("latest"), self.TAGS), "2.1.0")

    def test_floating_falls_back_to_literal_without_semver(self):
        self.assertEqual(pick_tag(parse_tag("latest"), ["latest", "alpine"]), "latest")

    def test_exact_only_when_present(self):
        self.assertEqual(pick_tag(parse_tag("1.27.3"), self.TAGS), "1.27.3")
        self.assertIsNone(pick_tag(parse_tag("1.27.99"), self.TAGS))

    def test_no_match_returns_none(self):
        self.assertIsNone(pick_tag(parse_tag("3"), self.TAGS))


class RetentionTests(unittest.TestCase):
    def test_keeps_one_version_per_requested_partial_and_floating(self):
        tags = ["1.26.9", "1.27.3", "1.27.4", "2.0.0", "2.1.0"]
        specs = [parse_tag("1.27"), parse_tag("2"), parse_tag("latest")]
        self.assertEqual(tags_to_keep(tags, specs), {"1.27.4", "2.1.0"})

    def test_exact_request_is_always_kept(self):
        tags = ["1.27.3", "1.27.4"]
        self.assertEqual(tags_to_keep(tags, [parse_tag("1.27.3")]), {"1.27.3"})

    def test_alias_targets_skip_exact_and_literals(self):
        tags = ["1.27.4", "2.1.0"]
        specs = [parse_tag("latest"), parse_tag("1.27"), parse_tag("1.27.4")]
        self.assertEqual(alias_targets(tags, specs), {"latest": "2.1.0", "1.27": "1.27.4"})


class RegistryHostTests(unittest.TestCase):
    def test_strips_scheme(self):
        self.assertEqual(registry_host("http://localhost:5000"), "localhost:5000")


if __name__ == "__main__":
    unittest.main()
