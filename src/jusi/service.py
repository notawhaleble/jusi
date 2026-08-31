from __future__ import annotations

import asyncio
import json
import signal
from typing import Any

import tornado.httpserver
import tornado.netutil

from jusi.application.supervisor import Supervisor
from jusi.application.plugin_workers import PluginWorkerManager
from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernelFactory
from jusi.infrastructure.plugin_discovery import FreshProcessPluginCatalogDiscovery
from jusi.infrastructure.plugin_worker import FreshProcessPluginWorkerFactory
from jusi.interfaces.http import make_application


async def serve(host: str, port: int) -> None:
    supervisor = Supervisor(
        ManagedJupyterKernelFactory(),
        FreshProcessPluginCatalogDiscovery(),
        PluginWorkerManager(FreshProcessPluginWorkerFactory()),
    )
    application = make_application(supervisor)
    sockets = tornado.netutil.bind_sockets(port, address=host)
    server = tornado.httpserver.HTTPServer(application)
    server.add_sockets(sockets)
    actual_port = int(sockets[0].getsockname()[1])
    print(
        json.dumps(
            {
                "status": "ready",
                "host": host,
                "port": actual_port,
                "supervisor_id": supervisor.supervisor_id,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    try:
        await stop_event.wait()
    finally:
        server.stop()
        await asyncio.to_thread(supervisor.close)
        await server.close_all_connections()
