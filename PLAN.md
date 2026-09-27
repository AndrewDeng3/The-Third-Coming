# StickFigure: Desktop AI Buddy (Revised Plan)

A procedurally animated stick figure that lives on the Windows desktop. It walks on windows, reacts to physics, chats through a local LLM and, when asked, operates apps on the user's behalf under strict guardrails.

**Target hardware:** RTX 40-series (16 GB VRAM), 24-core i9, 32 GB RAM, Windows 11.
**Decisions locked:** PySide6 + Pymunk, procedural IK figure, fully local (Ollama).

---

## 0. Key Changes from v1 (and why)

| # | v1 said | Revised | Reason |
|---|---------|---------|--------|
| 1 | Tauri v2 *or* PySide6 | **PySide6 only** | Rust isn't installed; one language across physics, vision, LLM and audio removes an IPC layer. |
| 2 | Poll windows at 30 FPS | **`SetWinEventHook` events + 10 Hz reconcile poll** | Event-driven is near-zero CPU. Polling alone misses fast drags and wastes cycles. |
| 3 | `GetWindowRect` implied | **`DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS)`** | On Win10/11, `GetWindowRect` includes invisible ~7 px resize borders, so the figure would float beside and above windows. |
| 4 | Every window = static box | **Only the *visible* top-edge segments of windows are platforms** | Windows overlap. The figure must not stand on the part of a window that's hidden behind another one. |
| 5 | YOLOv8-nano for UI detection | **Windows UI Automation (UIA) first**, OCR second, VLM last | Stock YOLO isn't trained on UI. UIA gives exact element boxes, names, roles, and even "is password field", with no ML involved. |
| 6 | Llama 3.2 3B for tool calling | **Qwen2.5-7B-Instruct (or Qwen3-8B) for actions**, 3B for chit-chat | 3B models are unreliable at multi-step tool calls. A 7B/8B model at Q4 fits easily in 16 GB. |
| 7 | "Sub-100 ms responses" | **~100 ms to first token; ~300–800 ms per agent step** | Realistic budget. The UI has to hide this with animation ("thinking" pose). |
| 8 | Stream 60 FPS screen capture | **Capture on demand** (per agent step) | Continuous capture burns GPU/CPU for nothing when idle. |
| 9 | LangGraph | **Custom `asyncio` state machine** (via `qasync`) | The loop is small, and LangGraph adds dependencies and indirection without a real payoff here. |
| 10 | Kill switch `Ctrl+Shift+Esc` | **`Ctrl+Alt+Pause`** via `RegisterHotKey` | `Ctrl+Shift+Esc` is hard-wired to Task Manager. |
| 11 | Safety in Phase 4 | **Safety ships *with* the first action** | Input injection without guardrails must never exist, even in dev builds. |
| 12 | Command blacklist (`rm`, `del`) | **App/element deny-list + HWND lock + user-override** | String blacklists are trivially bypassed. Blocking terminals and password fields outright is much stronger. |
| 13 | ChromaDB | **SQLite + `sqlite-vec`** | One file, no server, fewer deps. Swap to ChromaDB only if scale demands it. |
| 14 | Whisper.cpp | **`faster-whisper` (CUDA) + Silero VAD** | Better Python integration, and GPU is available. |
| 15 | Buddy's walk gates the action | **Walk is cosmetic and runs in parallel** | Correctness must never depend on pathfinding or a staircase succeeding. |

---

## 1. Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Overlay | **PySide6** `QWidget` (frameless, translucent, always-on-top, tool window) | One overlay spanning the virtual desktop. Per-monitor DPI v2. |
| Click-through | Small per-pixel-alpha window that follows the figure | Alpha-0 pixels of a layered window pass clicks through natively; an invisible alpha-1 halo makes the body grabbable. Cheaper than recompositing a 4K overlay every frame, and no WS_EX_TRANSPARENT toggling needed. *(Changed during Phase 1.)* |
| Capture exclusion | `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` on the overlay | The buddy never sees itself in screenshots. |
| Physics | **Pymunk** (Chipmunk2D), fixed 120 Hz step | Figure = capsule character controller; ragdoll only when thrown or stunned. |
| Animation | Custom **2-bone analytic IK** + procedural gait | No sprites. Foot targets come from ground raycasts. |
| Window tracking | `pywin32` / `ctypes`: `EnumWindows`, `SetWinEventHook`, DWM attributes | Filter cloaked, minimized, tool and zero-size windows plus our own overlay. |
| UI perception | **`uiautomation`** (UIA) → **Windows.Media.Ocr** / RapidOCR → **Qwen2.5-VL-7B** crops | Cheapest path first. |
| Capture | **`mss`** (or `dxcam` for DXGI speed) | On demand, cropped to the target window. |
| LLM | **Ollama**: `qwen2.5:7b-instruct` (actions), `llama3.2:3b` (chat/personality) | JSON-schema structured outputs (`format=`). `keep_alive` tuned to hold one model hot. |
| Orchestration | `asyncio` + **`qasync`** | Qt and asyncio share one event loop. |
| Input | **`SendInput`** via `ctypes` (or `pynput`) | Unicode typing. `LLKHF_INJECTED` detection to tell user from bot. |
| TTS | **Kokoro-82M** (fallback Piper) | Streamed by sentence. |
| STT | **faster-whisper** `small.en`/`distil` (CUDA) + **Silero VAD** | Push-to-talk first, wake-word later. |
| Memory | **SQLite + sqlite-vec**, embeddings via `nomic-embed-text` on Ollama | Rolling short-term buffer + long-term vector recall. |
| Config/logging | `pydantic-settings`, `structlog` | Every synthetic input is logged. |

### Resource budget (steady state, idle / active)

| Component | VRAM | RAM | CPU |
|-----------|------|-----|-----|
| Overlay + physics + render | ~50 MB | ~150 MB | <3% / ~5% |
| Qwen2.5-7B Q4_K_M | ~5.5 GB | – | – |
| Qwen2.5-VL-7B (loaded on demand) | ~6 GB | – | – |
| Llama 3.2 3B | ~2.5 GB | – | – |
| faster-whisper small (CUDA) | ~1 GB | ~300 MB | – |
| Kokoro | ~0.3 GB (GPU) | ~300 MB | – |
| **Total worst case** | **~15 GB** | **<2 GB** | – |

> If VRAM gets tight: don't keep the VL model resident. Load it only when UIA and OCR both fail.

---

## 2. Architecture

```
┌──────────────────────────── Qt main thread (qasync loop) ────────────────────────────┐
│                                                                                      │
│  Overlay Renderer ◄── Figure (IK pose) ◄── Physics World (Pymunk 120 Hz)             │
│        ▲                                      ▲                                      │
│        │ hit-test → toggle click-through      │ platforms (visible top edges)        │
│        │                                      │                                      │
│  Chat Window (Qt)            Window Tracker ──┘  (WinEvent hook + 10 Hz reconcile)   │
│        │                                                                             │
│        ▼                                                                             │
│  ┌──────────────────── Agent Loop (asyncio state machine) ─────────────────────┐     │
│  │ IDLE → LISTEN → THINK → (CHAT | PLAN) → PERCEIVE → PROPOSE → GUARD → ACT ↺  │     │
│  │   Memory recall · Emotion state · Tool registry · Step budget               │     │
│  └──────┬───────────────┬──────────────────┬──────────────────┬────────────────┘     │
│         │               │                  │                  │                      │
└─────────┼───────────────┼──────────────────┼──────────────────┼──────────────────────┘
          ▼               ▼                  ▼                  ▼
   Ollama (HTTP)    Perception worker   Safety Guard      Input Executor
   chat / action    UIA → OCR → VLM     (pure function)   SendInput, rate-limited
   models           mss crops           allow / deny /    HWND re-check before
                                        ask-user          every event
          ▲                                                     │
          └──────── Audio worker (STT/TTS threads) ◄────────────┘ (status → speech)
```

**Threading rules**
- Physics and render run on the Qt thread with a fixed-step accumulator. They never block on anything.
- Blocking work (UIA queries, OCR, capture, STT, TTS) goes to a `ThreadPoolExecutor`, wrapped with `asyncio.to_thread`.
- The input executor gets a **dedicated thread** so the kill switch can interrupt it mid-sequence.

---

## 3. Subsystems

### A. Overlay & Physics Surface

**Window tracker**
- Enumerate top-level windows in Z-order. Get bounds via `DWMWA_EXTENDED_FRAME_BOUNDS`. Skip windows that are `DWMWA_CLOAKED` (other virtual desktops or suspended UWP), minimized, `WS_EX_TOOLWINDOW`, or the overlay itself.
- `SetWinEventHook` for `EVENT_OBJECT_LOCATIONCHANGE`, `EVENT_SYSTEM_FOREGROUND`, `EVENT_OBJECT_SHOW/HIDE`, `EVENT_SYSTEM_MINIMIZESTART/END`, then a 10 Hz reconcile poll as a safety net.
- **Visible-edge computation:** for each window's top edge, subtract the horizontal spans covered by windows above it in Z-order. The remaining segments become Pymunk `Segment` shapes (one-way platforms). The taskbar and the screen bottom are always solid.
- Fullscreen apps (games, video): hide the overlay entirely (`SHQueryUserNotificationState` or a bounds == monitor check).

**Platform dynamics**
- A window moving under the figure is a *kinematic* body. Carry the figure along with the platform velocity so it doesn't slide off during drags.
- When the window under the figure closes or minimizes, the figure falls, with a "surprised" reaction.

**Click-through**
- Default: the overlay is fully click-through.
- 60 Hz cursor poll: if the cursor is inside the figure hit-capsule (inflated ~8 px) or a placed block, clear `WS_EX_TRANSPARENT`. Restore it on exit.
- Drag = grab (the figure becomes a ragdoll). Release with velocity = throw.

**Figure body (procedural)**
- Controller: capsule collider, ground-probe raycasts, coyote time, and a jump arc solver (can I reach platform P?).
- Skeleton: pelvis → spine → head; 2 × (hip → knee → foot); 2 × (shoulder → elbow → hand). Drawn as anti-aliased strokes with a circle head.
- **Gait:** foot targets step when displaced by more than the stride threshold, alternate legs, and follow a step arc. The pelvis bobs with the stride phase. Solved with analytic 2-bone IK with knee-direction hints.
- **Ragdoll mode:** swap to Pymunk bodies joined with `PivotJoint` + `RotaryLimitJoint`. Blend back to the controller when it settles.
- Pose states: `Idle`, `Walk`, `Run`, `Jump`, `Fall`, `Land`, `Climb`, `Sit` (on a window edge, legs dangling), `Think`, `Talk`, `Point`, `Type`, `Grabbed`, `Ragdoll`. Cross-fade transitions (~150 ms).

**Blocks / staircase**
- Blocks are cosmetic physics bodies the figure can spawn to reach unreachable platforms. They're capped (e.g. 8) and despawn on a timer.
- Pathfinding: build a graph of platform segments with jump/drop edges. If there's no path, plan a block staircase. **None of this gates agent actions.**

### B. Perception

Tiered and cheapest first. Stop at the first tier that answers the question.

1. **UIA tree** of the target window (`uiautomation`): elements with `Name`, `ControlType`, `BoundingRectangle`, `IsPassword`, `IsEnabled`. Prune to on-screen, interactable elements and number them. Serialize compactly (`[12] Button "Share" (0.81,0.05)`).
2. **OCR** (`Windows.Media.Ocr` via `winsdk`, or RapidOCR) on an `mss` crop when UIA is sparse. Canvas apps (Google Docs is canvas-heavy) often are.
3. **VLM** (`qwen2.5vl:7b`): only a crop of the relevant region plus the question. Never full 4K frames.

Coordinates are normalized to the **target window** (0–1), not the whole screen. They're converted back at execution time using the *current* window bounds.

### C. Cognitive Core

- **Two models:** the chat/personality model is always hot. The action model loads when a task starts.
- **Structured output:** every action turn must match a JSON schema, for example:
  ```json
  {"thought": "...", "action": "click", "target": {"element_id": 12}, "say": "Opening the share menu!"}
  ```
  Actions: `click`, `double_click`, `type_text`, `hotkey`, `scroll`, `focus_window`, `wait`, `ask_user`, `done`. Target by **element id** when possible and by normalized coordinates only as a fallback.
- **Step budget:** max N steps per task (default 15). The loop also aborts on 3 consecutive failed or blocked actions.
- **Personality:** the system prompt includes the emotion vector and recent memories. The `say` field feeds TTS and speech bubbles.

### D. Action Execution

- `SendInput` with `KEYEVENTF_UNICODE` for text. Long text goes through clipboard paste, with the user's clipboard saved and restored.
- Mouse moves follow Bézier paths at human-like speed. Typing is capped at ~80 WPM (configurable).
- **Before every input event:** re-check that the foreground HWND equals the locked target HWND and that the point is inside its *current* bounds. On mismatch, abort the step.
- The figure walks or points to the target *in parallel*, purely as a visual cue.

### E. Memory & Emotion

- Short-term: the last ~20 turns in RAM.
- Long-term: summarized episodes and facts in SQLite + `sqlite-vec`, top-k recall per turn.
- Emotion vector `[energy, curiosity, affection, annoyance]`. It decays toward baseline and gets bumped by events (petting → affection, being thrown → annoyance, idle → energy drops → sits or sleeps). It drives idle behavior selection and tone.

### F. Audio

- STT: push-to-talk hotkey → Silero VAD → faster-whisper.
- TTS: stream sentence by sentence from the LLM output, with the mouth/head bob synced to audio amplitude.

---

## 4. Safety Guardrails

These are the only thing between an LLM hallucination and the user's data, so they're non-negotiable from the first line of input code.

1. **Global kill switch.** `Ctrl+Alt+Pause` via `RegisterHotKey` (handled by the OS, so it works even when the agent is busy). It cancels the agent task, flushes the input queue, releases any held keys and buttons, and unloads the action model. A red ✕ on the figure also works.
2. **User always wins.** A low-level mouse/keyboard hook watches for *non-injected* input (`LLMHF_INJECTED` flag unset). Any physical user input during a task pauses the agent immediately.
3. **Target lock by HWND + process.** The task is bound to one window (HWND + exe name). Inputs outside its current bounds, or while another window is foreground, are dropped.
4. **App deny-list (hard block):** `cmd.exe`, `powershell.exe`, `pwsh.exe`, `WindowsTerminal.exe`, `regedit.exe`, `taskmgr.exe`, `mmc.exe`, the Run dialog, UAC/credential prompts, password managers, banking apps (configurable). Elevated windows are blocked by UIPI anyway.
5. **Element deny-list:** never type into UIA elements with `IsPassword=true`. Never click elements named like *Delete / Remove / Send / Pay / Purchase / Submit / Publish* without explicit `ask_user` confirmation.
6. **Hotkey filter:** block `Win+R`, `Win+X`, `Alt+F4` (unless on the target), `Ctrl+Alt+Del`, `Ctrl+Shift+Esc`, and `Win+L`.
7. **Confirm modes:** `supervised` (every action previewed with a ghost cursor and needs approval; this is the default), `trusted-app` (auto-approve inside an allow-listed app), `off` (dev only, loud warning).
8. **Rate limits:** ≤ 80 WPM, ≤ 3 clicks/sec, max steps per task.
9. **Audit log:** every proposed and executed action is logged with a timestamp, target, guard decision and screenshot hash.

---

## 5. Roadmap (each phase ends with a runnable demo)

**Phase 1: Overlay + Physics Spike** *(riskiest part, do first)*: **built 2026-09-27**, run with `.venv\Scripts\python -m stickfigure`
- Transparent, capture-excluded, per-monitor-DPI overlay across all monitors.
- Window tracker with visible-top-edge platforms and kinematic carry.
- Dynamic click-through. Placeholder capsule you can drag and throw.
- ✅ *Exit:* capsule lands on a Chrome window, rides along when it's dragged, falls when it's minimized, and never blocks clicks elsewhere. CPU < 3% idle.

**Phase 2: Procedural Figure**: **built 2026-09-27** (`tools/pose_sheet.py` renders every pose for review)
- IK skeleton, gait, jump solver, ragdoll blend, pose state machine.
- Autonomous idle wandering: walk, sit on window edges, jump between windows.
- Platform graph pathfinding plus the block staircase.
- ✅ *Exit:* the figure convincingly wanders a busy desktop for 10 minutes without getting stuck or clipping.

**Phase 3: Brain (chat only, no actions)**: **built 2026-09-27** (`tools/live_chat_check.py` verifies against local Ollama)
- Chat window plus speech bubbles. Ollama streaming via the `qasync` agent loop.
- Emotion vector plus memory (SQLite + sqlite-vec).
- ✅ *Exit:* you can hold a conversation, it remembers facts across restarts, and mood visibly changes behavior.

**Phase 4: Perception (read-only)**: **built 2026-09-27** (`tools/perception_eval.py`: 100% exact / 96% natural-language on Chrome, Explorer, Claude app)
- UIA tree extraction → OCR → VLM fallback, capture-on-demand.
- "What's on my screen?" / "Where's the share button?" → the figure walks over and *points*.
- ✅ *Exit:* correctly locates named elements in Chrome, VS Code and Explorer at ≥ 90% accuracy.

**Phase 5: Actions + Safety (shipped together)**: **built 2026-09-27**; typing into Google Docs confirmed by user. Changed per user: no per-step approval (risky clicks still confirm), Esc cancels, follow-ups/continuations, harmless idle 'mischief' (move/scroll only)
- Kill switch, user-override hook, HWND lock, deny-lists, supervised mode, audit log *first*.
- Then `SendInput` executor, structured action schema, step budget.
- ✅ *Exit:* completes "type this paragraph into my Google Doc" in supervised mode. Kill switch halts within 50 ms. It cannot be coerced into a terminal.

**Phase 6: Voice + Polish**: **built 2026-09-27** (Kokoro fp32 CPU ~0.2x real-time, Whisper base.en ~0.35 s; settings window; start-with-Windows; `packaging\build.ps1` -> dist\StickFigure\StickFigure.exe)
- Push-to-talk STT, streaming TTS, lip-bob sync.
- Latency tuning, settings UI, tray icon, autostart, packaging (PyInstaller / Nuitka).

---

## 6. Project Layout

```
stickfigure/
├── app.py                  # entry: QApplication + qasync loop
├── config.py
├── overlay/                # window, renderer, click-through, DPI
├── world/                  # pymunk space, platforms, blocks, pathfinding
├── figure/                 # skeleton, ik, gait, pose FSM, ragdoll
├── win/                    # window tracker, win event hooks, dwm helpers
├── perception/             # uia, ocr, vlm, capture
├── agent/                  # loop, tools, prompts, schemas, emotion
├── memory/                 # sqlite store, embeddings
├── actions/                # executor, input driver
├── safety/                 # guard, kill switch, user-override hook, audit
├── audio/                  # stt, tts
└── ui/                     # chat window, bubbles, tray, settings
tests/
```

---

## 7. Known Risks

| Risk | Mitigation |
|------|-----------|
| Mixed-DPI multi-monitor coordinate bugs | Per-monitor DPI v2 from day one. All internal math in physical pixels, converted at the Qt boundary. |
| Anti-cheat games flag injected input or overlays | Auto-hide in fullscreen. Never inject into processes on a game deny-list. |
| Canvas apps (Google Docs, Figma) expose little UIA | OCR/VLM tiers. For Docs, prefer keyboard navigation and paste over precise clicks. |
| 7B model loops or hallucinates element ids | Validate the id exists before acting. Step budget. Re-perceive after every action. |
| Python GIL stutter during heavy work | All heavy work off the Qt thread. Physics step is tiny. Profile early. |
| VRAM contention with the user's own GPU apps | Lazy-load models, short `keep_alive`, "low-VRAM mode" (3B only, no VLM). |
