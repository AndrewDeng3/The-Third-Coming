"""Self-update (version checks, trusted downloads), brain sizes, and flight."""

from dataclasses import replace

from conftest import FLOOR_FEET, make, step

from stickfigure import firstrun, updater
from stickfigure.config import Config
from stickfigure.figure.rival import ROSTER, Rival


def test_versions_compare_numerically():
    assert updater.is_newer("1.1.10", "1.1.9")
    assert updater.is_newer("v1.2.0", "1.1.99")
    assert not updater.is_newer("1.1.3", "1.1.3")
    assert not updater.is_newer("1.1", "1.1.0")
    assert not updater.is_newer("1.1.2", "")  # running from source: never updates


def test_only_github_downloads_are_trusted():
    assert updater._trusted("https://github.com/TechWA1/The-Third-Coming/releases/download/v1.1.3/x.exe")
    assert updater._trusted("https://objects.githubusercontent.com/github-production-release-asset/abc")
    assert not updater._trusted("http://github.com/x.exe")  # not https
    assert not updater._trusted("https://github.com.evil.example/x.exe")
    assert not updater._trusted("https://evilgithub.com/x.exe")


def test_source_checkouts_dont_self_update():
    assert not updater.can_update()  # tests run from source (not a frozen build)


def test_brain_is_picked_by_graphics_memory():
    assert firstrun.recommended_brain(0).model == "qwen3:4b"
    assert firstrun.recommended_brain(4).model == "qwen3:4b"
    assert firstrun.recommended_brain(8).model == "qwen3:8b"
    assert firstrun.recommended_brain(16).model == "qwen3:14b"


def test_one_brain_does_everything():
    """Only the chosen model (plus the small memory model) is required: no hidden second big download."""
    cfg = replace(Config(), chat_model="qwen3:4b", extract_model="qwen3:4b", action_model="qwen3:4b",
                  code_model="qwen3:4b")
    assert firstrun.required_models(cfg) == ["qwen3:4b", "embeddinggemma"]


def test_saved_brain_choice_applies_to_every_role(tmp_path, monkeypatch):
    import json

    from stickfigure import config

    monkeypatch.setattr(config, "settings_path", lambda cfg=None: str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text(json.dumps({"chat_model": "qwen3:8b", "temperature": 5}))
    cfg = config.load_config()
    assert (cfg.chat_model, cfg.extract_model, cfg.action_model, cfg.code_model) == ("qwen3:8b",) * 4
    config.update_settings({"auto_update": True}, cfg)
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved == {"chat_model": "qwen3:8b", "temperature": 5, "auto_update": True}  # the rest is kept


def test_figure_flies_anywhere_through_windows_and_lands_on_its_feet():
    from stickfigure.world.geometry import Rect

    world, fig = make(Rect(600, 500, 1300, 900))
    step(world, fig, 1.0)
    fig.body.position = (400, FLOOR_FEET - 60)
    step(world, fig, 0.5)
    fig.fly((950, 300), 1200)  # straight up through the window in the way
    step(world, fig, 2.0)
    x, y = fig.body.position
    assert abs(x - 950) < 3 and abs(y - 300) < 3 and not fig.grounded
    fig.fly((950, FLOOR_FEET - 63), 1200)  # down through the window again
    step(world, fig, 2.0)
    fig.land()
    step(world, fig, 1.0)
    assert fig.grounded and abs(fig.feet[1] - FLOOR_FEET) < 3 and fig.knocked == 0


def test_rival_flies_and_drops_back_to_its_floor():
    r = Rival(ROSTER[0], 500, 1000, 1)
    r.fly((900, 400), 1500)
    for _ in range(180):
        r.update(1 / 60)
    assert abs(r.x - 900) < 2 and abs(r.y - 400) < 2 and not r.puppet.grounded
    r.land()
    for _ in range(120):
        r.update(1 / 60)
    assert r.lift == 0 and r.puppet.grounded
