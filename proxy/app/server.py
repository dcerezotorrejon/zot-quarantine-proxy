"""Arranque del proxy sobre un socket dual-stack (IPv4 e IPv6).

Con ``uvicorn --host ::``, asyncio crea el socket con ``IPV6_V6ONLY`` activado y
deja fuera al IPv4 (``127.0.0.1``). Este módulo crea el socket a mano con
``IPV6_V6ONLY=0`` y se lo pasa a uvicorn para que ``localhost``, ``127.0.0.1`` y
``[::1]`` respondan.
"""

from __future__ import annotations

import logging
import socket

import uvicorn

logger = logging.getLogger(__name__)

DEFAULT_HOST = "::"
DEFAULT_PORT = 5001


def create_socket(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> socket.socket:
    """Crea un socket de escucha dual-stack enlazado a ``host:port``.

    Args:
        host: Dirección IPv6 a la que enlazar. Debe ser ``::`` para aceptar
            tanto IPv4 como IPv6.
        port: Puerto en el que escuchar.

    Returns:
        Socket ``AF_INET6`` enlazado, con ``IPV6_V6ONLY`` desactivado.
    """
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    return sock


def build_config(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> uvicorn.Config:
    """Construye la configuración de uvicorn para ``app.main:app``.

    Args:
        host: Dirección a la que enlazar. Con ``sockets=[...]`` uvicorn no
            vuelve a enlazar; se conserva solo como referencia en la configuración.
        port: Puerto de escucha. Igual que ``host``, no se usa para enlazar.

    Returns:
        Configuración de uvicorn.
    """
    return uvicorn.Config("app.main:app", host=host, port=port)


def main() -> None:
    """Crea el socket dual-stack y arranca uvicorn sobre él."""
    sock = create_socket(DEFAULT_HOST, DEFAULT_PORT)
    server = uvicorn.Server(build_config(DEFAULT_HOST, DEFAULT_PORT))
    logger.info("Proxy escuchando en %s:%d (dual-stack IPv4/IPv6)", DEFAULT_HOST, DEFAULT_PORT)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
