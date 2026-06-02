#!/usr/bin/env python3
"""Small TLS TCP proxy for HTTPS/WSS termination.

Defaults to listen on 127.0.0.1; pass --bind-public (or set --listen-host explicitly to
0.0.0.0 / :: ) to expose on all interfaces. Validates cert/key existence, requires TLSv1.2+,
logs backend connection failures, and shuts down cleanly on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import ssl
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            if not writer.is_closing():
                writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    peer = ""
    try:
        peer = str(client_writer.get_extra_info("peername") or "")
    except Exception:
        pass
    try:
        backend_reader, backend_writer = await asyncio.open_connection(target_host, target_port)
    except Exception as exc:
        print(
            f"[tls_tcp_proxy] backend connect failed peer={peer} target={target_host}:{target_port} err={exc!r}",
            file=sys.stderr,
            flush=True,
        )
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    await asyncio.gather(
        _pipe(client_reader, backend_writer),
        _pipe(backend_reader, client_writer),
        return_exceptions=True,
    )


def _build_ssl_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    if not os.path.isfile(certfile):
        raise SystemExit(f"[tls_tcp_proxy] certfile not found: {certfile}")
    if not os.path.isfile(keyfile):
        raise SystemExit(f"[tls_tcp_proxy] keyfile not found: {keyfile}")
    if not os.access(certfile, os.R_OK):
        raise SystemExit(f"[tls_tcp_proxy] certfile not readable: {certfile}")
    if not os.access(keyfile, os.R_OK):
        raise SystemExit(f"[tls_tcp_proxy] keyfile not readable: {keyfile}")
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass
    try:
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    except ssl.SSLError as e:
        raise SystemExit(f"[tls_tcp_proxy] failed to load cert/key: {e}")
    return ctx


async def main() -> None:
    p = argparse.ArgumentParser(description="TLS terminator proxy for HTTP/WebSocket backend")
    p.add_argument("--listen-host", default=None,
                   help="Listen interface. Default: 127.0.0.1 (use --bind-public to expose on all interfaces).")
    p.add_argument("--listen-port", type=int, required=True)
    p.add_argument("--target-host", default="127.0.0.1")
    p.add_argument("--target-port", type=int, required=True)
    p.add_argument("--certfile", required=True)
    p.add_argument("--keyfile", required=True)
    p.add_argument("--bind-public", action="store_true",
                   help="Bind on 0.0.0.0 (overridden by --listen-host if explicitly given).")
    args = p.parse_args()

    if args.listen_host:
        listen_host = args.listen_host
    elif args.bind_public:
        listen_host = "0.0.0.0"
    else:
        listen_host = "127.0.0.1"

    if listen_host not in ("127.0.0.1", "::1", "localhost"):
        print(
            f"[tls_tcp_proxy] WARNING: binding to {listen_host} (non-loopback). "
            "Ensure LISA_ADMIN_TOKEN is set on the bot or you accept that admin APIs will be reachable.",
            file=sys.stderr,
            flush=True,
        )

    ctx = _build_ssl_context(args.certfile, args.keyfile)

    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, args.target_host, args.target_port),
        host=listen_host,
        port=args.listen_port,
        ssl=ctx,
    )
    sockets = server.sockets or []
    bound = ", ".join(str(s.getsockname()) for s in sockets)
    print(
        f"[tls_tcp_proxy] listening (TLS) {bound} -> {args.target_host}:{args.target_port}",
        flush=True,
    )

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _shutdown(signum, _frame=None):  # accept signal handler signature
        if not stop.done():
            print(f"[tls_tcp_proxy] received signal {signum}, shutting down", flush=True)
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _shutdown)

    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            pass
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
