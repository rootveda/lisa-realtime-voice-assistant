"""Tiny helpers for running blocking I/O off the event loop.

Why a thin wrapper over ``asyncio.to_thread``?
    * One call site to add timeouts, naming, and structured logging if we ever need it.
    * A separate :func:`run_db_blocking` that funnels SQLite calls through a single-thread
      executor, so multiple coroutines do not race on a single sqlite3 connection.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import threading
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from loguru import logger

_P = ParamSpec("_P")
_R = TypeVar("_R")


_DB_EXECUTOR_LOCK = threading.Lock()
_DB_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _db_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _DB_EXECUTOR
    if _DB_EXECUTOR is None:
        with _DB_EXECUTOR_LOCK:
            if _DB_EXECUTOR is None:
                _DB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="lisa-db"
                )
    return _DB_EXECUTOR


async def run_blocking(
    func: Callable[_P, _R],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _R:
    """Offload ``func(*args, **kwargs)`` to the default thread pool.

    Use for any blocking I/O (subprocess, ``requests``, large file reads, OpenCV decode,
    ``urlopen``, ``json.dumps`` for huge payloads, etc.) that is currently called directly
    from an ``async def`` handler.
    """
    if kwargs:
        bound = functools.partial(func, *args, **kwargs)
        return await asyncio.to_thread(bound)
    return await asyncio.to_thread(func, *args)


async def run_db_blocking(
    func: Callable[_P, _R],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _R:
    """Offload a SQLite (or otherwise non-thread-safe) call through a single worker.

    SQLite connections are bound to a single thread by default; we use one executor with
    ``max_workers=1`` so every DB call is serialized on the same thread regardless of
    how many request coroutines are interleaving.
    """
    loop = asyncio.get_running_loop()
    bound = functools.partial(func, *args, **kwargs)
    try:
        return await loop.run_in_executor(_db_executor(), bound)
    except Exception:
        logger.exception("[asyncio_helpers] DB worker raised")
        raise


def run_or_create_task(coro: Awaitable[Any], *, name: str | None = None) -> asyncio.Task[Any] | None:
    """Schedule ``coro`` on the running loop, or return ``None`` if no loop is running.

    Useful in places that want to fire-and-forget, but might also be called from sync
    contexts (e.g. shutdown hooks) where ``asyncio.get_running_loop`` would raise.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(coro)
    if name:
        try:
            task.set_name(name)
        except (AttributeError, RuntimeError):
            pass
    return task


def shutdown() -> None:
    """Clean up the dedicated DB executor (idempotent)."""
    global _DB_EXECUTOR
    with _DB_EXECUTOR_LOCK:
        if _DB_EXECUTOR is not None:
            _DB_EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _DB_EXECUTOR = None
