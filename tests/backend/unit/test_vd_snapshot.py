from datetime import date, datetime, time, timedelta
from decimal import Decimal
import math

import pytest

from jusi.plugins.vd.snapshot import SnapshotError, Table, capture, restore


def test_snapshot_preserves_nested_data_and_non_string_mapping_keys():
    value = {("α", 2): [None, True, 123, 2.5, b"bytes", {1, 2}, frozenset({3})],
             "dates": [date(2026, 9, 9), datetime(2026, 9, 9, 12), time(1, 2), timedelta(days=2)],
             "decimal": Decimal("12.300")}
    assert restore(capture(value)) == value
    assert math.isnan(restore(capture(float("nan"))))
    assert restore(capture(float("inf"))) == float("inf")


def test_snapshot_rejects_cycles_and_custom_code():
    cycle = []; cycle.append(cycle)
    for value in [cycle, object()]:
        with pytest.raises(SnapshotError):
            capture(value)
    with pytest.raises(SnapshotError):
        restore({"snapshot_version": 1, "value": {"type": "import", "value": "os.system"}})
    with pytest.raises(SnapshotError):
        restore({"snapshot_version": 1, "value": {"type": "table", "columns": ["a"], "index_name": "i", "rows": [None]}})


def test_pandas_snapshot_preserves_index_duplicate_columns_and_nested_values():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame([[1, {"x": [2, 3]}], [4, None]], columns=["same", "same"], index=["a", "b"])
    frame.index.name = "row"
    value = restore(capture(frame))
    assert isinstance(value, Table)
    assert value.columns == ["row", "same", "same"]
    assert value.rows == [["a", 1, {"x": [2, 3]}], ["b", 4, None]]
    series = restore(capture(pd.Series([pd.NA, pd.Timestamp("2026-09-09T00:00:00.123456789")], name="when")))
    assert series.columns == ["index", "when"]
    assert series.rows == [[0, None], [1, "2026-09-09T00:00:00.123456789"]]


def test_missing_visidata_fails_before_allocating_a_client_directory(monkeypatch):
    from jusi.plugin_api import OperationRejected
    from jusi.plugins.vd.worker import VdWorker
    monkeypatch.setattr("jusi.plugins.vd.worker.find_spec", lambda name: None)
    worker = VdWorker(None)
    with pytest.raises(OperationRejected, match="jusi\\[vd\\]"):
        worker.handle("execute", capture(None))
    assert worker.directory is None
    worker.close()


def test_snapshot_accepts_large_values_and_many_nodes():
    for value in ["α" * 800000, list(range(150000))]:
        assert restore(capture(value)) == value
