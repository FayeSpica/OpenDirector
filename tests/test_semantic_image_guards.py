from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from opendirector.domain.creation.commands import parse_command
from opendirector.domain.projects import ProjectError
from opendirector.infrastructure.creation_projects import CreationProjects
from opendirector.infrastructure.local_projects import LocalProjects
from opendirector.infrastructure.model_configs import ModelConfigs
from opendirector_api.main import create_app

from test_direct_image import Provider, TEXT


@pytest.mark.parametrize('case, expected_calls', [
    ('ambiguous', 1), ('unsupported', 1), ('malformed', 1),
    ('provider_error', 1), ('structured_attachment', 0), ('history', 1), ('stale_image_model', 0),
])
def test_fallback_keeps_project_unmodified(tmp_path, monkeypatch, case, expected_calls):
    provider = Provider()
    store = LocalProjects(tmp_path)
    projects = CreationProjects(store)
    project_id, session_id = uuid4(), str(uuid4())
    assert projects.execute(parse_command(dict(type='project.create', projectId=project_id,
        commandId=uuid4(), payload={'name': 'Image routing'}))).status == 'applied'
    projects.open(project_id, session_id)
    configs = ModelConfigs(tmp_path)
    settings = dict(connection_type='self_hosted', protocol='openai', auth_mode='none', capability_auto_check=False)
    agent = configs.save('agent', 'http://127.0.0.1:12345/v1', 'test-agent', '', **settings)
    image = configs.save('image', 'http://127.0.0.1:12345/v1', 'test-image', '', **settings)
    app = create_app(store, generation_provider=provider)
    classifications, streams = [], []

    async def classify(self, config, payload):
        classifications.append(payload['messages'][-1]['content'])
        if case == 'provider_error':
            raise ProjectError('model rejected reply', 502)
        yield {'type': 'delta', 'text': 'EN and also OTHER' if case == 'malformed' else 'OTHER'}

    async def skills(*args, **kwargs):
        return []

    async def runtime(self, run):
        streams.append(run.messages)
        yield {'type': 'delta', 'text': 'Harness reply'}

    monkeypatch.setattr('opendirector.agent.direct_image.ChatCompletions.stream', classify)
    monkeypatch.setattr('opendirector_api.agent.AgentRuntimes.stream', runtime)
    monkeypatch.setattr('opendirector_api.agent.skills', skills)
    monkeypatch.setattr('opendirector_api.agent.runtime_available', lambda runtime: True)
    base = f'/api/creation/projects/{project_id}/assistant/turns'
    headers = {'X-OpenDirector-Request': '1', 'X-OpenDirector-Session': session_id}
    data = {'modelId': agent['id'], 'modelSelections': {'image': image['id']}}
    if case == 'stale_image_model':
        data['modelSelections']['image'] = str(uuid4())
    turn = {
        'ambiguous': 'Could we use something blue?',
        'unsupported': 'Draw a tiger and a separate image of a bear.',
        'malformed': 'What do you think of this idea?',
        'provider_error': 'Picture a small fox in a snowy garden.',
        'structured_attachment': 'Draw a fox like this reference.',
        'history': TEXT,
        'stale_image_model': 'What can you do?',
    }[case]
    with TestClient(app, headers=headers) as client:
        if case == 'history':
            response = client.post(base, json=data | {'id': str(uuid4()), 'text': 'Ask me before creating anything.'})
            assert response.status_code == 200 and 'Harness reply' in response.text, response.text
        extra = {'content': [{'type': 'reference', 'id': str(uuid4())}]} if case == 'structured_attachment' else {}
        response = client.post(base, json=data | {'id': str(uuid4()), 'text': turn} | extra)
        assert response.status_code == 200 and 'Harness reply' in response.text, response.text
    assert len(classifications) == expected_calls
    assert len(streams) == (2 if case == 'history' else 1)
    if case == 'history':
        assert streams[-1][0]['content'].startswith('Ask me before creating anything.')
    snapshot = projects.snapshot(project_id)
    assert not snapshot.canvasItems and not snapshot.jobs and not provider.calls
