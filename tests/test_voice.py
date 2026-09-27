import json

import numpy as np

from stickfigure import config as C
from stickfigure.voice.stt import FRAME, RATE, Endpointer
from stickfigure.voice.tts import SentenceSplitter, speakable


def test_sentence_splitter_streams_complete_sentences():
    s = SentenceSplitter()
    assert s.feed("Hi there! How are") == ["Hi there!"]
    assert s.feed("Hi there! How are you doing? I am") == ["How are you doing?"]
    assert s.feed("Hi there! How are you doing? I am") == []
    assert s.flush("Hi there! How are you doing? I am fine") == ["I am fine"]


def test_sentence_splitter_keeps_closing_quotes():
    s = SentenceSplitter()
    assert s.feed('He said "wow!" Then left. ') == ['He said "wow!"', "Then left."]


def test_speakable_strips_markup_and_links():
    assert speakable("**Look** at https://x.com/abc now") == "Look at a link now"


def frames(level, seconds):
    n = int(seconds * RATE / FRAME)
    return [level] * n


def run(ep, levels):
    return [ep.push(r) for r in levels]


def test_endpointer_detects_utterance_and_its_end():
    ep = Endpointer(silence=0.6)
    out = run(ep, frames(0.003, 0.5) + frames(0.08, 1.0) + frames(0.003, 0.7))
    assert "speech" in out and out[-1] == "end"


def test_endpointer_ignores_single_clicks_and_steady_noise():
    ep = Endpointer(silence=0.6)
    out = run(ep, frames(0.02, 1.0) + [0.2] + frames(0.02, 1.0))  # fan hum + one click
    assert "end" not in out and not ep.speech


def test_endpointer_tolerates_short_pauses_between_words():
    ep = Endpointer(silence=0.9)
    out = run(ep, frames(0.003, 0.3) + frames(0.08, 0.5) + frames(0.003, 0.4) + frames(0.08, 0.5))
    assert "end" not in out


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "settings_path", lambda cfg=None: str(tmp_path / "settings.json"))
    C.save_settings({"buddy_name": "Ziggy", "tts_speed": 1.2, "mischief": False, "gravity": 1})  # name & gravity ignored
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "gravity" not in saved
    cfg = C.load_config()
    assert cfg.buddy_name == "The Third Coming" and cfg.tts_speed == 1.2 and cfg.mischief is False
    assert cfg.gravity == C.Config().gravity


def test_bad_settings_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "settings_path", lambda cfg=None: str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text("{not json")
    assert C.load_config() == C.Config()
    (tmp_path / "settings.json").write_text(json.dumps({"tts_speed": "fast"}))
    assert C.load_config().tts_speed == C.Config().tts_speed
