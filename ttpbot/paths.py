import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir(env=None):
    source = os.environ if env is None else env
    configured = str(source.get('TTPBOT_DATA_DIR') or '').strip()
    if configured:
        return Path(configured)
    return PROJECT_ROOT


def runtime_path(name, env=None):
    return data_dir(env) / name


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
