"""数据目录：都落在插件目录下的 data/（凭证、订阅配置、推送状态、图片缓存）。"""
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _PLUGIN_ROOT / "data"


def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
