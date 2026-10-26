"""Tests del arranque dual-stack del proxy (``app.server``).

Ejecutar desde la raíz del repo:

    PYTHONPATH=shared:proxy python -m unittest discover -s tests -v
"""

import socket
import unittest

from app import server


class CreateSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self._sock: socket.socket | None = None

    def tearDown(self) -> None:
        if self._sock is not None:
            self._sock.close()

    def test_creates_ipv6_dual_stack_socket(self) -> None:
        self._sock = server.create_socket("::", 0)
        self.assertEqual(self._sock.family, socket.AF_INET6)
        self.assertEqual(self._sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 0)

    def test_binds_to_ipv6_wildcard(self) -> None:
        self._sock = server.create_socket("::", 0)
        self.assertEqual(self._sock.getsockname()[0], "::")

    def test_binds_to_an_ephemeral_port(self) -> None:
        self._sock = server.create_socket("::", 0)
        self.assertGreater(self._sock.getsockname()[1], 0)


class BuildConfigTests(unittest.TestCase):
    def test_points_to_app_main_with_dual_stack_defaults(self) -> None:
        config = server.build_config()
        self.assertEqual(config.app, "app.main:app")
        self.assertEqual(config.host, "::")
        self.assertEqual(config.port, 5001)


if __name__ == "__main__":
    unittest.main()
