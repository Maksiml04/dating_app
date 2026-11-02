"""Регрессии, предотвращающие повторный отказ Docker build на pip install ."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_package_discovery():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert config['tool']['setuptools']['packages']['find']['include'] == ['app*']
    assert config['build-system']['build-backend'] == 'setuptools.build_meta'


def test_docker_copies_app_before_install():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert dockerfile.index('COPY app ./app') < dockerfile.index('RUN pip install --no-cache-dir')
