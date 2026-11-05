import json
from pathlib import Path


def test_dashboard_panels_have_unique_ids_and_explicit_datasources():
    for path in Path('services/grafana/dashboards').glob('*.json'):
        panels = json.loads(path.read_text())['panels']
        ids = [panel['id'] for panel in panels]
        assert len(ids) == len(set(ids))
        for panel in panels:
            if panel.get('datasource', {}).get('type') == 'prometheus':
                assert panel['datasource']['uid'] == 'prometheus'
            if panel.get('datasource', {}).get('type') == 'tempo':
                assert panel['type'] == 'table'
                for target in panel['targets']:
                    assert target['queryType'] == 'traceql'
                    assert target['query']
            for target in panel.get('targets', []):
                assert 'trips_ingested_total' not in target.get('expr', '')
