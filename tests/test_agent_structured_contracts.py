import pytest

from opendirector.agent.tools import compact_command_contract, structured_tools, tools


@pytest.mark.parametrize('kind', ['audio', 'image', 'text', 'video'])
def test_generation_contract_keeps_only_the_requested_media_variants(kind):
    contract = compact_command_contract('canvas.createGeneration', kind)
    definitions = contract['$defs']
    payload = definitions['CanvasCreateGeneration']['properties']
    for key in ('spec', 'item'):
        definition = definitions[payload[key]['$ref'].rsplit('/', 1)[1]]
        assert definition['properties']['kind']['const'] == kind
    assert 'projectId' not in contract['properties'] and 'commandId' not in contract['properties']
    assert 'title' in definitions[payload['item']['$ref'].rsplit('/', 1)[1]]['properties']


def test_native_catalog_uses_batched_discovery_without_changing_cli_guidance():
    standard = {t['function']['name']: t['function'] for t in tools()}
    native = {t['function']['name']: t['function'] for t in structured_tools()}
    assert 'contracts' in native['read_project']['parameters']['properties']
    assert 'before its first execution' in standard['execute_command']['description']
    assert 'complete contract from read_project' in native['execute_command']['description']


def test_native_generation_tool_advertises_all_media_parameter_contracts():
    native = {tool['function']['name']: tool['function'] for tool in structured_tools()}
    schema = native['create_generation_node']['parameters']
    definitions = schema['$defs']
    parameter_types = {choice['$ref'].rsplit('/', 1)[1] for choice in
                       schema['properties']['parameters']['anyOf'] if '$ref' in choice}
    fields = {'TextParameters': 'maxTokens', 'ImageParameters': 'size',
              'VideoParameters': 'videoMode', 'AudioParameters': 'creationType'}
    assert parameter_types == fields.keys()
    for name, field in fields.items():
        assert field in definitions[name]['properties']


def test_batched_contract_keeps_the_selected_connections_choices():
    model = {'kind': 'video', 'inputRules': {'videoModes': ['reference'], 'generateAudioOptions': [False]}}
    contract = compact_command_contract('canvas.createGeneration', 'video', model)
    definitions = contract['$defs']
    settings = definitions['VideoParameters']
    assert settings['properties']['videoMode']['enum'] == ['reference']
    assert settings['properties']['generateAudio']['enum'] == [False]
    assert {'videoMode', 'generateAudio'} <= set(settings['required'])
    assert 'parameters' in definitions['VideoGenerationSpec']['required']
