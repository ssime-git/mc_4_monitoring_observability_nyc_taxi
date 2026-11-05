import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

RULES = Path('services/prometheus/alert_rules.yml')


def all_alerts():
    return [r for g in yaml.safe_load(RULES.read_text())['groups'] for r in g['rules'] if 'alert' in r]


def test_every_alert_has_duration_severity_message_and_action():
    for rule in all_alerts():
        name = rule['alert']
        assert rule.get('for'), f'{name}: missing for:'
        assert rule['labels']['severity'] in {'page', 'ticket'}, name
        assert rule['labels'].get('layer'), name
        assert rule['annotations']['summary'] and rule['annotations']['description'], name
        assert rule['annotations']['action'], f'{name}: no associated action'
        anchor = rule['annotations']['runbook_url'].split('#')[1]
        assert f'## {name}'.lower() in Path('docs/RUNBOOKS.md').read_text().lower(), f'{name}: no runbook section {anchor}'


def test_each_layer_is_covered():
    names = {r['alert'] for r in all_alerts()}
    assert {'HighLatencyP99', 'HighErrorRate', 'DataDriftDetected', 'BusinessThroughputLow', 'ServiceDown'} <= names


def test_prometheus_loads_rules_and_talks_to_alertmanager():
    config = yaml.safe_load(Path('services/prometheus/prometheus.yml').read_text())
    assert 'alert_rules.yml' in config['rule_files']
    assert config['alerting']['alertmanagers'][0]['static_configs'][0]['targets'] == ['alertmanager:9093']
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    assert any('alert_rules.yml' in v for v in compose['services']['prometheus']['volumes'])


def test_alertmanager_has_a_test_receiver():
    config = yaml.safe_load(Path('services/alertmanager/alertmanager.yml').read_text())
    assert config['receivers'][0]['webhook_configs'][0]['url'].startswith('http://alert_webhook:')


@pytest.mark.skipif(shutil.which('docker') is None, reason='docker not available')
def test_promtool_unit_tests_for_rules():
    cmd = ['docker', 'run', '--rm', '--entrypoint', 'promtool', '-v', f'{Path("services/prometheus").resolve()}:/p:ro',
           'prom/prometheus:v2.47.0']
    if subprocess.run(['docker', 'info'], capture_output=True).returncode != 0:
        pytest.skip('docker daemon not reachable')
    for args in (['check', 'rules', '/p/alert_rules.yml'], ['test', 'rules', '/p/tests/alert_rules_test.yml']):
        result = subprocess.run(cmd + args, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
