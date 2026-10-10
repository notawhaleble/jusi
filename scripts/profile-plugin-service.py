"""Development-only timing wrappers; launch through profile-plugin-cells.lua."""
import asyncio
import functools
import json
import os
import threading
import time
from pathlib import Path

from jusi.application.events import EventLog
from jusi.application.editor_actions import EditorActionManager
from jusi.infrastructure.editor_action_socket import LocalEditorActionBroker, _Endpoint
from jusi.application.supervisor import Supervisor
from jusi.application.terminal_surfaces import TerminalSurfaceManager
from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernel, ManagedJupyterKernelFactory
from jusi.infrastructure.plugin_discovery import FreshProcessPluginCatalogDiscovery
from jusi.infrastructure.plugin_worker import FreshProcessPluginWorker, FreshProcessPluginWorkerFactory
from jusi.infrastructure.terminal_pty import PosixTerminalBroker, PosixTerminalHandle

log = Path(os.environ['JUSI_PROFILE_OUTPUT']) / 'service.jsonl'
log.write_text('')
lock = threading.Lock()


def wrap(cls, name):
    original = getattr(cls, name)
    @functools.wraps(original)
    def timed(*args, **kwargs):
        start = time.monotonic_ns()
        outcome = 'ok'
        try:
            return original(*args, **kwargs)
        except BaseException:
            outcome = 'error'
            raise
        finally:
            end = time.monotonic_ns()
            record = dict(stage=f'{cls.__name__}.{name}', start_ns=start,
                          end_ns=end, ms=(end-start)/1e6, outcome=outcome)
            with lock, log.open('a') as stream:
                stream.write(json.dumps(record) + '\n')
    setattr(cls, name, timed)


for cls, names in (
    (Supervisor, ('start_kernel', 'execute', 'close_client', 'terminal_surface_failed', 'stop_kernel', '_retire_client', 'close')),
    (EditorActionManager, ('close_client',)),
    (EventLog, ('wait_after',)),
    (LocalEditorActionBroker, ('close_client',)),
    (_Endpoint, ('close',)),
    (FreshProcessPluginCatalogDiscovery, ('discover',)),
    (ManagedJupyterKernelFactory, ('start',)),
    (ManagedJupyterKernel, ('execute', 'stop')),
    (FreshProcessPluginWorkerFactory, ('start',)),
    (FreshProcessPluginWorker, ('request', 'stop', '_finish_readers')),
    (TerminalSurfaceManager, ('attach', 'close')),
    (PosixTerminalBroker, ('start',)),
    (PosixTerminalHandle, ('stop',)),
):
    for name in names:
        wrap(cls, name)

def wrap_async(cls, name):
    original = getattr(cls, name)
    @functools.wraps(original)
    async def timed(*args, **kwargs):
        start = time.monotonic_ns()
        try:
            return await original(*args, **kwargs)
        finally:
            end = time.monotonic_ns()
            with lock, log.open('a') as stream:
                stream.write(json.dumps(dict(stage=f'{cls.__name__}.{name}',
                    start_ns=start, end_ns=end, ms=(end-start)/1e6)) + '\n')
    setattr(cls, name, timed)

from tornado.httpserver import HTTPServer
wrap_async(HTTPServer, 'close_all_connections')
wrap_async(asyncio.BaseEventLoop, 'shutdown_default_executor')

from jusi.__main__ import main
raise SystemExit(main())
