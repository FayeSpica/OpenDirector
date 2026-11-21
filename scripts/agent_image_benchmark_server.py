"""Run the production API for the image benchmark in a fresh local workspace.

Run with python -m scripts.agent_image_benchmark_server.
Set OPENDIRECTOR_ROOT to an empty directory and put the source on PYTHONPATH.
Reads two named model connections from an existing OpenDirector workspace.
"""

import argparse
import os
from pathlib import Path
from uuid import uuid4

import uvicorn

from opendirector.infrastructure.local_projects import LocalProjects
from opendirector.infrastructure.model_configs import ModelConfigs
from opendirector.installation import data_root
from opendirector_api.main import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-root', required=True, type=Path)
    parser.add_argument('--agent-model', required=True)
    parser.add_argument('--image-model', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    if not os.environ.get('OPENDIRECTOR_ROOT'):
        parser.error('Set OPENDIRECTOR_ROOT to an isolated, empty directory.')
    root = data_root()
    source = args.model_root.resolve()
    if root.is_relative_to(source) or (root.exists() and any(root.iterdir())):
        parser.error('Use an empty directory outside the source workspace.')
    if not (source / '.opendirector/models.json').is_file():
        parser.error('The source workspace has no model configuration.')

    configs = ModelConfigs(source).catalog_snapshot()
    selected = []
    for kind, model_id in [('agent', args.agent_model), ('image', args.image_model)]:
        matches = [private for public, private in configs
                   if public['kind'] == kind and public['modelId'] == model_id]
        if len(matches) != 1:
            parser.error(f'Expected one {kind} connection for {model_id}, found {len(matches)}.')
        selected.append(matches[0])

    destination = ModelConfigs(root)
    for model in selected:
        destination.save(model['kind'], model['url'], model['modelId'], model['apiKey'],
                         connection_type=model['connectionType'], protocol=model['protocol'],
                         auth_mode=model['authMode'], proxy_mode=model['proxyMode'],
                         input_modalities=model['inputModalities'],
                         capability_auto_check=model.get('capabilityAutoCheck'))

    app = create_app(LocalProjects(root))
    instance = str(uuid4())

    @app.get('/api/creation-test-health')
    def health():
        return dict(isolated=True, root=instance, assistantFixture=False, mode='live',
                    agentModel=args.agent_model, imageModel=args.image_model)

    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
