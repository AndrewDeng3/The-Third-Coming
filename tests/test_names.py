from stickfigure.agent.lore import LORE
from stickfigure.agent.persona import Situation, system_prompt
from stickfigure.agent.emotion import Emotion
from stickfigure.config import Config
from stickfigure.names import NAME


def test_name_is_permanent():
    assert NAME == "The Third Coming" and Config().buddy_name == NAME
    assert "buddy_name" not in __import__("stickfigure.config", fromlist=["USER_SETTINGS"]).USER_SETTINGS


def test_lore_is_in_the_system_prompt():
    p = system_prompt(NAME, Emotion(), Situation("sitting", "the taskbar"), [])
    assert "Second Coming" in p and "Animator" in p and LORE[:40] in p
