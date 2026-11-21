"""Preflight uses the same persisted canvas and generation inputs as submission."""

import asyncio
from uuid import uuid4

import pytest

from opendirector.domain.creation.commands import parse_command
from opendirector.domain.projects import ProjectError
from test_creation_canvas_service import project, text_material, ver  # noqa: F401 (project is a fixture)
from test_creation_generation_service import Provider, runtime


def create_command(project_id, model_id, *, kind='text', prompt, parameters=None, source=None):
    item_id, spec_id = uuid4(), uuid4()
    spec = dict(id=spec_id, projectId=project_id, kind=kind, purpose='generation', prompt=prompt, modelConfigId=model_id)
    if parameters is not None:
        spec['parameters'] = parameters
    item = dict(id=item_id, projectId=project_id, kind=kind,
                binding=dict(role='generation', objectRef=dict(id=spec_id)))
    references = [] if source is None else [dict(id=uuid4(), projectId=project_id, targetSpecId=spec_id,
        binding='follow_current', source=dict(id=source.id))]
    return parse_command(dict(commandId=uuid4(), projectId=project_id, type='canvas.createGeneration',
        payload=dict(spec=spec, item=item, references=references)))


@pytest.mark.parametrize('kind, code', [('image', 'image_quality_unsupported'), ('text', 'invalid_input')])
def test_invalid_generation_keeps_project_state_and_replays_rejection(project, kind, code):
    store, project_id, session = project
    provider = Provider()
    runner, config = runtime(project, provider)
    if kind == 'image':
        config = runner.configs.save('image', 'http://127.0.0.1:12345/v1', 'compatible-image', '',
            connection_type='self_hosted', protocol='openai', auth_mode='none')
        command = create_command(project_id, config['id'], kind='image', prompt='A red cup', parameters={'quality': 'high'})
    else:
        command = create_command(project_id, config['id'], prompt='')
    before = store.snapshot(project_id)

    receipt = runner.create_generation_node(command, session=session, source='agent')

    assert receipt.status == 'rejected'
    assert [issue.code for issue in receipt.issues] == [code]
    assert store.snapshot(project_id) == before
    assert not receipt.changed and not receipt.jobIds and not provider.calls
    assert runner.create_generation_node(command, session=session, source='agent') == receipt


def test_valid_referenced_node_preserves_lease_and_submits_exactly_once(project):
    store, project_id, session = project
    provider = Provider()
    runner, config = runtime(project, provider)
    source, asset = text_material(project, text='Source notes')
    create = create_command(project_id, config['id'], prompt='Use the notes', source=source)
    before = store.snapshot(project_id)
    with pytest.raises(ProjectError):
        runner.create_generation_node(create, session=str(uuid4()), source='agent')
    assert store.snapshot(project_id) == before

    receipt = runner.create_generation_node(create, session=session, source='agent')
    assert receipt.status == 'applied' and not receipt.jobIds and not provider.calls
    assert runner.create_generation_node(create, session=session, source='agent') == receipt
    state = store.snapshot(project_id)
    item = next(item for item in state.canvasItems if item.id == create.payload.item.id)
    spec = next(spec for spec in state.generationSpecs if spec.id == create.payload.spec.id)
    assert item.visible and item.binding.objectRef.id == spec.id
    assert item.revision == spec.revision == 1
    assert len(state.canvasItems) == len(before.canvasItems) + 1 and not state.jobs
    submit = parse_command(dict(commandId=uuid4(), projectId=project_id, type='generation.submit',
        expectedVersions=[ver('canvas_item', item), ver('generation_spec', spec)],
        payload=dict(specId=spec.id, expectedSpecRevision=spec.revision, originItemId=item.id)))

    async def run():
        provider.finish.set()
        try:
            accepted = await runner.execute(submit, session=session, source='agent')
            assert accepted.status == 'accepted'
            assert await runner.execute(submit, session=session, source='agent') == accepted
            await asyncio.gather(*(job.future for job in list(runner.jobs.values())))
        finally:
            await runner.close()

    asyncio.run(run())
    [job] = store.snapshot(project_id).jobs
    assert job.status == 'succeeded' and job.originItemId == item.id
    assert job.inputSnapshot.references[0].asset.assetId == asset.id
    assert len(provider.calls) == 1
