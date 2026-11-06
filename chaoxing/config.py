"""配置加载：config.toml → Settings。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class HttpCfg:
    timeout: float = 30.0
    retries: int = 3
    backoff: float = 2.0
    min_gap: float = 0.6          # 全局最小请求间隔（秒）
    jitter: float = 0.4           # 在 min_gap 上叠加的随机抖动上限
    impersonate: str | None = None  # 有 curl_cffi 时可设 "chrome"
    proxy: str | None = None
    global_rate: float = 4.0
    global_burst: float = 6.0
    audit: bool = True


@dataclass(slots=True)
class VideoCfg:
    speed: float = 2.0            # 本地推进倍速
    interval: int = 60            # 上报间隔，卡片 reportTimeInterval 优先
    jitter: float = 0.3           # 间隔抖动比例
    max_rounds: int = 3000
    instant_skip: bool = False     # 先试 playingTime=duration 秒过
    audio_fallback: bool = True   # 视频失败后用 dtype=Audio 重来


@dataclass(slots=True)
class QuizCfg:
    submit_threshold: float = 0.8  # 覆盖率达标才提交
    think_min: float = 0.5
    think_max: float = 1.5
    count_ai_as_known: bool = True  # AI 给出的答案是否计入覆盖率


@dataclass(slots=True)
class AiCfg:
    """OpenAI 标准 /v1/chat/completions 接口的答题配置。"""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    timeout: float = 60.0
    retries: int = 2
    batch_size: int = 12
    max_tokens: int = 1600
    json_mode: str = "schema"  # schema | object | text，服务端不支持时自动降级


@dataclass(slots=True)
class RiskCfg:
    daily_cap_hours: float = 6.0
    cooldown_seconds: int = 600
    max_cooldowns: int = 3
    # 同时在飞的视频通道上限。实测本账号 68 个不同 objectId / 70 秒就会吃到 9010，
    # 所以并发该按"同时在跑几个视频"来卡，而不是按请求速率。
    max_lanes: int = 8


@dataclass(slots=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    db_path: Path = PROJECT_ROOT / "data" / "cx.db"
    log_path: Path = PROJECT_ROOT / "data" / "cx.log"
    http: HttpCfg = field(default_factory=HttpCfg)
    video: VideoCfg = field(default_factory=VideoCfg)
    quiz: QuizCfg = field(default_factory=QuizCfg)
    ai: AiCfg = field(default_factory=AiCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    types: tuple[str, ...] = ("video", "document", "read", "emptypage", "work")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "fonts").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "unresolved").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "state").mkdir(parents=True, exist_ok=True)


def _pick(dc_type: type, raw: dict[str, Any] | None):
    """只取 dataclass 已声明的字段，忽略未知键，便于配置向前兼容。"""
    raw = raw or {}
    allowed = {f for f in dc_type.__dataclass_fields__}  # type: ignore[attr-defined]
    return dc_type(**{k: v for k, v in raw.items() if k in allowed})


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.toml"
    if not cfg_path.exists():
        s = Settings()
        s.ensure_dirs()
        return s

    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    base = Settings()
    if d := raw.get("data_dir"):
        base.data_dir = (PROJECT_ROOT / d).resolve() if not Path(d).is_absolute() else Path(d)
    base.db_path = base.data_dir / "cx.db"
    base.log_path = base.data_dir / "cx.log"
    base.http = _pick(HttpCfg, raw.get("http"))
    base.video = _pick(VideoCfg, raw.get("video"))
    base.quiz = _pick(QuizCfg, raw.get("quiz"))
    base.ai = _pick(AiCfg, raw.get("ai"))
    base.risk = _pick(RiskCfg, raw.get("risk"))
    if t := raw.get("types"):
        base.types = tuple(t)
    base.ensure_dirs()
    return replace(base)


DEFAULT_CONFIG_TOML = """# 超星学习通刷课工具配置
data_dir = "data"

[http]
timeout      = 30
retries      = 3
backoff      = 2.0
min_gap      = 0.6        # 全局最小请求间隔（秒）
jitter       = 0.4
impersonate  = ""          # 装了 curl_cffi 可填 "chrome"
proxy        = ""          # 单账号固定出口，多账号才需要
global_rate  = 4.0         # 全账号每秒最多几个请求（跨通道总闸）
global_burst = 6.0         # 允许的短时突发
audit        = true

[video]
speed          = 2.0       # 本地推进倍速，建议 1.0~2.0
interval       = 60        # 兜底上报间隔，卡片 reportTimeInterval 优先
jitter         = 0.3
max_rounds     = 3000
instant_skip   = false
audio_fallback = true

[quiz]
submit_threshold = 0.8     # 覆盖率低于此值只保存不提交
think_min        = 0.5
think_max        = 1.5
count_ai_as_known = true   # AI 答案计入覆盖率

# OpenAI 兼容接口：填 base_url 与 api_key 就能用，走 /v1/chat/completions
# 例：base_url = "https://api.openai.com/v1"
#     base_url = "https://api.deepseek.com/v1"
#     base_url = "https://xxx.azure.com/openai/deployments/gpt-4o"（自备代理时按需改）
[ai]
enabled     = false
base_url    = "https://api.openai.com/v1"
api_key     = ""
model       = "gpt-4o-mini"
temperature = 0.2
timeout     = 60
retries     = 2
batch_size  = 12
max_tokens  = 1600
json_mode   = "schema"     # schema | object | text，服务端不支持会自动降级

[risk]
daily_cap_hours  = 6.0
cooldown_seconds = 600
max_cooldowns    = 3
max_lanes        = 8          # 同时在飞的视频通道上限，超过会被夹到该值

types = ["video", "document", "read", "emptypage", "work"]
"""
