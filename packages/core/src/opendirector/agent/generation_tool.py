"""Compose existing creation commands for one new generation node."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID, uuid5

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from opendirector.domain.creation.base import Contract, GenerationKind, Point, agent_command_id
from opendirector.domain.creation.commands import CommandReceipt
from opendirector.domain.creation.generation import AudioParameters, ImageParameters, TextParameters, VideoParameters
from opendirector.domain.creation.project import ProjectSnapshot
from opendirector.domain.projects import ProjectError
from opendirector.domain.rule_facts import MAX_REFERENCES, PROMPT_MAX_LENGTH
from opendirector.infrastructure.model_configs import ModelConfigs


class CreateGenerationNode(Contract):
    """Create one visible generation node and optionally start its generation."""

    kind: GenerationKind = Field(description='Kind of content the user requested.')
    prompt: str = Field(max_length=PROMPT_MAX_LENGTH, description='Generation instruction, retaining the user’s content and constraints.')
    submit: bool = Field(strict=True, description='Set true only when the user asks to start generation now. Set false for setup only.')
    modelConfigId: UUID | None = Field(default=None, description='Existing model configuration ID when explicitly requested. Otherwise use the turn selection or the only configured model of this kind. A setup-only node can remain unbound.')
    parameters: dict[str, JsonValue] | None = Field(default=None, description='Requested generation settings using existing parameter names and JSON types. Omit for defaults.')
    position: Point | None = Field(default=None, description='Canvas position if specifically requested. Omit to place next to existing nodes.')
    references: tuple[dict[str, JsonValue], ...] = Field(
        default=(), max_length=MAX_REFERENCES,
        description='Reference bindings in order, with source and binding, plus role and provenance if requested. Preserve canvas source identity. Omit host-owned id, projectId, targetSpecId and revision fields.',
    )


def generation_tool_schema() -> dict:
    schema = CreateGenerationNode.model_json_schema()
    parameters = TypeAdapter(TextParameters | ImageParameters | VideoParameters | AudioParameters | None).json_schema()
    schema['$defs'].update(parameters.pop('$defs'))
    schema['properties']['parameters'].update(parameters)
    return schema


async def create_generation_node(
    arguments: dict[str, JsonValue],
    *,
    project_id: UUID,
    turn_id: UUID,
    call_id: str,
    model_selections: Mapping[str, str | None],
    configs: ModelConfigs,
    snapshot: Callable[[UUID], ProjectSnapshot],
    execute: Callable[[str, dict], Awaitable[dict]],
) -> dict:
    request = CreateGenerationNode.model_validate(arguments)
    if any(ref.keys() & {'id', 'projectId', 'targetSpecId', 'revision'} for ref in request.references):
        raise ValueError('Reference bindings must omit id, projectId, targetSpecId and revision.')

    operation_id = agent_command_id(turn_id, call_id)
    item_id = uuid5(operation_id, 'canvas_item')
    spec_id = uuid5(operation_id, 'generation_spec')

    config_kind = 'agent' if request.kind == 'text' else request.kind
    selected_id = str(request.modelConfigId) if request.modelConfigId is not None else model_selections.get(config_kind)
    if not selected_id:
        matching_ids = [row['id'] for row in await asyncio.to_thread(configs.list) if row['kind'] == config_kind]
        selected_id = matching_ids[0] if len(matching_ids) == 1 else None
    if selected_id or request.submit:
        config = await asyncio.to_thread(configs.resolve, selected_id or '', config_kind)
        selected_id = config['id']
    position = request.position
    if position is None:
        project = await asyncio.to_thread(snapshot, project_id)
        existing = next((item for item in project.canvasItems if item.id == item_id), None)
        if existing is not None:
            position = existing.position
        else:
            visible = [item for item in project.canvasItems if item.visible and item.parentId is None]
            position = Point(x=max(item.position.x + item.width for item in visible) + 48,
                             y=min(item.position.y for item in visible)) if visible else Point()

    project_key = str(project_id)
    spec_key = str(spec_id)
    item_key = str(item_id)
    spec = {'id': spec_key, 'projectId': project_key, 'kind': request.kind, 'purpose': 'generation',
            'prompt': request.prompt, 'modelConfigId': selected_id}
    if request.parameters is not None:
        spec['parameters'] = request.parameters
    item = {'id': item_key, 'projectId': project_key, 'kind': request.kind, 'position': position.model_dump(mode='json'),
            'binding': {'role': 'generation', 'objectRef': {'id': spec_key}}}
    references = [
        {'order': index, **ref, 'id': str(uuid5(operation_id, f'reference:{index}')),
         'projectId': project_key, 'targetSpecId': spec_key}
        for index, ref in enumerate(request.references)
    ]
    create_receipt = CommandReceipt.model_validate(await execute(f'create-generation-node:{operation_id}', {
        'type': 'canvas.createGeneration',
        'payload': {'spec': spec, 'item': item, 'references': references},
    }))
    result = {'status': create_receipt.status, 'createReceipt': create_receipt.model_dump(mode='json')}
    if create_receipt.status == 'rejected':
        return {**result, 'issues': result['createReceipt']['issues']}

    result.update({'itemId': item_key, 'specId': spec_key})
    if not request.submit:
        return result

    versions = {(value.objectType, value.id): value for value in create_receipt.changed}
    try:
        spec_version = versions[('generation_spec', spec_id)]
        item_version = versions[('canvas_item', item_id)]
        submit_receipt = CommandReceipt.model_validate(await execute(call_id, {
            'type': 'generation.submit',
            'expectedVersions': [version.model_dump(mode='json') for version in (spec_version, item_version)],
            'payload': {'specId': spec_key, 'expectedSpecRevision': spec_version.revision, 'originItemId': item_key},
        }))
    except (ProjectError, ValidationError, KeyError, OSError) as failure:
        partial = {**result, 'status': 'partial', 'error': str(failure)}
        if isinstance(failure, ProjectError):
            partial['code'] = failure.code
        return partial

    submission = submit_receipt.model_dump(mode='json')
    return {**result, 'status': submit_receipt.status, 'submitReceipt': submission,
            'issues': submission['issues'], 'jobIds': submission['jobIds']}
