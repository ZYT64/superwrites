"""配置管理：读写全局 config.json。

设计原则：
- API Key 永远不写入源码、永远不提交到版本控制（config.json 在 .gitignore 内）
- 优先级：环境变量 DEEPSEEK_API_KEY > config.json.api_key
- 项目级配置（章节状态、字数）由 novel_engine 直接读写 metadata.json，本模块只管全局配置
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 全局配置文件路径（项目根目录下）
CONFIG_FILE_NAME = "config.json"
ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"

# 默认配置
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_STREAM = True
DEFAULT_SELF_CHECK_THRESHOLD = 0.6
DEFAULT_NOVELS_DIR = "novels"
DEFAULT_WORDS_PER_CHAPTER = 3000  # 默认每章目标字数（网文主流）
DEFAULT_TARGET_TOTAL_WORDS = 300_000  # 默认整本目标字数（30 万字）
DEFAULT_DIRECTIONS_COUNT = 3  # 每次生成的故事方向数
DEFAULT_TITLES_COUNT = 5  # 每次生成的书名候选数


@dataclass
class AppConfig:
    """全局应用配置。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    stream: bool = DEFAULT_STREAM
    self_check_threshold: float = DEFAULT_SELF_CHECK_THRESHOLD
    novels_dir: str = DEFAULT_NOVELS_DIR
    default_words_per_chapter: int = DEFAULT_WORDS_PER_CHAPTER
    default_target_total_words: int = DEFAULT_TARGET_TOTAL_WORDS
    max_retries: int = 3  # AI 调用失败最大重试次数
    directions_count: int = DEFAULT_DIRECTIONS_COUNT
    titles_count: int = DEFAULT_TITLES_COUNT

    def has_api_key(self) -> bool:
        """是否已有可用的 API Key（环境变量或配置文件）。"""
        if os.environ.get(ENV_API_KEY, "").strip():
            return True
        return bool(self.api_key.strip())

    def effective_api_key(self) -> str:
        """获取实际生效的 API Key（环境变量优先）。"""
        env_key = os.environ.get(ENV_API_KEY, "").strip()
        if env_key:
            return env_key
        return self.api_key.strip()

    def effective_base_url(self) -> str:
        env = os.environ.get(ENV_BASE_URL, "").strip()
        return env or self.base_url

    def effective_model(self) -> str:
        env = os.environ.get(ENV_MODEL, "").strip()
        return env or self.model


def config_path(root: Path | None = None) -> Path:
    """全局 config.json 的路径。"""
    base = root or Path.cwd()
    return base / CONFIG_FILE_NAME


def load_config(root: Path | None = None) -> AppConfig:
    """从 config.json 加载配置；不存在则返回默认值。

    参数:
        root: 项目根目录，None 表示使用当前工作目录。

    返回:
        AppConfig 实例。
    """
    path = config_path(root)
    if not path.exists():
        return AppConfig()
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 文件损坏时退回默认，但保留用户能编辑的入口
        return AppConfig()

    # 用 dataclass 字段过滤，避免多余键污染
    field_names = {f for f in AppConfig.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in field_names}
    return AppConfig(**filtered)


def load_env_file(env_path: Path) -> dict[str, str]:
    """从 .env 文件加载 KEY=VALUE 格式配置。

    返回: {"API_KEY": "...", "BASE_URL": "...", "MODEL": "..."}
    未找到的字段返回空字符串（不在 dict 中）。
    """
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return result
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 仅识别我们关心的三个变量
        if key == ENV_API_KEY:
            result["api_key"] = value
        elif key == ENV_BASE_URL:
            result["base_url"] = value
        elif key == ENV_MODEL:
            result["model"] = value
    return result


def auto_discover_config(root: Path | None = None) -> tuple[AppConfig, str]:
    """按优先级发现配置：环境变量 > .env 文件 > config.json > 默认值。

    返回: (AppConfig, 来源标签)
      来源标签: "env" / "dotenv" / "file" / "default"
    """
    # 1. 优先环境变量
    env_key = os.environ.get(ENV_API_KEY, "").strip()
    if env_key:
        cfg = AppConfig()
        cfg.api_key = env_key
        env_url = os.environ.get(ENV_BASE_URL, "").strip()
        if env_url:
            cfg.base_url = env_url
        env_model = os.environ.get(ENV_MODEL, "").strip()
        if env_model:
            cfg.model = env_model
        return cfg, "env"

    # 2. .env 文件
    base = root or Path.cwd()
    env_data = load_env_file(base / ".env")
    if env_data.get("api_key"):
        cfg = AppConfig()
        cfg.api_key = env_data["api_key"]
        if env_data.get("base_url"):
            cfg.base_url = env_data["base_url"]
        if env_data.get("model"):
            cfg.model = env_data["model"]
        return cfg, "dotenv"

    # 3. config.json
    return load_config(base), "file" if config_path(base).exists() else "default"


def save_config(config: AppConfig, root: Path | None = None) -> Path:
    """保存配置到 config.json。

    注意：不会写入环境变量覆盖的部分（避免误导用户）。
    """
    path = config_path(root)
    data = asdict(config)
    # 保留用户的 api_key 字段（即使它被环境变量覆盖，写回去也是合理的——便于 UI 显示）
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def mask_api_key(key: str) -> str:
    """把 API Key 中间部分掩码显示（如 sk-abc****xyz）。"""
    if not key:
        return "(未设置)"
    if len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]


def list_projects(novels_dir: Path) -> list[dict[str, Any]]:
    """列出 novels_dir 下所有有效小说项目（包含 metadata.json 的子目录）。

    返回:
        [{"name": "剑破苍穹", "title": "...", "chapters": 30, "completed": 5}, ...]
    """
    if not novels_dir.exists():
        return []

    projects: list[dict[str, Any]] = []
    for entry in sorted(novels_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            projects.append(
                {
                    "name": entry.name,
                    "title": entry.name,
                    "chapters": 0,
                    "completed": 0,
                    "broken": True,
                }
            )
            continue
        projects.append(
            {
                "name": entry.name,
                "title": meta.get("title", entry.name),
                "chapters": meta.get("total_chapters", 0),
                "completed": meta.get("completed_chapters", 0),
                "current_model": meta.get("current_model", ""),
                "broken": False,
            }
        )
    return projects