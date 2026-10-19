from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernelFactory


def test_bootstrap_bypasses_empty_magic_rejection_without_executing_body():
    kernel = ManagedJupyterKernelFactory().start('python3', timeout=8)
    try:
        source = '''
from IPython.display import display
calls = []
def magic(line, body):
    raise AssertionError('ordinary magic execution must not run')
def initialize(line):
    calls.append(line)
    display({'application/vnd.jusi.handoff.v1+json': {
        'protocol_version': 1, 'kind': 'plugin.handoff', 'plugin_id': 'fixture',
        'plugin_version': '1', 'family_id': 'fixture', 'magic_name': 'fixture',
        'payload': {'arguments': line},
    }}, raw=True)
magic.__jusi_bootstrap_v1__ = initialize
get_ipython().register_magic_function(magic, 'cell', 'fixture')
'''
        assert kernel.execute(source, timeout=5, on_output=lambda _: None).outcome == 'succeeded'
        assert kernel.execute('%%fixture main', timeout=5, on_output=lambda _: None).outcome == 'failed'
        arguments = "main'; raise Exception('must remain data') #"
        result = kernel.bootstrap('fixture', arguments, timeout=5, on_output=lambda _: None)
        assert result.outcome == 'succeeded'
        assert result.handoffs[0].payload == {'arguments': arguments}
        assert kernel.execute('assert len(calls) == 1', timeout=5, on_output=lambda _: None).outcome == 'succeeded'
        missing = kernel.bootstrap('missing', '', timeout=5, on_output=lambda _: None)
        assert missing.outcome == 'failed'
        assert kernel.execute('1 + 1', timeout=5, on_output=lambda _: None).outcome == 'succeeded'
    finally:
        kernel.stop(timeout=5)
