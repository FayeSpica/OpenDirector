"""Project-scoped assistant persistence and native runtime streaming."""
import asyncio
from contextlib import aclosing, suppress
from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid5

from anyio import CancelScope
from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opendirector.agent.catalog import model_choices
from opendirector.agent.context import RunContext, RuntimeId
from opendirector.domain.creation.base import agent_command_id
from opendirector.agent.citations import referenced_sources, with_trace_sources
from opendirector.agent.runtime import AgentRuntimes, runtime_available, configured_runtimes, runtime_spec, runtime_catalog, runtime_status
from opendirector.agent.skills import skills
from opendirector.domain.projects import ProjectError
from opendirector.application.model_availability import requests_for
from opendirector.domain.rule_facts import MAX_REFERENCES, PROMPT_MAX_LENGTH
from opendirector.infrastructure.creation_projects import durable_json
from opendirector.infrastructure.local_projects import child, read_json
from opendirector.integrations.chat_completions import CHAT_MAX_TOKENS
from opendirector_api.localization import localized_error_for_language

logger = logging.getLogger(__name__)


Runtime = RuntimeId


def _assistant_request(config):
    """Admission uses the same output ceiling as the configured model gateway."""
    return replace(requests_for(config, inputs={'text'})[0],
                   parameters=(('max_tokens', CHAT_MAX_TOKENS),))


class Draft(BaseModel):
    conversationId: UUID | None = None
    model_config = ConfigDict(extra='forbid')
    draft: str = Field(default='', max_length=PROMPT_MAX_LENGTH)
    draftContent: list[dict[str, JsonValue]] | None = None
    assets: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=MAX_REFERENCES)
    runtime: Runtime = 'default'
    runtimeModel: str | None = Field(default=None, max_length=200)
    harnessModels: dict[Literal["opencode", "codex", "claude", "jiuwenswarm", "deepseek"], Annotated[str, Field(max_length=200)] | None] = Field(default_factory=dict)
    thinking: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    builtinModelThinking: dict[str, Literal['auto', 'low', 'medium', 'high']] = Field(default_factory=dict)
    modelSelections: dict[str, str | None] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=1)


class Turn(BaseModel):
    conversationId: UUID | None = None
    model_config = ConfigDict(extra='forbid')
    id: UUID
    modelId: UUID | None = None
    text: str = Field(min_length=1, max_length=PROMPT_MAX_LENGTH)
    content: list[dict[str, JsonValue]] | None = None
    assets: list[UUID] = Field(default_factory=list, max_length=MAX_REFERENCES)
    runtime: Runtime = 'default'
    runtimeModel: str | None = Field(default=None, max_length=200)
    thinking: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    modelSelections: dict[str, str | None] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=1)


def draft_content_is_empty(content):
    """Blank text-only structured drafts are empty; reference or skill parts are not.

    Non-string text values are treated as present content so New does not raise,
    and an accepted non-text or non-string part is not silently reused away.
    """
    if not content:
        return True
    for part in content:
        if not isinstance(part, dict):
            return False
        kind = part.get('type')
        if kind == 'text':
            text_value = part.get('text')
            if not isinstance(text_value, str):
                return False
            if text_value.strip():
                return False
        else:
            return False
    return True


def conversation_is_empty(value):
    """A session with no messages or unsent content can be reused by New."""
    return (not value.get('messages')
            and not (value.get('draft') or '').strip()
            and not value.get('assets')
            and not value.get('skills')
            and draft_content_is_empty(value.get('draftContent')))


def public_conversation(value):
    return {key: item for key, item in value.items()
            if key not in {'archivedConversations', 'reusedCreateIds'}}


def reused_ids(value):
    return [str(item) for item in (value.get('reusedCreateIds') or [])]


class Conversations:
    def __init__(self, projects):
        self.projects = projects

    def path(self, project_id: UUID, session: str | None = None) -> Path:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            if session:
                self.projects._lease(db, project_id, directory, session)
            return directory

    def update(self, project_id: UUID, session: str | None = None, *, draft: Draft | None = None,
               begin: dict | None = None, finish: dict | None = None, conversation_id: UUID | None = None,
               before_begin=None) -> dict:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            if session:
                self.projects._lease(db, project_id, directory, session)
            folder = child(directory, 'agent-runs')
            folder.mkdir(exist_ok=True)
            file = child(folder, 'conversation.json')
            value = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump()}
            value['conversationId'] = value.get('conversationId') or str(uuid5(project_id, 'assistant'))
            supplied = draft.conversationId if draft is not None else conversation_id
            if (draft is not None or begin is not None or finish is not None) and str(supplied) != value['conversationId'] and (supplied is not None or value.get('archivedConversations')):
                raise ProjectError('当前对话已切换，请重新加载后重试；原对话未覆盖。', 409)
            changed = False
            if draft is not None:
                value.update(draft.model_dump(mode="json", exclude={"conversationId"}))
                changed = True
            if begin is not None:
                if any(message['id'] == begin['id'] for message in value['messages']):
                    raise ProjectError('这条消息已接受，请读取对话，不要重复发送。', 409)
                if before_begin is not None:
                    before_begin()
                value['messages'].append(begin)
                value['messages'].append({'id': str(uuid5(UUID(begin['id']), 'assistant')),
                    'role': 'assistant', 'text': '', 'status': 'streaming',
                    **{key: begin[key] for key in ('model', 'modelIsCliDefault', 'runtime') if key in begin}})
                value.update(draft='', draftContent=None, assets=[], skills=[])
                changed = True
            if finish is not None:
                value['messages'] = [finish if message['id'] == finish['id'] else message for message in value['messages']]
                changed = True
            if changed:
                durable_json(file, value)
            result = public_conversation(value)
        return with_trace_sources(result, directory)

    def list(self, project_id: UUID) -> dict:
        directory = self.path(project_id)
        file = child(child(directory, 'agent-runs'), 'conversation.json')
        with self.projects.local.transaction():
            current = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump()}
            current['conversationId'] = current.get('conversationId') or str(uuid5(project_id, 'assistant'))
            return {'activeId': current['conversationId'], 'items': [
                {'id': item['conversationId'],
                 'title': next((message['text'].strip().split('\n')[0][:48] or None
                                for message in item['messages'] if message['role'] == 'user'),
                               (item.get('draft') or '').strip().split('\n')[0][:48] or None),
                 'messageCount': len(item['messages']),
                 'hasDraft': bool((item.get('draft') or '').strip() or item.get('assets') or item.get('skills'))}
                for item in [current, *current.get('archivedConversations', [])]]}

    def activate(self, project_id: UUID, conversation_id: UUID, session: str, *, create=False) -> dict:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            self.projects._lease(db, project_id, directory, session)
            folder = child(directory, 'agent-runs')
            folder.mkdir(exist_ok=True)
            file = child(folder, 'conversation.json')
            current = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump(mode='json')}
            current['conversationId'] = current.get('conversationId') or str(uuid5(project_id, 'assistant'))
            archives = current.pop('archivedConversations', [])
            requested = str(conversation_id)
            if current['conversationId'] == requested or requested in reused_ids(current):
                return with_trace_sources(public_conversation(current), directory)
            target = next((item for item in archives
                           if item['conversationId'] == requested or requested in reused_ids(item)), None)
            if target is None:
                if not create:
                    raise ProjectError('对话不存在。', 404)
                if conversation_is_empty(current):
                    aliases = reused_ids(current)
                    if requested not in aliases:
                        aliases.append(requested)
                    current['reusedCreateIds'] = aliases
                    durable_json(file, {**current, 'archivedConversations': archives})
                    return with_trace_sources(public_conversation(current), directory)
                if len(archives) >= 99:
                    raise ProjectError('项目对话已达 100 个，暂不能继续新建。', 409)
                target = {'messages': [], **Draft().model_dump(mode='json'),
                          **{key: current[key] for key in ('runtime', 'runtimeModel', 'harnessModels', 'thinking', 'builtinModelThinking', 'modelSelections') if key in current},
                          'conversationId': requested}
            archives = [item for item in archives if item['conversationId'] != target['conversationId']]
            durable_json(file, {**target, 'archivedConversations': [current, *archives]})
        return with_trace_sources(public_conversation(target), directory)


class ConversationCreate(BaseModel):
    id: UUID


class RunEvents:
    """Events of one accepted turn, kept until the turn ends.

    The run belongs to the project, not to the HTTP request that started it:
    leaving the project closes the stream, and returning attaches a new one that
    replays from the beginning. Only an explicit stop cancels the run.
    """

    def __init__(self):
        self.items: list[dict] = []
        self.changed = asyncio.Event()

    def put(self, event: dict) -> None:
        self.items.append(event)
        self.changed.set()
        self.changed = asyncio.Event()

    async def follow(self):
        index = 0
        while True:
            while index < len(self.items):
                event = self.items[index]
                index += 1
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                if event['type'] == 'end':
                    return
            await self.changed.wait()


def agent_routes(projects, generation, configs, provider=None, *, active=None) -> APIRouter:
    router = APIRouter(prefix='/api/creation/projects/{project_id}/assistant')
    conversations = Conversations(projects)
    runtimes = AgentRuntimes(projects, generation, configs, provider)

    @router.get('/traces/{turn_id}/input')
    def trace_input(project_id: UUID, turn_id: UUID):
        file = child(child(conversations.path(project_id), 'agent-runs'), 'conversation.json')
        with projects.local.transaction():
            conversation = read_json(file) if file.is_file() else {}
        for item in [conversation, *conversation.get('archivedConversations', [])]:
            for message in item.get('messages', []):
                if message['role'] == 'user' and message['id'] == str(turn_id):
                    return {'text': message['text']}
        raise ProjectError('该轮用户消息不存在。', 404)

    @router.get('/traces/{turn_id}/{source}')
    def trace(project_id: UUID, turn_id: UUID,
              source: Literal['opencode', 'codex', 'claude', 'jiuwenswarm', 'deepseek', 'model']):
        folder = child(conversations.path(project_id), 'agent-runs')
        file = child(folder, f'{turn_id}.{source}.jsonl')
        if not file.is_file():
            raise ProjectError('轨迹文件不存在。', 404)
        return FileResponse(file, media_type='text/plain', headers={'Cache-Control': 'no-store'})

    @router.get('/turns/{turn_id}/jobs')
    def turn_jobs(project_id: UUID, turn_id: UUID):
        # Read-only like the trace routes: no session lease, and the Agent trace is not consulted.
        # It reads the committed snapshot and never starts, resumes or replays a command.
        jobs = sorted((job for job in projects.snapshot(project_id).jobs if job.agentRunId == turn_id),
                      key=lambda job: (job.createdAt, str(job.id)))
        return [dict(jobId=job.id, callId=job.agentCallId, commandId=agent_command_id(turn_id, job.agentCallId),
                     status=job.status, phase=job.phase, recovery=job.recovery, resumeAllowed=job.resumeAllowed,
                     cancelRequested=job.cancelRequested, remoteCancellation=job.remoteCancellation,
                     errorDetails=job.errorDetails, asset=job.asset, adopted=job.adopted)
                for job in jobs]

    if active is None:
        active = {}
