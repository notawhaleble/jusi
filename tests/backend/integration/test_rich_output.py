import json
from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernelFactory, MAX_OUTPUT_EVENT_BYTES


def test_rich_bundle_preserves_alternatives_and_complete_chunk_boundaries():
    kernel = ManagedJupyterKernelFactory().start('python3', timeout=8)
    outputs = []
    try:
        result = kernel.execute("from IPython.display import display\nprint('before')\ndisplay({'text/plain': 'Figure', 'image/svg+xml': '<svg>' + 'α' * 20000 + '</svg>'}, raw=True)\nprint('after')", timeout=8, on_output=outputs.append)
        assert result.outcome == 'succeeded'
        chunks = [o for o in outputs if o.bundle_id]
        assert len(chunks) > 1
        assert len({o.bundle_id for o in chunks}) == 1
        assert [o.bundle_end for o in chunks] == [False] * (len(chunks) - 1) + [True]
        assert all(len(o.data.encode('utf-8')) <= MAX_OUTPUT_EVENT_BYTES for o in chunks)
        bundle = json.loads(''.join(o.data for o in chunks))
        assert bundle == {'text/plain': 'Figure', 'image/svg+xml': '<svg>' + 'α' * 20000 + '</svg>'}
        assert outputs[0].data == 'before\n' and outputs[-1].data == 'after\n'

        outputs.clear()
        source = 'element.textContent = "Hello </script> α";\n' + '// chunk boundary α\n' * 2000
        result = kernel.execute('%%javascript\n' + source, timeout=8, on_output=outputs.append)
        assert result.outcome == 'succeeded'
        chunks = [o for o in outputs if o.bundle_id]
        assert len(chunks) > 1 and chunks[-1].bundle_end
        javascript = json.loads(''.join(o.data for o in chunks))
        assert javascript['application/javascript'] == source
    finally:
        kernel.stop(timeout=5)
