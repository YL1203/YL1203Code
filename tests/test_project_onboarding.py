from __future__ import annotations

import pytest


def test_initial_config_uses_env_reference_and_preserves_existing_file(tmp_path, monkeypatch):
    from YL1203Code.onboarding import write_initial_config
    from YL1203Code.config import load_config
    path = write_initial_config(str(tmp_path), protocol='openai-compat',
                                base_url='https://example.invalid/v1', model='test-model')
    assert '${OPENAI_API_KEY}' in path.read_text()
    config = load_config(path)
    assert config.providers[0].model == 'test-model'
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-key')
    assert config.providers[0].resolve_api_key() == 'test-only-key'
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_initial_config(str(tmp_path), protocol='anthropic', base_url='https://example.invalid', model='other')
    assert path.read_bytes() == before


def test_doctor_redacts_credentials_and_reports_bad_config(tmp_path, monkeypatch):
    from YL1203Code.onboarding import doctor_report
    from YL1203Code.config import AppConfig, ProviderConfig
    monkeypatch.setenv('OPENAI_API_KEY', 'never-print-this-key')
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy-user:proxy-secret@127.0.0.1:1')
    provider = ProviderConfig('test', 'openai', 'https://api-user:api-secret@example.invalid/v1', 'model')
    report = doctor_report(str(tmp_path), AppConfig([provider]))
    text = '\n'.join(report)
    for secret in ['never-print-this-key', 'proxy-user', 'proxy-secret', 'api-user', 'api-secret']:
        assert secret not in text
    assert '127.0.0.1:1' in text
    assert 'OPENAI_API_KEY' in text
    assert '配置' in '\n'.join(doctor_report(str(tmp_path), None, config_error='missing configuration'))


@pytest.mark.parametrize('file, content, expected', [
    ('pyproject.toml', '[project]\nname="tiny"\n', 'pytest'),
    ('pom.xml', '<project/>', 'mvn test'),
    ('package.json', '{"name":"tiny","scripts":{"test":"vitest"}}', 'npm test'),
])
def test_project_discovery_suggests_tests_without_reading_secrets(tmp_path, file, content, expected):
    from YL1203Code.project import discover_project
    (tmp_path / file).write_text(content)
    secret_dir = tmp_path / '.YL1203Code'
    secret_dir.mkdir()
    (secret_dir / 'config.local.yaml').write_text('api_key: secret')
    info = discover_project(str(tmp_path))
    assert any(expected in command for command in info.test_commands)
    assert 'secret' not in info.summary()
    assert 'config.local.yaml' not in info.summary()