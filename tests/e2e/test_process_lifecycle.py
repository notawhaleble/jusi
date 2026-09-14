"""Destructive probes own isolated editors/services and their exact descendants."""
from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import select
import threading
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from test_walking_skeleton import command, request_json, require_loopback_bind

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('condition did not become true within deadline')


def running(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class Editor:
    def __init__(self, tmp_path, mode, *, external=None):
        require_loopback_bind()
        self.root = tmp_path
        self.metadata = tmp_path / 'ready.json'
        self.trigger = tmp_path / 'quit'
        self.children = []
        home = tmp_path / 'home'; home.mkdir()
        env = {**os.environ, 'HOME': str(home), 'IPYTHONDIR': str(home / 'ipython'),
               'JUPYTER_RUNTIME_DIR': str(home / 'jupyter'),
               'PYTHONPATH': str(ROOT / 'tests/fixtures/terminal_plugin')}
        source = {'idle': '1', 'sleep': "import time; print('started', flush=True); time.sleep(600)",
                  'stubborn': "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('started', flush=True); time.sleep(600)",
                  'input': "input('waiting: ')", 'client': '%%terminal_fixture\nactive client', 'followup': '%%terminal_fixture\nactive client'}[mode]
        config = {'kind': 'remote', 'base_url': external} if external else {
            'kind': 'local', 'command': [str(ROOT / '.venv/bin/python'), '-m', 'jusi', 'serve']}
        config['kernel_name'] = 'python3'
        config['terminal_bridge_command'] = [str(ROOT / '.venv/bin/python'), '-m', 'jusi', 'terminal-bridge']
        lua = '''
local j = require('jusi')
local function wait(p) assert(vim.wait(60000,p,20),'editor wait timed out') end
j.setup({targets={test=vim.json.decode(CONFIG)}})
local buf=vim.api.nvim_get_current_buf()
vim.bo[buf].filetype='jusi'
vim.api.nvim_buf_set_lines(buf,0,-1,false,vim.list_extend({'╭──'},vim.list_extend(vim.split(SOURCE,'\\n',{plain=true}),{'╰──'})))
local ticket=j.start('test')
wait(function() return j._sessions[buf] and j._sessions[buf].controller.kernel_state=='on' and not j._starts[buf] end)
local s=j._sessions[buf]
local starts, finishes, kernel_died = 0, 0, false
s.controller.on_event=function(e)
  if e.kind=='execution.started' then starts=starts+1 end
  if e.kind=='execution.completed' then finishes=finishes+1 end
  if e.kind=='failure.occurred' and e.payload.reason=='kernel_died' then kernel_died=true end
end
if MODE~='idle' then
  j.execute(buf,1)
  wait(function()
    if MODE=='input' then return s.controller.pending_input~=nil end
    if MODE=='client' or MODE=='followup' then
      for _,r in pairs(s.interactive.surfaces) do return r.job_id and r.buf and #vim.api.nvim_buf_get_lines(r.buf,0,-1,false)>1 end
      return false
    end
    local output=s.presentation:buffer_for_cell(s.model:cell_at_row(1).id)
    return output and table.concat(vim.api.nvim_buf_get_lines(output,0,-1,false),'\\n'):find('started',1,true)~=nil
  end)
end
if MODE=='followup' then
  vim.api.nvim_buf_set_lines(buf,1,3,false,{'fixture:wait'})
  j.submit(buf,1)
  wait(function() return next(s.controller.client_operations)~=nil end)
end
vim.fn.writefile({vim.json.encode({base_url=s.base_url,kernel_id=s.controller.kernel_id,
  service_pid=s.service and s.service.pid, notebook_id=s.model.notebook_id})},META)
local resumed=false
wait(function()
  if not resumed and vim.fn.filereadable(RESUME)==1 then resumed=true; s.controller:connect() end
  vim.fn.writefile({vim.json.encode({transport=s.controller.transport_state,kernel=s.controller.kernel_state,
    starts=starts,finishes=finishes,kernel_died=kernel_died,requests=vim.tbl_count(s.controller.transport.requests),
    runtime=s.controller.runtime_id,supervisor=s.controller.supervisor_id,
    status=require('jusi.statusline').render(vim.fn.win_findbuf(buf)[1])})},STATE)
  return vim.fn.filereadable(TRIGGER)==1
end)
vim.cmd('qa!')
'''
        for key, value in {'CONFIG': json.dumps(config), 'SOURCE': source, 'MODE': mode,
                           'META': str(self.metadata), 'TRIGGER': str(self.trigger),
                           'STATE': str(tmp_path / 'state.json'), 'RESUME': str(tmp_path / 'resume')}.items():
            lua = lua.replace(key, json.dumps(value))
        script = tmp_path / 'editor.lua'; script.write_text(lua)
        self.log = (tmp_path / 'editor.log').open('w+')
        self.process = subprocess.Popen(['nvim', '--headless', '-u', 'tests/frontend/minimal_init.lua', '-l', str(script)],
                                        cwd=ROOT, env=env, stdout=self.log, stderr=self.log)
        self.editor = psutil.Process(self.process.pid)
        try:
            wait_for(lambda: self.metadata.exists() or self.process.poll() is not None, 20)
            assert self.metadata.exists(), self.read_log()
            self.info = json.loads(self.metadata.read_text())
            self.children = self.editor.children(recursive=True)
        except BaseException:
            self.close()
            raise

    def state(self):
        try:
            return json.loads((self.root / 'state.json').read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def read_log(self):
        self.log.flush(); self.log.seek(0); return self.log.read()

    def close(self):
        try:
            self.children += self.editor.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        for child in reversed(self.children + [self.editor]):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        self.process.wait(timeout=5)
        self.log.close()


@pytest.mark.e2e
@pytest.mark.parametrize('mode', ['idle', 'sleep', 'input', 'client', 'followup', 'stubborn'])
@pytest.mark.parametrize('exit_kind', ['quit', 'kill'])
def test_owned_editor_exit_cleans_descendants(tmp_path, mode, exit_kind):
    editor = Editor(tmp_path, mode)
    try:
        if exit_kind == 'quit':
            editor.trigger.touch()
        else:
            editor.process.kill()
        editor.process.wait(timeout=10)
        try:
            wait_for(lambda: not any(running(p) for p in editor.children), 12)
        except AssertionError:
            survivors = [(p.pid, p.cmdline()) for p in editor.children if running(p)]
            pytest.fail(f'orphaned descendants: {survivors}; editor log: {editor.read_log()}')
    finally:
        editor.close()


class Service:
    def __init__(self, tmp_path):
        require_loopback_bind()
        self.root = tmp_path
        home = tmp_path / 'service-home'; home.mkdir()
        self.stdout = (tmp_path / 'service.out').open('w+')
        self.stderr = (tmp_path / 'service.err').open('w+')
        env = {**os.environ, 'HOME': str(home), 'IPYTHONDIR': str(home / 'ipython'),
               'JUPYTER_RUNTIME_DIR': str(home / 'jupyter'),
               'PYTHONPATH': str(ROOT / 'tests/fixtures/terminal_plugin')}
        self.process = subprocess.Popen([sys.executable, '-m', 'jusi', 'serve', '--port', '0'],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=self.stdout, stderr=self.stderr)
        self.identity = psutil.Process(self.process.pid)
        self.children = []
        def ready():
            self.stdout.seek(0)
            line = self.stdout.readline()
            return json.loads(line) if line.endswith('\n') else None
        try:
            data = wait_for(ready)
            self.base_url = f"http://{data['host']}:{data['port']}"
        except BaseException:
            self.close()
            raise

    def capture(self):
        self.children += self.identity.children(recursive=True)

    def close(self):
        try:
            self.capture()
            self.process.terminate()
            self.process.wait(timeout=10)
        except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
            self.process.kill(); self.process.wait(timeout=5)
        for child in reversed(self.children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        self.stdout.close(); self.stderr.close()


@pytest.mark.e2e
@pytest.mark.parametrize('mode', ['idle', 'sleep', 'input', 'client', 'followup', 'stubborn'])
@pytest.mark.parametrize('death', [signal.SIGTERM, signal.SIGKILL])
def test_service_death_retires_owned_processes(tmp_path, mode, death):
    editor = Editor(tmp_path, mode)
    try:
        service = psutil.Process(editor.info['service_pid'])
        descendants = service.children(recursive=True)
        service.send_signal(death)
        wait_for(lambda: not running(service), 12)
        wait_for(lambda: editor.state().get('transport') == 'disconnected', 12)
        try:
            wait_for(lambda: not any(running(p) for p in descendants), 12)
        except AssertionError:
            pytest.fail(f'service death orphaned {[(p.pid, p.cmdline()) for p in descendants if running(p)]}')
    finally:
        editor.close()


@pytest.mark.e2e
@pytest.mark.parametrize('mode', ['idle', 'sleep', 'input', 'client', 'followup', 'stubborn'])
def test_kernel_death_becomes_off_and_allows_new_generation(tmp_path, mode):
    editor = Editor(tmp_path, mode)
    try:
        url = editor.info['base_url']
        health = lambda: request_json(url, '/v1/health', method='GET')
        before = health()
        kernel = psutil.Process(before['kernel']['pid'])
        kernel.kill()
        wait_for(lambda: health()['kernel']['state'] == 'off', 8)
        wait_for(lambda: not health()['clients'] and not health()['surfaces'] and not health()['executions'], 8)
        wait_for(lambda: editor.state().get('kernel') == 'off', 8)
        assert editor.state()['kernel_died'], 'kernel death did not reach frontend diagnostics'
        started = request_json(url, '/v1/kernels', method='POST', payload=command('start_kernel','trace_new',
            notebook_id=editor.info['notebook_id'], kernel_name='python3', idempotency_key='new_after_death'))
        assert started['kernel']['kernel_id'] != before['kernel']['kernel_id']
        assert started['kernel']['state'] == 'on'
        editor.children.append(psutil.Process(started['kernel']['pid']))
        executed = request_json(url, f"/v1/kernels/{started['kernel']['kernel_id']}/executions",
            method='POST', payload=command('execute', 'trace_after_death',
                kernel_id=started['kernel']['kernel_id'], notebook_id=editor.info['notebook_id'],
                cell_id='cell_after_death', code='1 + 1'))
        assert executed['execution']['outcome'] == 'succeeded'
    finally:
        editor.close()


@pytest.mark.e2e
@pytest.mark.parametrize('mode', ['idle', 'sleep', 'input', 'client', 'followup', 'stubborn'])
@pytest.mark.parametrize('exit_kind', ['quit', 'kill'])
def test_external_service_survives_editor_exit(tmp_path, mode, exit_kind):
    service = Service(tmp_path)
    editor = None
    try:
        editor = Editor(tmp_path, mode, external=service.base_url)
        service.capture()
        before = request_json(service.base_url, '/v1/health', method='GET')
        if exit_kind == 'quit':
            editor.trigger.touch()
        else:
            editor.process.kill()
        editor.process.wait(timeout=10)
        wait_for(lambda: not any(running(p) for p in editor.children), 8)
        after = request_json(service.base_url, '/v1/health', method='GET')
        assert after['kernel']['state'] == 'on'
        assert after['kernel']['kernel_id'] == before['kernel']['kernel_id']
        assert after['runtime']['runtime_id'] == before['runtime']['runtime_id']
        assert [c['client_id'] for c in after['clients']] == [c['client_id'] for c in before['clients']]
        if mode == 'followup':
            assert after['client_operations'] == before['client_operations']
        if mode in {'sleep', 'input', 'stubborn'}:
            assert after['executions'][0]['execution_id'] == before['executions'][0]['execution_id']
        request_json(service.base_url, f"/v1/kernels/{after['kernel']['kernel_id']}", method='DELETE',
            payload=command('stop_kernel','trace_external_stop',kernel_id=after['kernel']['kernel_id']))
        assert request_json(service.base_url, '/v1/health', method='GET')['kernel']['state'] == 'off'
    finally:
        if editor:
            editor.close()
        service.close()


class CuttableProxy:
    """Loopback stand-in for a lost tunnel; target service remains untouched."""
    def __init__(self, target):
        port = int(target.rsplit(':', 1)[1])
        self.down = threading.Event()
        self.blackhole = threading.Event()
        proxy = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if proxy.down.is_set():
                    return
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=2) as upstream:
                        peers = {self.request: upstream, upstream: self.request}
                        while not proxy.down.is_set():
                            ready, _, _ = select.select(list(peers), [], [], .05)
                            for peer in ready:
                                chunk = peer.recv(65536)
                                if not chunk:
                                    return
                                if not proxy.blackhole.is_set():
                                    peers[peer].sendall(chunk)
                except OSError:
                    pass
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        self.down.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


@pytest.mark.e2e
@pytest.mark.parametrize("loss", ["disconnect", "blackhole"])
def test_transport_loss_preserves_truth_and_replays_completion_without_execution(tmp_path, loss):
    service = Service(tmp_path)
    proxy = CuttableProxy(service.base_url)
    editor = None
    try:
        editor = Editor(tmp_path, 'sleep', external=proxy.base_url)
        service.capture()
        before = request_json(service.base_url, '/v1/health', method='GET')
        (proxy.down if loss == "disconnect" else proxy.blackhole).set()
        wait_for(lambda: editor.state().get('transport') == 'disconnected', 30)
        assert editor.state()['kernel'] == 'on'
        assert 'last known' in editor.state()['status']
        wait_for(lambda: editor.state().get('requests') == 0, 5)
        execution = before['executions'][0]
        request_json(service.base_url, f"/v1/kernels/{execution['kernel_id']}/executions/{execution['execution_id']}/interrupt",
            method='POST', payload=command('interrupt','trace_during_loss',kernel_id=execution['kernel_id'],execution_id=execution['execution_id']))
        wait_for(lambda: not request_json(service.base_url, '/v1/health', method='GET')['executions'])
        proxy.down.clear(); proxy.blackhole.clear()
        (tmp_path / 'resume').touch()
        wait_for(lambda: editor.state().get('transport') == 'connected' and editor.state().get('finishes') == 1)
        state = editor.state()
        assert state['starts'] == 1, 'reconnect repeated execution'
        assert state['runtime'] == before['runtime']['runtime_id']
        assert state['supervisor'] == before['supervisor_id']
        assert 'last known' not in state['status']
    finally:
        if editor:
            editor.close()
        proxy.close(); service.close()
