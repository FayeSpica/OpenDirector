import asyncio
from uuid import uuid4

import pytest

from opendirector.agent.direct_image import DirectImageRequest


@pytest.mark.parametrize(('text', 'chunks', 'locale'), [
    ('Draw a red cup on a blue table for me.', ('E', 'N'), 'en-US'),
    ('能画个红色咖啡杯放在浅蓝桌上吗？', (' ZH\n',), 'zh-CN'),
    ('不要现在画，先给我建议。', ('OTHER',), None),
    ('Could you edit the image already on my canvas?', ('OTHER',), None),
    ('Could you draw a red cup?', ('EN', ' but unsure'), None),
])
def test_semantic_result_is_a_closed_label(monkeypatch, text, chunks, locale):
    async def fake_stream(self, config, payload):
        assert payload['messages'][1] == {'role': 'user', 'content': text}
        for chunk in chunks:
            yield {'type': 'delta', 'text': chunk}

    monkeypatch.setattr('opendirector.agent.direct_image.ChatCompletions.stream', fake_stream)
    request = asyncio.run(DirectImageRequest.classify(text, {'modelId': 'fixture'},
                          project_id=uuid4(), turn_id=uuid4()))
    if locale is None:
        assert request is None
    else:
        assert request.locale == locale
        assert request.size == 'auto'
        assert request.prompt == text
