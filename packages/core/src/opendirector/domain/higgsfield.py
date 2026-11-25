"""Supported Higgsfield Open model families and reference limits."""

from .projects import ProjectError

BASE = 'https://api.higgsfield.ai'
IMAGE_MODEL = 'kling-image/v4.0'
VIDEO_MODEL = 'kling-video/v4.0'
IMAGE_RATIOS = ('auto', '1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '21:9')
IMAGE_BYTES = 10 * 1024 * 1024


def validate_model(config):
    models = {'image': IMAGE_MODEL, 'video': VIDEO_MODEL}
    if (config['url'].rstrip('/') != BASE or config.get('authMode', 'bearer') != 'bearer'
            or config.get('connectionType', 'api') != 'api'
            or models.get(config['kind']) != config['modelId']):
        raise ProjectError('请选择 Higgsfield Open 支持的模型和官方 API 地址。', 422, code='higgsfield_model')
