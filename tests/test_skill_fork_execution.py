from __future__ import annotations

import asyncio
import copy

import pytest

from YL1203Code.agent import Agent
from YL1203Code.conversation import ConversationManager
from YL1203Code.permissions.checker import PermissionChecker
from YL1203Code.permissions.dangerous import DangerousCommandDetector
from YL1203Code.permissions.modes import PermissionMode
from YL1203Code.permissions.rules import RuleEngine
from YL1203Code.permissions.sandbox import PathSandbox
from YL1203Code.skills.executor import SkillExecutor
from YL1203Code.skills.parser import SkillDef
from YL1203Code.tools import create_default_registry
from YL1203Code.tools.base import StreamEnd, TextDelta, ToolCallComplete
from YL1203Code.tools.impl.tool_search import ToolSearchTool
from YL1203Code.tools.load_skill import LoadSkill


class ScriptClient:
    def __init__(self, calls=()):
        self.calls = list(calls)
        self.seen = []

    async def stream(self, conversation, **kwargs):
        self.seen.append(copy.deepcopy(conversation.history))
        if len(self.seen) == 1:
            for call in self.calls:
                yield call
        else:
            yield TextDelta('done')
        yield StreamEnd('end_turn')


def make_agent(tmp_path, client, mode=PermissionMode.ACCEPT_EDITS, rules=None):
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(tmp_path)),
        RuleEngine(project_rules_path=rules), mode,
    )
    return Agent(client, create_default_registry(), 'anthropic',
                 work_dir=str(tmp_path), permission_checker=checker, max_iterations=3,
                 instructions_content='Keep the public API stable.')


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['recent', 'full', 'none'])
async def test_fork_receives_current_conversation(tmp_path, context):
    client = ScriptClient()
    agent = make_agent(tmp_path, client)
    conv = ConversationManager()
    conv.add_user_message('The failing test is test_login.')
    conv.add_assistant_message('I will investigate the login handler.')
    agent._current_conversation = conv
    skill = SkillDef('investigate', 'debug', 'Investigate $ARGUMENTS', 'fork', context=context)
    await SkillExecutor(agent, client, 'anthropic').execute_fork(skill, 'login')
    text = '\n'.join(m.content for m in client.seen[0])
    assert ('test_login' in text) == (context != 'none')
    assert 'Investigate login' in text
    assert 'Keep the public API stable.' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('effect', ['deny', 'ask'])
async def test_fork_obeys_parent_permissions_without_hanging(tmp_path, effect):
    rules = tmp_path / 'permissions.yaml'
    rules.write_text(f'- rule: WriteFile(*)\n  effect: {effect}\n')
    client = ScriptClient([ToolCallComplete('w', 'WriteFile',
                          {'file_path': 'forbidden.txt', 'content': 'changed'})])
    agent = make_agent(tmp_path, client, rules=rules)
    skill = SkillDef('try-write', 'write', 'Try writing', 'fork')
    await asyncio.wait_for(SkillExecutor(agent, client, 'anthropic').execute_fork(skill, ''), 3)
    assert not (tmp_path / 'forbidden.txt').exists()
    results = [r for m in client.seen[-1] for r in m.tool_results]
    assert results and results[0].is_error
    assert agent.permission_checker.mode == PermissionMode.ACCEPT_EDITS


@pytest.mark.asyncio
async def test_fork_tool_discovery_does_not_change_parent(tmp_path):
    client = ScriptClient([ToolCallComplete('s', 'ToolSearch', {'query': 'select:ReadFile'})])
    agent = make_agent(tmp_path, client)
    agent.registry.get('ReadFile').should_defer = True
    agent.registry.register(ToolSearchTool(agent.registry))
    await SkillExecutor(agent, client, 'anthropic').execute_fork(
        SkillDef('discover', 'discover', 'Find ReadFile', 'fork'), '')
    results = [r for m in client.seen[-1] for r in m.tool_results]
    assert 'Found 1 tool' in results[0].content
    assert not agent.registry.is_discovered('ReadFile')


@pytest.mark.asyncio
async def test_fork_skill_activation_does_not_change_parent(tmp_path):
    class Loader:
        def get(self, name):
            return SkillDef(name, 'nested', 'Nested skill instructions')

    client = ScriptClient([ToolCallComplete('s', 'LoadSkill', {'name': 'nested'})])
    agent = make_agent(tmp_path, client)
    tool = LoadSkill()
    tool.set_loader(Loader())
    tool.set_agent(agent)
    agent.registry.register(tool)
    await SkillExecutor(agent, client, 'anthropic').execute_fork(
        SkillDef('load', 'load', 'Load nested', 'fork'), '')
    assert not agent.active_skills
    assert 'Nested skill instructions' in '\n'.join(r.content for m in client.seen[-1] for r in m.tool_results)