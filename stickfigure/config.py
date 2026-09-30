"""Tunables. Distances are physical pixels, times are seconds."""

import json
import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Config:
    # Physics
    physics_hz: int = 120
    gravity: float = 2400.0
    max_substeps: int = 8

    # Figure collider (total height = body_height + 2 * body_radius)
    body_width: float = 16.0
    body_height: float = 96.0
    body_radius: float = 12.0
    body_mass: float = 1.0
    walk_speed: float = 130.0
    run_speed: float = 300.0
    run_threshold: float = 350.0  # walk targets farther than this are run to
    air_control: float = 600.0  # px/s^2 horizontal accel while airborne and drifting
    max_throw_speed: float = 3500.0
    jump_speed: float = 1150.0  # max vertical launch speed -> max rise ~ v^2 / 2g ~ 275 px
    max_jump_vx: float = 520.0
    jump_prep_time: float = 0.13
    tumble_threshold: float = 700.0  # throws faster than this spin the figure
    knockdown_impact: float = 1100.0  # landing speed that knocks the figure over
    knocked_time: float = 1.3

    # Platforms
    platform_thickness: float = 3.0
    max_carry_per_frame: float = 400.0  # larger jumps (snap/maximize) drop the figure instead
    tracker_poll_interval: float = 0.2

    # Blocks (staircase building)
    block_size: float = 60.0
    block_lifetime: float = 25.0
    max_blocks: int = 10  # temporary (staircase) blocks
    max_permanent_blocks: int = 400  # structures + whatever you build in build mode
    stair_rise: float = 150.0
    stair_run: float = 74.0

    # Rendering
    frame_hz: int = 60
    stroke: float = 8.0  # limb thickness
    hit_width: float = 22.0  # invisible grab area around limbs

    # Safety / controls
    respawn_after_offscreen: float = 1.5

    # Brain (Phase 3)
    buddy_name: str = "The Third Coming"  # fixed (see agent/lore.py); not a user setting
    user_name: str = ""  # what the figure calls you ("" = unknown, it may learn it from chat)
    ollama_url: str = "http://127.0.0.1:11434"
    # One model for chat, memory, and hands: it stays loaded (no swapping). qwen3:14b fits a 16 GB GPU, streams its
    # first words in ~0.1-0.2 s, and thinks step by step (streamed) only for hard questions. (qwen3:8b is the
    # faster, lighter alternative for smaller GPUs.)
    chat_model: str = "qwen3:14b"
    extract_model: str = "qwen3:14b"
    embed_model: str = "embeddinggemma"
    code_model: str = "qwen3:14b"  # writes code/long text for tasks ("qwen2.5-coder:7b" is an alternative)
    history_messages: int = 40  # recent chat turns sent with every message
    recall_facts: int = 10  # long-term facts recalled by relevance (+ the most recent ones)
    recall_episodes: int = 4  # older conversations recalled by relevance
    max_reply_tokens: int = 1500  # long enough for code and explanations (casual replies stay short)
    idle_chatter: tuple[float, float] = (240.0, 540.0)  # random gap between spontaneous remarks
    think_gap: tuple[float, float] = (25.0, 60.0)  # the mind decides what to do next this often
    # Actions (Phase 5)
    action_model: str = "qwen3:14b"
    supervised: bool = False  # True = approve every step; False = steps just run (risky ones still ask)
    max_steps: int = 60
    max_failures: int = 3
    typing_wpm: int = 80
    paste_threshold: int = 40  # longer text is pasted (clipboard saved & restored) instead of typed
    max_clicks_per_sec: float = 3.0
    approval_timeout: float = 90.0
    # What stops a running task besides Ctrl+Alt+Pause / Stop / "stop" in chat.
    # Options: "esc", "click", "any_key", "mouse_move". Moving the mouse doesn't stop anything by default.
    override_triggers: tuple[str, ...] = ("esc",)

    # Pet mischief: harmless unprompted input (cursor tugs, a bit of scrolling, hovering).
    # Never clicks or types; only when you've been idle; any real input from you stops it.
    mischief: bool = True
    mischief_idle: float = 45.0  # seconds without user input before mischief is allowed
    mischief_gap: tuple[float, float] = (240.0, 720.0)  # random pause between pranks
    # Idle adventures: look things up in a new browser tab / peek at your tabs while you're away.
    adventures: bool = True
    adventure_idle: float = 90.0  # seconds without user input first
    adventure_gap: tuple[float, float] = (600.0, 1500.0)  # 10-25 minutes between adventures

    # Lounge & curiosity
    lounge_after: float = 60.0  # quiet seconds (chat open) before the chat turns into the lounge
    notice_activity: bool = True  # now and then, peek at what you're doing and ask about it
    awareness: bool = True  # keep track of apps / window titles / activity (in memory only, no screenshots)
    # Temperature 1..10: how often it does things on its own (pranks, adventures, thinking) and how much it clicks
    # around in its own web searches. 3 = the defaults above; 10 ~ 3x as often; 1 ~ 3x less.
    temperature: int = 3
    peek_gap: tuple[float, float] = (420.0, 1080.0)  # random pause between peeks (7-18 min)

    # Voice (Phase 6)
    voice_enabled: bool = True
    tts_voice: str = "af_heart"
    tts_speed: float = 1.05
    tts_volume: float = 0.9
    stt_model: str = "base.en"  # faster-whisper model; "small.en" is more accurate, a bit slower
    stt_device: str = "cpu"  # "cuda" needs cuBLAS/cuDNN installed
    listen_silence: float = 0.9  # seconds of quiet that end an utterance
    listen_max: float = 20.0
    color: str = "Orange"
    # Updates (installed copies): check GitHub for a newer release now and then; ask in chat before installing,
    # unless auto_update is on (then it installs by itself while you're away from the computer).
    check_updates: bool = True
    auto_update: bool = False

    data_dir: str = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StickFigure")

    @property
    def figure_height(self) -> float:
        return self.body_height + 2 * self.body_radius


# Settings the user can change in the Settings window (saved to <data_dir>/settings.json).
USER_SETTINGS = (
    "user_name", "color", "voice_enabled", "tts_voice", "tts_speed", "tts_volume", "stt_model",
    "mischief", "supervised", "chat_model", "notice_activity", "adventures", "awareness", "temperature",
    "check_updates", "auto_update",
)


def temp_scale(temperature: int) -> float:
    """Multiplier for "time between spontaneous things": 1.0 at the default 3, ~0.3 at 10, 3.0 at 1."""
    return 3.0 / max(1, min(10, int(temperature)))


def temp_clicks(temperature: int) -> int:
    """How many search results it clicks into on one web adventure."""
    return (0, 0, 1, 1, 1, 2, 2, 2, 3, 3)[max(1, min(10, int(temperature))) - 1]


def settings_path(cfg: Config | None = None) -> str:
    return os.path.join((cfg or Config()).data_dir, "settings.json")


def load_config() -> Config:
    """Defaults, overridden by the user's saved settings (unknown or malformed entries are ignored)."""
    base = Config()
    try:
        with open(settings_path(base), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return base
    changes = {}
    for key in USER_SETTINGS:
        if key in saved:
            default = getattr(base, key)
            try:
                changes[key] = type(default)(saved[key])
            except (TypeError, ValueError):
                pass
    if "chat_model" in changes:  # one brain: the chosen model does chat, memory, and tasks (no model swapping)
        m = changes["chat_model"]
        changes.update(extract_model=m, action_model=m, code_model=m)
    return replace(base, **changes)


def load_saved_settings(cfg: Config | None = None) -> dict:
    try:
        with open(settings_path(cfg), encoding="utf-8") as f:
            saved = json.load(f)
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def update_settings(changes: dict, cfg: Config | None = None) -> None:
    """Change some saved settings, keeping the rest."""
    save_settings({**load_saved_settings(cfg), **changes}, cfg)


def save_settings(values: dict, cfg: Config | None = None) -> None:
    path = settings_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = {k: v for k, v in values.items() if k in USER_SETTINGS}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2)


CONFIG = load_config()
