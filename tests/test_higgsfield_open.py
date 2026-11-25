"""Higgsfield Open wire contract and durable generation checkpoints."""
import asyncio
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from opendirector.domain.higgsfield import BASE, IMAGE_MODEL, VIDEO_MODEL
from opendirector.domain.projects import ProjectError
from opendirector.infrastructure.model_configs import ModelConfigs
from opendirector.integrations.providers import GenerationProviders


def config(kind):
    return dict(kind=kind, modelId=IMAGE_MODEL if kind == 'image' else VIDEO_MODEL,
                url=BASE, apiKey='synthetic-id:synthetic-secret', protocol='higgsfield_open',
                authMode='bearer', connectionType='api')


@pytest.mark.cpu
@pytest.mark.parametrize('kind', ['image', 'video'])
def test_model_configuration(kind, tmp_path):
    configs = ModelConfigs(tmp_path)
    model = config(kind)
    row = configs.save(kind, BASE, model['modelId'], model['apiKey'], protocol=model['protocol'])
    assert 'apiKey' not in row
    assert row['inputRules']['profile'] == 'higgsfield-open'
    assert row['inputRules']['maxImages'] == (4 if kind == 'image' else 2)
    if kind == 'video':
        assert row['inputRules']['videoModes'] == ['first_frame']
        assert row['inputRules']['videoCapabilities']['defaultDuration'] == 10
    with pytest.raises(ProjectError, match='Higgsfield Open'):
        configs.save(kind, 'https://other.test', model['modelId'], model['apiKey'], protocol=model['protocol'])


@pytest.mark.cpu
@pytest.mark.parametrize(('kind', 'with_reference', 'operation'), [
    ('image', False, 'text-to-image'), ('image', True, 'edit'),
    ('video', False, 'text-to-video'), ('video', True, 'image-to-video'),
])
def test_generation_lifecycle(kind, with_reference, operation, public_cdn, monkeypatch):
    model = config(kind)
    content = BytesIO()
    Image.new('RGB', (640, 360)).save(content, 'PNG')
    image = content.getvalue()
    monkeypatch.setattr('opendirector.integrations.higgsfield.require_ffprobe', lambda: None)
    result_url = f'https://cdn.test/result.{"png" if kind == "image" else "mp4"}'
    reference_url = 'https://cdn.test/reference.png'
    put_url = 'https://cdn.test/put?signature=synthetic'
    status_path = '/requests/provider-job/status'
    api_path = f"/{model['modelId']}/{operation}"
    accepted, ready, calls = [], [], []

    class Assets:
        def asset(self, project_id, asset_id):
            return image, dict(id=asset_id, type='image/png', name='frame.png', fingerprint='test')

    def respond(request):
        path = request.url.path
        calls.append((request.method, path))
        assert request.headers.get('Authorization') == (
            'Key ' + model['apiKey'] if request.url.host == 'api.higgsfield.ai' else None)
        if path == '/files/generate-upload-url':
            assert json.loads(request.content) == {'content_type': 'image/png'}
            return httpx.Response(200, json={'upload_url': put_url, 'public_url': reference_url,
                                            'upload_headers': {'Content-Type': 'image/png', 'x-amz-tagging': 'retention=temporary'}})
        if path == '/put':
            assert request.method == 'PUT' and request.content == image
            assert request.headers['x-amz-tagging'] == 'retention=temporary'
            return httpx.Response(200)
        if path == api_path:
            payload = json.loads(request.content)
            if kind == 'image':
                expected = {'prompt': 'A blue sphere', 'resolution': '2k', 'aspect_ratio': '16:9'}
                if with_reference:
                    expected['image_urls'] = [reference_url]
            else:
                expected = {'prompt': 'A blue sphere', 'resolution': '720p', 'duration': 5}
                expected.update({'image_url': reference_url} if with_reference else {'aspect_ratio': '16:9'})
            assert payload == expected
            return httpx.Response(200, json={'request_id': 'provider-job', 'status': 'queued'})
        if path == status_path:
            assert accepted == ['provider-job']
            if calls.count(('GET', status_path)) == 1:
                return httpx.Response(200, json={'status': 'in_progress'})
            outputs = {'images': [{'url': result_url}]} if kind == 'image' else {'video': {'url': result_url}}
            return httpx.Response(200, json={'status': 'completed', **outputs})
        if path == '/requests/provider-job/cancel':
            assert request.method == 'POST'
            return httpx.Response(204)
        assert path == httpx.URL(result_url).path
        assert ready[-1][1] == result_url
        return httpx.Response(200, content=b'fixture result')

    async def run():
        provider = GenerationProviders(Assets(), httpx.MockTransport(respond), poll_interval=0)
        task = dict(kind=kind, projectId='project', prompt='A blue sphere',
                    referenceIds=['asset'] if with_reference else [],
                    parameters={'aspect_ratio': '16:9', 'duration': 5, 'resolution': '720P'})
        suffix = 'png' if kind == 'image' else 'mp4'

        async def submitted(request_id):
            accepted.append(request_id)

        async def result_ready(extension, url):
            ready.append((extension, url))

        assert await provider.generate(model, task, submitted, result_ready=result_ready) == (suffix, b'fixture result')
        await provider.cancel(model, 'provider-job')
        assert await provider.generate(model, {**task, 'externalTaskId': 'provider-job'},
                                       submitted, result_ready=result_ready) == (suffix, b'fixture result')

    asyncio.run(run())
    assert calls.count(('POST', api_path)) == 1
    assert calls.count(('POST', '/files/generate-upload-url')) == int(with_reference)
    assert calls.count(('GET', status_path)) == 3
