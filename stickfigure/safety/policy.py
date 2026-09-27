"""The action guard: a pure function from (proposed action, target window, focused element, mode)
to allow / ask / deny. No I/O, so it's exhaustively testable.

Layers (all must pass):
1. Window: never act in terminals, Run dialogs, system tools, credential prompts, password
   managers, or windows whose title looks like banking/payment.
2. Keys: hotkeys come from an allowlist (no Win or Alt combos, no Ctrl+Alt+Del style chords).
3. Typing: never into password fields or terminal panes; never shell-command-looking text.
4. Clicks: anything destructive-sounding ("Delete", "Send", "Pay", ...) always asks, even when
   the user has pre-approved the rest of the task.
Everything else asks in supervised mode (the default) unless the user approved the whole task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from stickfigure.perception.uia import UIElement

DENY_PROCESSES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "openconsole.exe", "conhost.exe",
    "bash.exe", "wsl.exe", "wslhost.exe", "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe",
    "regedit.exe", "taskmgr.exe", "mmc.exe", "msconfig.exe", "control.exe", "systemsettings.exe", "gpedit.exe",
    "services.exe", "eventvwr.exe", "diskmgmt.exe", "compmgmt.exe", "resmon.exe", "perfmon.exe", "sysdm.cpl",
    "consent.exe", "credentialuibroker.exe", "lockapp.exe", "logonui.exe",
    "1password.exe", "keepass.exe", "keepassxc.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe",
    "stickfigure.exe",
}
DENY_CLASSES = {
    "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow",
    "Credential Dialog Xaml Host", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
    "Windows.UI.Core.CoreWindow", "LockScreenControllerProxyWindow",
}
DENY_TITLE = re.compile(
    r"(^run$|user account control|windows security|credential|password manager|bitwarden|1password|keepass|"
    r"lastpass|online banking|\bbank\b|paypal|venmo|coinbase|crypto wallet|metamask|checkout|payment)",
    re.I,
)
TERMINAL_HINT = re.compile(r"(terminal|console|xterm|command prompt|powershell|\bbash\b|\bshell\b)", re.I)
SHELL_TEXT = re.compile(
    r"(\b(powershell|pwsh|wsl|rundll32|regsvr32|mshta|certutil|bitsadmin|schtasks|diskpart|bcdedit|vssadmin|wmic|"
    r"invoke-expression|start-process|invoke-webrequest)\b"
    r"|\bcmd(\.exe)?\s*/[ck]\b|\breg\s+(add|delete)\b|\bnet\s+user\b|\bshutdown\s+/|\biex\s*\("
    r"|\brm\s+-rf\b|\bdel\s+/[fsq]\b|\brmdir\s+/s\b|\bformat\s+[a-z]:"
    r"|\.(exe|bat|cmd|ps1|vbs|msi|scr|lnk)\b"
    r"|^\s*(javascript|vbscript|file|ms-settings|shell|search-ms|ms-msdt)\s*:)",
    re.I | re.M,
)
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|erase|discard|trash|bin|send|submit|post|publish|pay|purchase|buy|order|checkout|"
    r"confirm|transfer|uninstall|format|reset|sign out|log ?out|close account|unsubscribe|permanently|"
    r"overwrite|replace all|revoke|deactivate)\b",
    re.I,
)

SAFE_SINGLE = {"enter", "tab", "esc", "backspace", "delete", "space", "home", "end", "pageup", "pagedown",
               "left", "right", "up", "down"}
SAFE_COMBOS = {
    *(f"ctrl+{k}" for k in "acvxzysfbiu"),
    "ctrl+home", "ctrl+end", "ctrl+left", "ctrl+right", "ctrl+backspace",
    "shift+tab", "shift+left", "shift+right", "shift+up", "shift+down", "shift+home", "shift+end", "shift+enter",
    "ctrl+shift+z", "ctrl+shift+left", "ctrl+shift+right", "ctrl+shift+end", "ctrl+shift+home",
    # browsing / editors
    "ctrl+t", "ctrl+l", "ctrl+tab", "ctrl+shift+tab", "ctrl+enter", "ctrl+/", "ctrl+d", "ctrl+]",
    "ctrl+[", "ctrl+plus", "ctrl+minus", "ctrl+0", "ctrl+pageup", "ctrl+pagedown",
}
# Can submit, close, or destroy something: always asks (in chat), even in an approved task.
ALWAYS_CONFIRM_KEYS = {"enter", "ctrl+x", "delete", "backspace", "ctrl+backspace", "ctrl+enter"}
TEXT_EDIT_KEYS = {"enter", "shift+enter", "ctrl+x", "delete", "backspace", "ctrl+backspace"}  # harmless in a document

MAX_TEXT = 5000

ALLOW, ASK, DENY = "allow", "ask", "deny"


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    process: str
    cls: str
    title: str


@dataclass(frozen=True)
class Proposed:
    kind: str  # click | double_click | type_text | hotkey | scroll | wait | ask_user | done
    element: UIElement | None = None  # clicked element (resolved at the click point)
    text: str = ""
    keys: str = ""
    amount: int = 0


@dataclass(frozen=True)
class Decision:
    verdict: str  # allow | ask | deny
    reason: str
    always_ask: bool = False  # destructive: ask even if the task was pre-approved


def normalize_keys(keys: str) -> str:
    parts = [p.strip().lower() for p in keys.replace(" ", "").split("+") if p.strip()]
    alias = {"control": "ctrl", "return": "enter", "escape": "esc", "del": "delete", "cmd": "win", "meta": "win",
             "windows": "win", "option": "alt", "pgup": "pageup", "pgdn": "pagedown"}
    parts = [alias.get(p, p) for p in parts]
    mods = [m for m in ("ctrl", "shift", "alt", "win") if m in parts]
    rest = [p for p in parts if p not in ("ctrl", "shift", "alt", "win")]
    return "+".join(mods + rest)


def window_problem(w: WindowInfo) -> str | None:
    if w.process.lower() in DENY_PROCESSES:
        return f"{w.process} is on the never-touch list"
    if w.cls in DENY_CLASSES:
        return f"{w.cls} windows are off limits"
    if DENY_TITLE.search(w.title or ""):
        return f"the window '{w.title[:40]}' looks sensitive (security, credentials, or payments)"
    return None


def decide(
    action: Proposed,
    window: WindowInfo,
    focused: UIElement | None,
    task_approved: bool = False,
    multiline: bool = False,
) -> Decision:
    """`multiline`: the caret is in a multi-line text body (a document), where Enter/Backspace just
    edit text; in single-line fields and elsewhere they can submit forms or delete files, so they ask."""
    problem = window_problem(window)
    if problem:
        return Decision(DENY, problem)
    if action.kind in ("wait", "done", "ask_user"):
        return Decision(ALLOW, "no input involved")

    always = False
    if action.kind == "hotkey":
        k = normalize_keys(action.keys)
        if k not in SAFE_SINGLE and k not in SAFE_COMBOS:
            return Decision(DENY, f"the key combination '{k}' isn't on the allowed list")
        always = k in ALWAYS_CONFIRM_KEYS and not (multiline and k in TEXT_EDIT_KEYS)
    elif action.kind == "type_text":
        if not action.text:
            return Decision(DENY, "nothing to type")
        if len(action.text) > MAX_TEXT:
            return Decision(DENY, f"text is longer than {MAX_TEXT} characters")
        if focused is not None and focused.is_password:
            return Decision(DENY, "I never type into password fields")
        if focused is not None and TERMINAL_HINT.search(f"{focused.name} {focused.automation_id}"):
            return Decision(DENY, "the focused element looks like a terminal")
        m = SHELL_TEXT.search(action.text)
        # A whole program pasted into a document/code editor is just text (it may mention .exe files etc.);
        # a one-liner that looks like a command is refused everywhere.
        if m and not (multiline and action.text.count("\n") >= 2):
            return Decision(DENY, f"the text contains something command-like ('{m.group(0).strip()[:20]}')")
        always = "\n" in action.text and not multiline  # in a single-line field a newline can submit a form
    elif action.kind in ("click", "double_click"):
        el = action.element
        if el is not None and TERMINAL_HINT.search(f"{el.name} {el.automation_id}"):
            return Decision(DENY, "that looks like it opens or focuses a terminal")
        if el is not None and DESTRUCTIVE.search(el.name):
            always = True
    elif action.kind == "scroll":
        if abs(action.amount) > 20:
            return Decision(DENY, "scroll amount too large")
    elif action.kind == "open_url":
        url = action.text.strip()
        if not re.match(r"^https?://", url, re.I):
            return Decision(DENY, "only http(s) web addresses can be opened")
        if DENY_TITLE.search(url):
            return Decision(DENY, "that address looks like banking/payments/credentials")
    elif action.kind == "switch_window":
        pass  # the new window is vetted by window_problem() before anything happens in it
    else:
        return Decision(DENY, f"unknown action '{action.kind}'")

    if always:
        return Decision(ASK, "this could submit, send, or delete something", always_ask=True)
    if task_approved:
        return Decision(ALLOW, "you approved this task")
    return Decision(ASK, "supervised mode: every step needs your OK")
