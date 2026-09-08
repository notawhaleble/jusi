from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernelFactory
from jusi.protocol import validate_completion


def test_real_kernel_completion_uses_kernel_globals_and_unicode_offsets():
    kernel = ManagedJupyterKernelFactory().start("python3", timeout=8)
    try:
        kernel.execute("jusi_unique_name = 42\nαvalue = 1", timeout=5, on_output=lambda _: None)
        for prefix, expected, applied in [
            ("jusi_unique_n", "jusi_unique_name", "jusi_unique_nameSUFFIX"),
            ("from time import s", "sleep", "from time import sleepSUFFIX"),
            ("αv", "αvalue", "αvalueSUFFIX"),
            ("x = 1\njusi_unique_n", "jusi_unique_name", "x = 1\njusi_unique_nameSUFFIX"),
        ]:
            completion = validate_completion(kernel.complete(prefix, timeout=5), len(prefix))
            item = next(item for item in completion["items"] if item["text"] == expected)
            assert item["end"] == len(prefix)
            assert prefix[:item["start"]] + item["text"] + "SUFFIX" == applied
        assert kernel.execute("assert jusi_unique_name == 42", timeout=5, on_output=lambda _: None).outcome == "succeeded"
    finally:
        kernel.stop(timeout=5)
