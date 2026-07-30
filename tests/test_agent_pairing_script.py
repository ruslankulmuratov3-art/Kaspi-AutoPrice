import json

import httpx

from scripts import local_agent


def test_pair_device_saves_per_device_credentials(tmp_path, monkeypatch):
    target = tmp_path / 'agent.json'
    monkeypatch.setenv('KASPI_AGENT_CONFIG', str(target))

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        assert url.endswith('/api/local-agent/pair')
        assert json['code'].startswith('DEV-')
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'token': 'kat_secret',
            'device_id': 7,
            'device_key': 'device-key-7',
            'device_name': 'Office PC',
        })

    monkeypatch.setattr(local_agent.httpx, 'post', fake_post)
    data = local_agent.pair_device('https://example.test', 'DEV-AAAA-BBBB-CCCC', 'Office PC', 'Windows')
    assert data['token'] == 'kat_secret'
    saved = json.loads(target.read_text(encoding='utf-8'))
    assert saved['agent_id'] == 'device-key-7'
    assert saved['render_base_url'] == 'https://example.test'
