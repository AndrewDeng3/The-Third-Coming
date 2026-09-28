"""Level-1 awareness: app switches, page changes, activity, away/back, time per app, privacy."""

from stickfigure.awareness import AWAY_AFTER, Awareness


def feed(a, t, process="chrome.exe", title="Inbox - Google Chrome", private=False, cursor=(0, 0), idle=0.2):
    a.observe(t, process, title, private, cursor, idle)


def test_app_switches_and_page_changes_are_logged():
    a = Awareness()
    t = 1_000_000.0
    feed(a, t)
    feed(a, t + 1, process="Code.exe", title="main.py - StickFigure - Visual Studio Code")
    feed(a, t + 2, process="Code.exe", title="app.py - StickFigure - Visual Studio Code")
    feed(a, t + 2.5, process="Code.exe", title="app.py - StickFigure - Visual Studio Code")
    feed(a, t + 6, process="Code.exe", title="app.py - StickFigure - Visual Studio Code")
    texts = [e for _, e in a.events]
    assert texts[0] == "The user is in Chrome ('Inbox')"  # the ' - Google Chrome' suffix is dropped
    assert texts[1].startswith("The user switched from Chrome to VS Code")
    assert texts[-1] == "In VS Code, the user moved on to 'app.py - StickFigure'"


def test_flickering_titles_while_loading_count_once():
    a = Awareness()
    t = 1_000_000.0
    feed(a, t, title="Google - Google Chrome")
    for i, title in enumerate(["Loading...", "cats - Google Search", "cats - Google Search"]):
        feed(a, t + 0.5 * (i + 1), title=f"{title} - Google Chrome")
    feed(a, t + 5, title="cats - Google Search - Google Chrome")
    moves = [e for _, e in a.events if "moved on" in e]
    assert moves == ["In Chrome, the user moved on to 'cats - Google Search'"]


def test_typing_mouse_reading_away_and_back():
    a = Awareness()
    t = 1_000_000.0
    feed(a, t, cursor=(0, 0), idle=0.1)
    feed(a, t + 0.5, cursor=(0, 0), idle=0.1)  # input without the cursor moving: typing
    assert a.s.activity == "typing"
    feed(a, t + 1, cursor=(300, 200), idle=0.1)
    assert a.s.activity == "mouse"
    feed(a, t + 10, cursor=(300, 200), idle=8)
    assert a.s.activity == "reading"
    feed(a, t + 200, cursor=(300, 200), idle=AWAY_AFTER + 10)
    assert a.s.activity == "away" and "stepped away" in a.events[-1][1]
    feed(a, t + 900, cursor=(310, 200), idle=0.1)
    assert a.events[-1][1].startswith("The user came back after 13 min")  # away since idle began


def test_private_windows_are_never_named():
    a = Awareness()
    feed(a, 1_000_000.0, process="chrome.exe", title="Chase Online Banking - Google Chrome", private=True)
    a.set_focus("Edit 'Account number'")
    text = a.summary()
    assert "Chase" not in text and "Banking" not in text and "Account" not in text
    assert "a private window" in text


def test_time_per_app_and_summary():
    a = Awareness()
    t = 1_000_000.0
    for i in range(0, 400):
        feed(a, t + i * 0.5, process="Spotify.exe", title="Song - Artist")
    assert a.today_text().startswith("Spotify 3 min")
    assert "Right now the user is in Spotify" in a.summary()


def test_disabled_means_nothing():
    a = Awareness()
    a.enabled = False
    feed(a, 1_000_000.0)
    assert a.summary() == "" and not a.events
