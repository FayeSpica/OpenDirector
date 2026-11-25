"""Higgsfield Open generation, signed reference uploads and durable polling."""
import asyncio
import json
import re
from io import BytesIO
from urllib.parse import quote

import httpx
from PIL import Image

from opendirector.domain.higgsfield import BASE, IMAGE_BYTES, validate_model
from opendirector.domain.projects import ProjectError
from opendirector.integrations.generation_errors import checked, protocol_failure, provider_failure, transport_failure
from opendirector.integrations.http_settings import MODEL_HTTP_HOOKS, public_target, trust_environment
from opendirector.integrations.reference_inputs import image_type, reference_text, references
from opendirector.integrations.volcengine import VolcengineGeneration, bounded
from opendirector.media.probe import require_ffprobe

class HiggsfieldGeneration(VolcengineGeneration):
    def validate(self, kind, config):
        validate_model(config)
        if kind == 'video':
            require_ffprobe()

    def prepare_inputs(self, project_id, task, config, *, complete=True):
        kind = task['kind']
        self.validate(kind, config)
        sources = {str(meta['id']): (blob, meta, media_kind)
                   for blob, meta, media_kind in references(self.store, project_id, task['referenceIds'])}
        bindings = task.get('referenceRoles') or [dict(assetId=key, role='reference') for key in sources]
        images, manifest, context = {}, [], []
        content = task.get('promptContent')
        for binding in bindings:
            key = binding['assetId']
            data, meta, media_kind = sources[key]
            role = binding['role']
            if media_kind == 'text':
                context.append(reference_text(data))
                role = 'text'
            else:
                mime = image_type(data) if media_kind == 'image' else None
                try:
                    if mime not in {'image/jpeg', 'image/png'} or len(data) > IMAGE_BYTES:
                        raise ValueError
                    with Image.open(BytesIO(data)) as image:
                        if kind == 'image' and (min(image.size) < 300 or not .4 <= image.width / image.height <= 2.5):
                            raise ValueError
                        image.verify()
                except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                    raise ProjectError('参考素材须为有效的 PNG 或 JPEG 图片，单张不超过 10 MB。',
                                       422, code='higgsfield_image') from None
                if kind == 'image':
                    role = key
                elif role == 'reference':
                    role = 'first_frame'
                if role in images or (kind == 'video' and role not in {'first_frame', 'last_frame'}):
                    raise ProjectError('每个帧槽位只能使用一张图片。', 422, code='higgsfield_frames')
                images[role] = (data, mime)
            manifest.append(dict(assetId=key, fingerprint=meta['fingerprint'], kind=media_kind,
                                 role=role if kind == 'video' or media_kind == 'text' else 'reference',
                                 transmission='content'))
        image_mentions = {key: f'@image_{index}' for index, key in enumerate(images, 1)} if kind == 'image' else {}
        prompt = task['prompt'] if content is None else ''.join(
            part['text'] if part['type'] == 'text' else image_mentions.get(part['assetId'], part['name'])
            for part in content)
        prompt += ''.join('\n' + text for text in context)
        if kind == 'video' and task.get('videoMode', 'first_frame') != 'first_frame':
            raise ProjectError('请选择首尾帧模式。', 422, code='higgsfield_frames')
        if complete and 'last_frame' in images and 'first_frame' not in images:
            raise ProjectError('尾帧须搭配首帧。', 422, code='higgsfield_frames')
        if len(prompt) > 50000 or (complete and not prompt.strip() and (kind == 'image' or not images)):
            raise ProjectError('请输入提示词。', 422, code='higgsfield_prompt')
        if len(images) > (4 if kind == 'image' else 2):
            raise ProjectError('参考图片数量超出限制。', 422, code='higgsfield_image')
        return dict(profile='higgsfield-open', prompt=prompt, images=images, references=manifest)

    def _client(self, config):
        return httpx.AsyncClient(event_hooks=MODEL_HTTP_HOOKS, transport=self.transport,
                                 trust_env=trust_environment(BASE, config=config),
                                 timeout=httpx.Timeout(240, connect=15), follow_redirects=False)

    async def _json(self, client, config, method, path, stage, **kwargs):
        secrets = (config.get('apiKey', ''),)
        try:
            async with client.stream(method, BASE + path, headers={'Authorization': f'Key {secrets[0]}'},
                                     **kwargs) as response:
                await checked(response, stage=stage, secrets=secrets)
                if stage == 'cancel':
                    return
                value = json.loads(await bounded(response, stage=stage, secrets=secrets, limit=1024 * 1024))
                if not isinstance(value, dict):
                    raise protocol_failure(stage)
                return value
        except httpx.TimeoutException:
            raise transport_failure(stage, timeout=True) from None
        except httpx.HTTPError:
            raise transport_failure(stage) from None
        except (ValueError, TypeError):
            raise protocol_failure(stage) from None

    async def _upload(self, client, config, data, mime):
        stage = 'prepare'
        info = await self._json(client, config, 'POST', '/files/generate-upload-url', stage,
                                json={'content_type': mime})
        url = info.get('upload_url')
        headers = info.get('upload_headers')
        public_url = info.get('public_url')
        try:
            self._result_url(url, config)
            self._result_url(public_url, config)
        except (httpx.InvalidURL, ValueError, TypeError):
            raise protocol_failure(stage) from None
        if (not isinstance(headers, dict)
                or any(key.lower() not in {'content-type', 'x-amz-tagging'} or not isinstance(value, str)
                       for key, value in headers.items())):
            raise protocol_failure(stage)
        if self.transport is None:
            url, host_headers, extensions = await public_target(
                url, unsafe=lambda: protocol_failure(stage),
                unresolved=lambda: transport_failure(stage), schemes=('https',))
            headers = {**headers, **host_headers}
        else:
            extensions = {}
        try:
            async with httpx.AsyncClient(trust_env=False, transport=self.transport, timeout=120) as storage:
                async with storage.stream('PUT', url, headers=headers, extensions=extensions, content=data) as response:
                    await checked(response, stage=stage, secrets=(config.get('apiKey', ''),))
        except httpx.TimeoutException:
            raise transport_failure(stage, timeout=True) from None
        except httpx.HTTPError:
            raise transport_failure(stage) from None
        return public_url

    @staticmethod
    def _payload(kind, task, prepared, urls):
        params = task['parameters']
        payload = dict(prompt=prepared['prompt'])
        if kind == 'image':
            payload.update(resolution=params.get('size', '2K').lower(), aspect_ratio=params.get('aspect_ratio', 'auto'))
            if urls:
                payload['image_urls'] = list(urls.values())
            return 'edit' if urls else 'text-to-image', payload
        payload.update(duration=int(params.get('duration', 10)), resolution=params.get('resolution', '1080P').lower())
        if urls:
            payload['image_url'] = urls['first_frame']
            if 'last_frame' in urls:
                payload['end_image_url'] = urls['last_frame']
            return 'image-to-video', payload
        ratio = params.get('ratio', 'adaptive')
        payload['aspect_ratio'] = '16:9' if ratio == 'adaptive' else ratio
        return 'text-to-video', payload

    async def generate(self, config, task, submitted, *, result_ready=None):
        kind, stage = task['kind'], 'submit'
        self.validate(kind, config)
        try:
            async with self._client(config) as client:
                external_id = task.get('externalTaskId')
                if not external_id:
                    prepared = await asyncio.to_thread(self.prepare_inputs, task['projectId'], task, config)
                    stage = 'prepare'
                    urls = {role: await self._upload(client, config, data, mime)
                            for role, (data, mime) in prepared['images'].items()}
                    stage = 'submit'
                    operation, payload = self._payload(kind, task, prepared, urls)
                    value = await self._json(client, config, 'POST', f"/{config['modelId']}/{operation}", stage, json=payload)
                    external_id = self._task_id(value.get('request_id'), config, stage)
                    await submitted(external_id)
                stage = 'poll'
                path = self._request_path(config, external_id, stage) + '/status'
                while True:
                    value = await self._retry_read(lambda: self._json(client, config, 'GET', path, stage))
                    status = value.get('status')
                    if value.get('request_id', external_id) != external_id:
                        raise protocol_failure(stage)
                    if status == 'completed':
                        url = value['video']['url'] if kind == 'video' else value['images'][0]['url']
                        suffix = 'mp4' if kind == 'video' else 'png'
                        self._result_url(url, config)
                        if result_ready:
                            await result_ready(suffix, url)
                        return await self.resume_result(config, suffix, url)
                    if status in {'failed', 'nsfw', 'canceled', 'cancelled'}:
                        raise provider_failure(value, stage=stage, http_status=200,
                                               secrets=(config.get('apiKey', ''),), expected_task_id=external_id)
                    if status not in {'queued', 'in_progress'}:
                        raise protocol_failure(stage)
                    await asyncio.sleep(self.poll_interval)
        except (httpx.InvalidURL, ValueError, TypeError, KeyError, AttributeError, IndexError):
            raise protocol_failure(stage) from None

    @staticmethod
    def _task_id(value, config, stage):
        if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value)
                or config.get('apiKey') and config['apiKey'] in value):
            raise protocol_failure(stage)
        return value

    @staticmethod
    def _result_url(url, config):
        if not isinstance(url, str) or len(url) > 16384:
            raise ValueError('Invalid media URL')
        target = httpx.URL(url)
        if (target.scheme != 'https' or not target.host
                or target.userinfo or config.get('apiKey') and config['apiKey'] in url):
            raise ValueError('Invalid media URL')

    async def resume_result(self, config, suffix, url):
        try:
            self._result_url(url, config)
        except (httpx.InvalidURL, ValueError, TypeError):
            raise protocol_failure('download') from None
        return await super().resume_result(config, suffix, url, public_only=True)

    def _request_path(self, config, external_id, stage):
        return '/requests/' + quote(self._task_id(external_id, config, stage), safe='')

    async def cancel(self, config, external_id):
        self.validate(config['kind'], config)
        async with self._client(config) as client:
            await self._json(client, config, 'POST', self._request_path(config, external_id, 'cancel') + '/cancel', 'cancel')
