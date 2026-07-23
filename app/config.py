from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT_DIR / ".runtime"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
LOCAL_CONFIG_PATH = ROOT_DIR / "local_config.yaml"
LOCAL_CONFIG_EXAMPLE_PATH = ROOT_DIR / "local_config.example.yaml"


class Settings(BaseSettings):
    """运行时设置。优先读 local_config.yaml，其次 .env / 环境变量。"""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "openai"
    llm_base_url: str = "https://api.ali.cdn.pianke.xyz/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek/deepseek-v4-flash"

    stitch_api_key: str = ""
    stitch_mcp_url: str = "https://stitch.googleapis.com/mcp"
    stitch_oauth_token: str = ""
    stitch_oauth_client_id: str = ""
    stitch_oauth_client_secret: str = ""

    uniapp_template_dir: str = "/Users/shihongwei/n8n/uniapp/uniapp_base"
    uniapp_output_dir: str = "/Users/shihongwei/n8n/uniapp"

    codex_bin: str = "codex"
    codex_model: str = ""
    codex_sandbox: str = "workspace-write"

    claude_bin: str = "claude"
    claude_model: str = ""

    cursor_bin: str = "agent"
    cursor_model: str = ""

    host: str = "0.0.0.0"
    port: int = 8765

    hbuilderx_cli: str = ""
    hbuilderx_app: str = "/Applications/HBuilderX.app"

    publish_default_repo: str = "/Users/shihongwei/uniapp1/uniapp"
    publish_base_branch: str = "main"


def load_local_config() -> dict[str, Any]:
    """读取项目根目录 local_config.yaml（密钥与接口配置）。"""
    path = LOCAL_CONFIG_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _settings_overrides_from_local(local: dict[str, Any]) -> dict[str, Any]:
    """把 local_config.yaml 嵌套结构映射为 Settings 字段。"""
    overrides: dict[str, Any] = {}

    llm = local.get("llm") if isinstance(local.get("llm"), dict) else {}
    if llm.get("provider"):
        overrides["llm_provider"] = str(llm["provider"]).strip()
    if llm.get("base_url"):
        overrides["llm_base_url"] = str(llm["base_url"]).strip().replace(" ", "")
    if "api_key" in llm and llm.get("api_key") is not None:
        overrides["llm_api_key"] = str(llm.get("api_key") or "").strip()
    if llm.get("model"):
        overrides["llm_model"] = str(llm["model"]).strip()

    stitch = local.get("stitch") if isinstance(local.get("stitch"), dict) else {}
    if "api_key" in stitch and stitch.get("api_key") is not None:
        overrides["stitch_api_key"] = str(stitch.get("api_key") or "").strip()
    if stitch.get("mcp_url"):
        overrides["stitch_mcp_url"] = str(stitch["mcp_url"]).strip()
    if "oauth_token" in stitch:
        overrides["stitch_oauth_token"] = str(stitch.get("oauth_token") or "").strip()
    if "oauth_client_id" in stitch:
        overrides["stitch_oauth_client_id"] = str(stitch.get("oauth_client_id") or "").strip()
    if "oauth_client_secret" in stitch:
        overrides["stitch_oauth_client_secret"] = str(stitch.get("oauth_client_secret") or "").strip()

    uniapp = local.get("uniapp") if isinstance(local.get("uniapp"), dict) else {}
    if uniapp.get("template_dir"):
        overrides["uniapp_template_dir"] = str(uniapp["template_dir"]).strip()
    if uniapp.get("output_dir"):
        overrides["uniapp_output_dir"] = str(uniapp["output_dir"]).strip()

    codegen = local.get("codegen") if isinstance(local.get("codegen"), dict) else {}
    for src, dest in (
        ("codex_bin", "codex_bin"),
        ("codex_model", "codex_model"),
        ("codex_sandbox", "codex_sandbox"),
        ("claude_bin", "claude_bin"),
        ("claude_model", "claude_model"),
        ("cursor_bin", "cursor_bin"),
        ("cursor_model", "cursor_model"),
    ):
        if src in codegen and codegen.get(src) is not None:
            overrides[dest] = str(codegen.get(src) or "").strip()

    server = local.get("server") if isinstance(local.get("server"), dict) else {}
    if server.get("host"):
        overrides["host"] = str(server["host"]).strip()
    if server.get("port") is not None and str(server.get("port")).strip():
        overrides["port"] = int(server["port"])

    hbx = local.get("hbuilderx") if isinstance(local.get("hbuilderx"), dict) else {}
    if hbx.get("cli"):
        overrides["hbuilderx_cli"] = str(hbx["cli"]).strip()
    if hbx.get("app"):
        overrides["hbuilderx_app"] = str(hbx["app"]).strip()

    publish = local.get("publish") if isinstance(local.get("publish"), dict) else {}
    if publish.get("default_repo"):
        overrides["publish_default_repo"] = str(publish["default_repo"]).strip()
    if publish.get("base_branch"):
        overrides["publish_base_branch"] = str(publish["base_branch"]).strip()

    return overrides


@lru_cache
def get_settings() -> Settings:
    local = load_local_config()
    overrides = _settings_overrides_from_local(local)
    # 显式传入的 overrides 优先于 .env，便于只改 local_config.yaml
    return Settings(**overrides)


def get_llm_model_options() -> list[str]:
    """前端模型下拉列表：优先 local_config.yaml 的 llm.models。"""
    local = load_local_config()
    llm = local.get("llm") if isinstance(local.get("llm"), dict) else {}
    models = llm.get("models")
    if isinstance(models, list) and models:
        return [str(m).strip() for m in models if str(m).strip()]
    cfg = load_yaml_config()
    from_yaml = cfg.get("llm_models") or []
    if isinstance(from_yaml, list) and from_yaml:
        return [str(m).strip() for m in from_yaml if str(m).strip()]
    settings = get_settings()
    return [settings.llm_model] if settings.llm_model else []


@lru_cache
def load_yaml_config() -> dict[str, Any]:
    path = ROOT_DIR / "config.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_runtime_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def reload_settings() -> Settings:
    """测试或热更新时清缓存后重新加载。"""
    get_settings.cache_clear()
    load_yaml_config.cache_clear()
    return get_settings()
