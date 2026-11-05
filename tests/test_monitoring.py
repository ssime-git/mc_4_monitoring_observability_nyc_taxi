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


def test_every_data_panel_declares_a_unit_and_status_dashboard_has_thresholds():
    for path in Path('services/grafana/dashboards').glob('*.json'):
        for panel in json.loads(path.read_text())['panels']:
            if panel['type'] in ('stat', 'timeseries', 'bargauge', 'gauge'):
                assert panel['fieldConfig']['defaults'].get('unit'), f"{path.name}: '{panel['title']}' has no unit"
    status = json.loads(Path('services/grafana/dashboards/L0_status.json').read_text())
    stats = [p for p in status['panels'] if p['type'] == 'stat']
    assert len(stats) >= 6 and all(p['fieldConfig']['defaults']['thresholds']['steps'] for p in stats)


def test_promtail_labels_stay_low_cardinality():
    import yaml
    config = yaml.safe_load(Path('services/promtail/promtail-config.yml').read_text())
    stages = [s for job in config['scrape_configs'] for s in job['pipeline_stages'] if 'labels' in s]
    labels = {name for s in stages for name in s['labels']}
    assert labels and not labels & {'trace_id', 'span_id', 'request_id'}


def test_promtail_keeps_the_full_json_line():
    import yaml
    config = yaml.safe_load(Path('services/promtail/promtail-config.yml').read_text())
    stages = [s for job in config['scrape_configs'] for s in job['pipeline_stages']]
    assert not any('output' in s for s in stages), 'output stage would replace the JSON line by its message'
