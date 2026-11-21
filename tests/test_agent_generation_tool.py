"""Exercise the composed tool against the shared canvas and job commands."""

from uuid import UUID, uuid4

import pytest

from opendirector.agent.context import RunContext
from opendirector.agent.gateway import ToolGateway
from opendirector.domain.creation.base import agent_command_id
from opendirector.domain.projects import ProjectError
from test_agent_job_lineage import Provider, settled, stack
from test_creation_canvas_service import project, text_material  # noqa: F401 (project is a fixture)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def generation_gateway(project, tools, model_selections=None):
    store, pid, session = project
    return ToolGateway(tools, RunContext(
        runtime='opencode', project_id=pid, turn_id=uuid4(), session=session,
        directory=store.local.repository, config=None, messages=[], selected_skills=[],
        model_selections=model_selections or {},
    ))


@pytest.mark.anyio
async def test_configures_video_without_starting_a_job(project):
    store, pid, _ = project
    provider = Provider()
    runner, tools, _ = stack(project, provider)
    config = runner.configs.save('video', 'http://127.0.0.1:12345/v1', 'MiniMax-H3', '',
        connection_type='self_hosted', protocol='vllm_omni', auth_mode='none')
    other = runner.configs.save('video', 'http://127.0.0.1:12345/v1', 'MiniMax-H3-other', '',
        connection_type='self_hosted', protocol='vllm_omni', auth_mode='none')
    gateway = generation_gateway(project, tools, {'video': config['id']})
    try:
        result = await gateway.call('setup-video', 'create_generation_node', {
            'kind': 'video', 'prompt': 'A boat passing under the bridge', 'submit': False,
            'parameters': {'durationSeconds': 5, 'generateAudio': False},
            'position': {'x': 280, 'y': -80},
        })
        assert result.error is None, result.value
        assert result.value['status'] == 'applied'
        current = store.snapshot(pid)
        [item], [spec] = current.canvasItems, current.generationSpecs
        assert item.visible and item.kind == 'video' and item.binding.objectRef.id == spec.id
        assert (item.position.x, item.position.y) == (280, -80)
        assert str(item.id) == result.value['itemId']
        assert spec.modelConfigId == UUID(config['id']) != UUID(other['id'])
        assert spec.prompt == 'A boat passing under the bridge'
        assert spec.parameters.durationSeconds == 5 and not spec.parameters.generateAudio
        assert not current.jobs and not provider.calls
    finally:
        await gateway.close()
        await runner.close()


@pytest.mark.anyio
@pytest.mark.parametrize('model_count', [0, 2])
async def test_setup_without_unambiguous_model_keeps_node_editable(project, model_count):
    store, pid, _ = project
    provider = Provider()
    runner, tools, _ = stack(project, provider)
    for index in range(model_count):
        runner.configs.save('video', 'http://127.0.0.1:12345/v1', f'MiniMax-H3-{index}', '',
            connection_type='self_hosted', protocol='vllm_omni', auth_mode='none')
    gateway = generation_gateway(project, tools)
    request = {'kind': 'video', 'prompt': 'A boat passing under the bridge', 'submit': False}
    try:
        submitted = await gateway.call('start-without-selection', 'create_generation_node', {**request, 'submit': True})
        assert submitted.error
        invalid = await gateway.call('explicit-invalid-model', 'create_generation_node', {**request, 'modelConfigId': str(uuid4())})
        assert invalid.error
        assert not store.snapshot(pid).canvasItems

        result = await gateway.call('setup-without-selection', 'create_generation_node', request)
        assert result.error is None, result.value
        current = store.snapshot(pid)
        [item], [spec] = current.canvasItems, current.generationSpecs
        assert item.visible and item.binding.objectRef.id == spec.id
        assert spec.modelConfigId is None and spec.prompt == request['prompt']
        assert not current.jobs and not provider.calls
    finally:
        await gateway.close()
        await runner.close()


@pytest.mark.anyio
async def test_places_a_referenced_text_node_and_submits_once_with_lineage(project):
    store, pid, _ = project
    source, asset = text_material(project, text='Source notes')
    provider = Provider()
    runner, tools, config = stack(project, provider)
    gateway = generation_gateway(project, tools)
    request = {
        'kind': 'text', 'prompt': 'Write a two-sentence synopsis from these notes.',
        'submit': True, 'references': [{'binding': 'follow_current', 'source': {'id': str(source.id)}}],
    }
    try:
        result = await gateway.call('write-synopsis', 'create_generation_node', request)
        assert result.error is None, result.value
        replay = await gateway.call('write-synopsis', 'create_generation_node', request)
        assert result.value == replay.value
        conflict = await gateway.call('write-synopsis', 'create_generation_node', {**request, 'submit': False})
        assert conflict.error
        await settled(runner)
        current = store.snapshot(pid)
        created = next(item for item in current.canvasItems if str(item.id) == result.value['itemId'])
        assert created.visible and created.position.x >= source.position.x + source.width
        spec = next(spec for spec in current.generationSpecs if str(spec.id) == result.value['specId'])
        assert spec.modelConfigId == UUID(config['id'])
        [reference], [job] = current.references, current.jobs
        assert reference.source.id == source.id and reference.targetSpecId == spec.id
        assert job.inputSnapshot.references[0].asset.assetId == asset.id
        assert result.value['status'] == 'accepted'
        assert result.value['createReceipt']['status'] == 'applied'
        assert result.value['submitReceipt']['status'] == 'accepted'
        assert (job.agentRunId, job.agentCallId) == (gateway.run.turn_id, 'write-synopsis')
        assert result.value['submitReceipt']['commandId'] == str(agent_command_id(gateway.run.turn_id, job.agentCallId))
        assert result.value['jobIds'] == [str(job.id)] and provider.calls == [str(job.id)]
    finally:
        await gateway.close()
        await runner.close()


@pytest.mark.anyio
@pytest.mark.parametrize('submit_raises', [False, True])
async def test_reports_submission_failure_after_committing_the_new_node(project, monkeypatch, submit_raises):
    store, pid, _ = project
    provider = Provider()
    runner, tools, _ = stack(project, provider)
    gateway = generation_gateway(project, tools)

    async def failed_submit(*args, **kwargs):
        raise ProjectError('Provider unavailable', 409)

    if submit_raises:
        monkeypatch.setattr(runner, 'execute', failed_submit)
    try:
        result = await gateway.call('partial', 'create_generation_node', {
            'kind': 'text', 'prompt': 'Write a synopsis' if submit_raises else '', 'submit': True,
        })
        assert result.value['itemId'] == str(store.snapshot(pid).canvasItems[0].id)
        assert result.value['createReceipt']['status'] == 'applied'
        if submit_raises:
            assert result.error == 'Provider unavailable'
            assert result.value['status'] == 'partial'
        else:
            assert result.value['status'] == 'rejected'
            submission = result.value['submitReceipt']
            assert submission['status'] == 'rejected' and submission['issues']
            assert result.error == submission['issues'][0]['message']
        assert not store.snapshot(pid).jobs and not provider.calls
    finally:
        await gateway.close()
        await runner.close()
