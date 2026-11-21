import asyncio
import json
import time
from io import BytesIO
from threading import Event
from uuid import UUID, uuid4, uuid5

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from opendirector.agent.direct_image import DirectImageRequest
from opendirector.domain.creation.commands import parse_command
from opendirector.infrastructure.creation_projects import CreationProjects
from opendirector.infrastructure.local_projects import LocalProjects
from opendirector.infrastructure.model_configs import ModelConfigs
from opendirector_api.main import create_app


PROMPT = 'Two blue paper boats.'
TEXT = ('Generate one image on the canvas. Set the generation size to 1024x1024 and output format to PNG. '
        f'Use this exact image prompt: "{PROMPT}" Keep the image visible on the canvas.')


class Provider:
    def __init__(self):
        self.calls = []
        self.finish = Event()

    def validate(self, kind, config):
        pass

    async def generate(self, config, task, submitted):
        self.calls.append(task)
        while not self.finish.is_set():
            await asyncio.sleep(.01)
        image = BytesIO()
        Image.new('RGB', (1024, 1024), '#0070d0').save(image, 'PNG')
        return 'png', image.getvalue()


@pytest.mark.parametrize('structured', [False, True])
def test_direct_turn_creates_real_node_and_job_before_result(tmp_path, monkeypatch, structured):
    provider = Provider()
    store = LocalProjects(tmp_path)
    projects = CreationProjects(store)
    project_id, turn_id, session_id = uuid4(), uuid4(), str(uuid4())
    assert projects.execute(parse_command(dict(type='project.create', projectId=project_id,
        commandId=uuid4(), payload={'name': 'Image direct path'}))).status == 'applied'
    projects.open(project_id, session_id)
    configs = ModelConfigs(tmp_path)
    options = dict(connection_type='self_hosted', protocol='openai', auth_mode='none', capability_auto_check=False)
    agent = configs.save('agent', 'http://127.0.0.1:12345/v1', 'test-agent', '', **options)
    image = configs.save('image', 'http://127.0.0.1:12345/v1', 'test-image', '', **options)
    app = create_app(store, generation_provider=provider)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Explicit image intent must not start skill scan or model runtime')
    monkeypatch.setattr('opendirector_api.agent.skills', unexpected)
    monkeypatch.setattr('opendirector_api.agent.AgentRuntimes.stream', unexpected)
    base = f'/api/creation/projects/{project_id}'
    headers = {'X-OpenDirector-Request': '1', 'X-OpenDirector-Session': session_id}
    with TestClient(app, headers=headers) as client:
        response = client.post(base + '/assistant/turns', json={
            'id': str(turn_id), 'modelId': agent['id'], 'text': TEXT,
            'content': [{'type': 'text', 'text': TEXT}] if structured else None,
            'modelSelections': {'agent': agent['id'], 'image': image['id']},
        })
        assert response.status_code == 200, response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert [e['type'] for e in events] == ['start', 'tool', 'tool', 'delta', 'end']
        assert events[-1]['error'] is None
        snapshot = projects.snapshot(project_id)
        [node], [job] = snapshot.canvasItems, snapshot.jobs
        assert node.visible and node.kind == 'image'
        assert node.id == uuid5(turn_id, 'image-node') == job.originItemId
        assert job.inputSnapshot.spec.prompt == PROMPT and not job.inputSnapshot.references
        assert job.agentRunId == turn_id and job.status in {'queued', 'running'}
        assert len(snapshot.results) == 0
        receipt = client.get(base + f'/assistant/turns/{turn_id}/jobs').json()
        assert len(receipt) == 1 and receipt[0]['jobId'] == str(job.id)
        provider.finish.set()
        for _ in range(100):
            finished = projects.snapshot(project_id).jobs[0]
            if finished.status in {'succeeded', 'failed'}:
                assert finished.status == 'succeeded' and finished.adopted
                break
            time.sleep(.02)
        else:
            pytest.fail('Image task did not finish')
        assert len(provider.calls) == 1


@pytest.mark.parametrize('text', [TEXT + ' Also generate a video.', TEXT.replace('PNG', 'GIF'),
                                 TEXT.replace('"', ''), TEXT.replace('Generate one', 'Do not generate one')])
def test_unsupported_requests_are_not_direct(text):
    assert DirectImageRequest.parse(text, str(uuid4())) is None
