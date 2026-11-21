"""Agent startup overlaps independent checks before accepting a turn."""
import asyncio
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from opendirector.application.model_availability import ModelAvailability, identity
from opendirector.domain.projects import ProjectError
from opendirector.infrastructure.local_projects import LocalProjects
from opendirector.infrastructure.model_configs import ModelConfigs
from opendirector_api import agent
from opendirector_api.main import create_app


@pytest.mark.parametrize('outcome', ['accepted', 'cancel_discovery', 'both_fail'])
def test_turn_overlaps_skill_discovery_with_model_probe(tmp_path, monkeypatch, outcome):
    discovery_started = Event()
    discovery_cancelled = Event()
    model = ModelConfigs(tmp_path).save('agent', 'http://assistant.test/v1', 'fixture', '',
        connection_type='self_hosted', auth_mode='none', capability_auto_check=False)

    def prepare(self, config):
        assert discovery_started.wait(5), 'skill discovery must start before model admission finishes'
        if outcome != 'accepted':
            raise ProjectError('能力检查失败。', 409)
        return identity(config)

    async def discover(*args):
        discovery_started.set()
        if outcome == 'both_fail':
            raise ProjectError('技能检查失败。', 503)
        if outcome == 'cancel_discovery':
            try:
                await asyncio.Future()
            finally:
                discovery_cancelled.set()
        return []

    async def stream(self, run):
        assert outcome == 'accepted'
        yield {'type': 'delta', 'text': 'Fixture reply'}

    monkeypatch.setattr(ModelAvailability, 'prepare', prepare)
    monkeypatch.setattr(agent, 'skills', discover)
    monkeypatch.setattr(agent, 'runtime_available', lambda _: True)
    monkeypatch.setattr(agent.AgentRuntimes, 'stream', stream)
    headers = {'X-OpenDirector-Request': '1', 'X-OpenDirector-Session': str(uuid4())}
    project_id = str(uuid4())
    base = f'/api/creation/projects/{project_id}'
    with TestClient(create_app(LocalProjects(tmp_path)), headers=headers) as client:
        client.post(base + '/commands', json={'type': 'project.create', 'projectId': project_id,
            'commandId': str(uuid4()), 'payload': {'name': 'Agent startup'}}).raise_for_status()
        client.post(base + '/open').raise_for_status()
        response = client.post(base + '/assistant/turns', json={'id': str(uuid4()), 'text': 'Hello',
            'modelId': model['id']})
        messages = client.get(base + '/assistant').json()['messages']

    if outcome == 'accepted':
        response.raise_for_status()
        assert [message['text'] for message in messages] == ['Hello', 'Fixture reply']
    else:
        assert response.status_code == 409
        assert not messages
    assert discovery_cancelled.is_set() == (outcome == 'cancel_discovery')
