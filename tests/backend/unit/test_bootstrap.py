from concurrent.futures import ThreadPoolExecutor

import pytest

from jusi.application.plugin_workers import PluginWorkerManager
from jusi.application.ports import KernelExecutionResult, PluginHandoff
from jusi.application.supervisor import Supervisor, SupervisorError
from test_supervisor import FakeKernel, FakeFactory, FakeDiscovery, FakePluginWorkerFactory, plugin_entry


def setup_bootstrap(*, supported=True, result=None):
    class Kernel(FakeKernel):
        calls = []

        def bootstrap(self, magic, arguments, **kwargs):
            self.calls.append((magic, arguments))
            return self.result

    handoff = PluginHandoff('exact_sql', '1.0.0', 'sql', 'sql', {'body': ''})
    kernel = Kernel(result or KernelExecutionResult('succeeded', handoffs=(handoff,)))
    plugin = plugin_entry()
    if supported:
        plugin['families'][0]['capabilities'].append('bootstrap')
    workers = FakePluginWorkerFactory()
    supervisor = Supervisor(FakeFactory(kernel), FakeDiscovery(plugins=[plugin]), PluginWorkerManager(workers))
    start = supervisor.start_kernel(notebook_id='nb', kernel_name='python3', trace_id='trace_start')
    args = dict(kernel_id=start['kernel']['kernel_id'], notebook_id='nb', cell_id='cell',
                code='%%sql main\nNEVER SUBMIT THIS', bootstrap=True, trace_id='trace_bootstrap')
    return supervisor, kernel, workers, args


def test_bootstrap_discards_body_and_concurrent_repeats_reuse_client():
    supervisor, kernel, workers, args = setup_bootstrap()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: supervisor.execute(**args), range(2)))
    assert kernel.calls == [('sql', 'main')]
    assert len(workers.specs) == 1
    client = supervisor.health()['clients'][0]
    assert any(result.get('client', {}).get('client_id') == client['client_id'] for result in results)
    assert any(result.get('execution', {}).get('client_id') == client['client_id'] for result in results)
    assert 'NEVER SUBMIT' not in str(supervisor.health())
    for code in ['%%sql other', '%%sql main --different']:
        with pytest.raises(SupervisorError) as error:
            supervisor.execute(**{**args, 'code': code})
        assert error.value.failure.reason == 'conflict'
    assert len(workers.specs) == 1
    supervisor.close_client(client_id=client['client_id'], trace_id='trace_close')
    supervisor.execute(**args)
    assert len(workers.specs) == 2


def test_bootstrap_reuses_client_created_by_ordinary_execution():
    supervisor, kernel, workers, args = setup_bootstrap()
    first = supervisor.execute(**{**args, 'bootstrap': False})
    second = supervisor.execute(**args)
    assert second['client']['client_id'] == first['execution']['client_id']
    assert not kernel.calls and len(workers.specs) == 1


@pytest.mark.parametrize('code', ['%%sql main', 'print(1)', ''])
def test_unsupported_bootstrap_never_executes(code):
    supervisor, kernel, workers, args = setup_bootstrap(supported=False)
    with pytest.raises(SupervisorError) as error:
        supervisor.execute(**{**args, 'code': code})
    assert error.value.failure.reason == 'unsupported'
    assert not kernel.calls and not workers.specs
    assert supervisor.health()['kernel']['state'] == 'on'


def test_missing_handoff_fails_locally_and_can_retry():
    supervisor, kernel, workers, args = setup_bootstrap(result=KernelExecutionResult('succeeded'))
    with pytest.raises(SupervisorError) as error:
        supervisor.execute(**args)
    assert error.value.failure.reason == 'protocol_violation'
    assert supervisor.health()['kernel']['state'] == 'on'
    assert not workers.specs


@pytest.mark.parametrize('body', ['', '\n', '\n \t\n'])
def test_empty_execution_uses_authoritative_catalog_without_frontend_flag(body):
    supervisor, kernel, workers, args = setup_bootstrap()
    result = supervisor.execute(**{**args, 'bootstrap': False, 'code': '%%sql main' + body})
    assert result['execution']['client_id']
    assert kernel.calls == [('sql', 'main')]
    assert len(workers.specs) == 1


def test_restart_discards_initialized_client_and_header_binding():
    supervisor, kernel, workers, args = setup_bootstrap()
    first = supervisor.execute(**args)['execution']['client_id']
    health = supervisor.health()
    restarted = supervisor.restart_notebook(runtime_id=health['runtime']['runtime_id'],
        kernel_id=args['kernel_id'], notebook_id='nb', next_notebook_id='nb_new',
        kernel_name='python3', trace_id='trace_restart')
    assert first not in supervisor._client_headers
    result = supervisor.execute(**{**args, 'kernel_id': restarted['kernel']['kernel_id'], 'notebook_id': 'nb_new'})
    assert result['execution']['client_id'] != first


def test_bootstrap_preserves_literal_arguments():
    supervisor, kernel, _, args = setup_bootstrap()
    supervisor.execute(**{**args, 'code': '%%sql main\\ \nprivate draft'})
    assert kernel.calls == [('sql', 'main\\ ')]
