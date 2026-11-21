"""Compose existing creation commands for one new generation node."""

import asyncio
from collections.abc import Callable, Mapping
from typing import Awaitable, Protocol
from uuid import UUID, uuid5

from pydantic import Field, JsonValue, ValidationError

from opendirector.domain.creation.base import Contract, GenerationKind, Point, agent_command_id
from opendirector.domain.creation.commands import CommandReceipt
from opendirector.domain.creation.generation import AudioParameters, ImageParameters, TextParameters, VideoParameters
from opendirector.domain.creation.project import ProjectSnapshot
from opendirector.domain.projects import ProjectError
from opendirector.domain.rule_facts import MAX_REFERENCES, PROMPT_MAX_LENGTH
from opendirector.infrastructure.model_configs import ModelConfigs


PARAMETER_MODELS: dict[GenerationKind, type[Contract]] = {
    'text': TextParameters,
    'image': ImageParameters,
    'video': VideoParameters,
    'audio': AudioParameters,
}


class CreateGenerationNode(Contract):
    """Create one visible generation node and optionally start its generation.

    Pass settings at the top level. Supply only settings required by the request
    or selected model. Omit the rest to use defaults.
    """

    kind: GenerationKind = Field(description='Kind of content the user requested.')
    prompt: str = Field(max_length=PROMPT_MAX_LENGTH, description='Generation instruction, retaining the user’s content and constraints.')
    submit: bool = Field(strict=True, description='Set true only when the user asks to start generation now. Set false for setup only.')
    modelConfigId: UUID | None = Field(default=None, description='Existing model configuration ID when explicitly requested. Otherwise use the turn selection or the only configured model of this kind. A setup-only node can remain unbound.')
    parameters: dict[str, JsonValue] | None = Field(default=None, description='Legacy nested settings. Use top-level settings in new calls and provide each setting only once.')
    position: Point | None = Field(default=None, description='Canvas position if specifically requested. Omit to place next to existing nodes.')
    references: tuple[dict[str, JsonValue], ...] = Field(
        default=(), max_length=MAX_REFERENCES,
        description='Reference bindings in order, with source and binding, plus role and provenance if requested. Preserve canvas source identity. Omit host-owned id, projectId, targetSpecId and revision fields.',
    )


def generation_tool_schema() -> dict:
    schema = CreateGenerationNode.model_json_schema()
    settings = {}
    for kind, model in PARAMETER_MODELS.items():
        contract = model.model_json_schema()
        schema['$defs'].update(contract.get('$defs', {}))
        for name, definition in contract['properties'].items():
            settings.setdefault(name, {})[kind] = definition
    for name, by_kind in settings.items():
        variants = list(by_kind.values())
        default = variants[0]
        if all(variant == default for variant in variants):
            definition = {**default, 'description': f"{default['description']} For {', '.join(by_kind)}."}
        else:
            definition = {'anyOf': [{**variant, 'description': f"{kind}: {variant['description']}"}
                                   for kind, variant in by_kind.items()]}
        schema['properties'][name] = definition
    return schema


def parse_generation_request(arguments: dict[str, JsonValue]) -> tuple[CreateGenerationNode, dict[str, JsonValue] | None]:
    fields = CreateGenerationNode.model_fields
    request = CreateGenerationNode.model_validate({name: value for name, value in arguments.items() if name in fields})
    flat = {name: value for name, value in arguments.items() if name not in fields}
    if request.parameters is None and not flat:
        return request, None

    nested = dict(request.parameters or {})
    if nested.get('kind') == request.kind:
        # Older calls may repeat the outer discriminator inside the wrapper.
        nested.pop('kind')
    duplicates = nested.keys() & flat.keys()
    if duplicates:
        raise ValueError('Settings must appear only once: ' + ', '.join(sorted(duplicates)))
    settings = nested | flat
    PARAMETER_MODELS[request.kind].model_validate(settings, strict=True)
    return request, settings


class ExecuteCommand(Protocol):
    def __call__(self, call_id: str, command: dict, *, preflight_generation: bool = False) -> Awaitable[dict]: ...


async def create_generation_node(
    arguments: dict[str, JsonValue],
    *,
    project_id: UUID,
    turn_id: UUID,
    call_id: str,
    model_selections: Mapping[str, str | None],
    configs: ModelConfigs,
    snapshot: Callable[[UUID], ProjectSnapshot],
    execute: ExecuteCommand,
) -> dict:
    request, parameters = parse_generation_request(arguments)
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
    if parameters is not None:
        spec['parameters'] = parameters
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
    }, preflight_generation=request.submit))
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
