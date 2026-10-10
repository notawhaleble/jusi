"""Summarize profile directories without recording application payloads."""
import collections
import importlib.metadata
import json
import platform
from pathlib import Path
import statistics
import sys


def summarize(rows):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row['stage']].append(row['ms'])
    return {stage: dict(n=len(values), median_ms=statistics.median(values),
                       min_ms=min(values), max_ms=max(values), samples_ms=values)
            for stage, values in groups.items()}


runs = []
for directory in sys.argv[1:]:
    path = Path(directory)
    frontend = json.loads((path / 'frontend.json').read_text())
    run = {key: value for key, value in frontend.items() if key != 'rows'}
    run['frontend'] = summarize(frontend['rows'])
    service_path = path / 'service.jsonl'
    if frontend['instrument'] and service_path.exists():
        rows = [json.loads(line) for line in service_path.read_text().splitlines()]
        run['service'] = summarize(rows)
        executes = [row for row in rows if row['stage'] == 'Supervisor.execute'][frontend['samples']:]
        deltas = []
        for execute in executes:
            attach = next((row for row in rows if row['stage'] == 'TerminalSurfaceManager.attach'
                           and row['start_ns'] >= execute['end_ns']), None)
            if attach:
                deltas.append(dict(stage='execute_return_to_first_attach',
                                   ms=(attach['start_ns']-execute['end_ns'])/1e6))
        run['derived'] = summarize(deltas)
    runs.append(run)
print(json.dumps(dict(environment=dict(python=platform.python_version(), platform=platform.platform(),
    packages={name: importlib.metadata.version(name) for name in
              ('jusi', 'visidata', 'ipykernel', 'jupyter_client', 'tornado')}), runs=runs), indent=2))
