"""Push-to-talk speech recognition: record from the mic until the user stops talking, then transcribe
with faster-whisper (CPU int8 by default).

Endpointing is energy-based with an adaptive noise floor: speech starts after a few loud frames,
and the utterance ends after `listen_silence` seconds of quiet (or when stop() is called).
The mic is only open while listening.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from stickfigure.config import CONFIG, Config

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 480  # 30 ms
NO_SPEECH_TIMEOUT = 6.0


class Endpointer:
    """Decides when an utterance has started and ended, from per-frame loudness."""

    def __init__(self, silence: float, frame_s: float = FRAME / RATE):
        self.frame_s = frame_s
        self.silence_frames = int(silence / frame_s)
        self.floor = None
        self.speech = False
        self._loud_run = 0
        self._quiet_run = 0

    def push(self, rms: float) -> str:
        """Returns "wait", "speech" (utterance in progress), or "end"."""
        if self.floor is None:
            self.floor = rms
        threshold = max(0.012, self.floor * 3.0)
        loud = rms > threshold
        if not loud:  # track the background level only while it's quiet
            self.floor = 0.95 * self.floor + 0.05 * rms
        if not self.speech:
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= 3:
                self.speech = True
            return "speech" if self.speech else "wait"
        self._quiet_run = 0 if loud else self._quiet_run + 1
        return "end" if self._quiet_run >= self.silence_frames else "speech"


class Listener:
    def __init__(self, cfg: Config = CONFIG):
        self.cfg = cfg
        self._model = None
        self._model_lock = threading.Lock()
        self.listening = False
        self.level = 0.0
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self.last_transcribe_ms = 0.0

    # -- model ------------------------------------------------------------------------------

    def _load(self):
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                from stickfigure.voice.models import models_dir

                t0 = time.perf_counter()
                compute = "int8" if self.cfg.stt_device == "cpu" else "float16"
                self._model = WhisperModel(self.cfg.stt_model, device=self.cfg.stt_device, compute_type=compute,
                                           download_root=str(models_dir() / "whisper"))
                log.info("Whisper %s ready in %.1fs", self.cfg.stt_model, time.perf_counter() - t0)
            return self._model

    def warm_up(self) -> None:
        threading.Thread(target=self._load, name="stt-load", daemon=True).start()

    def transcribe(self, audio: np.ndarray) -> str:
        """16 kHz mono float32 -> text."""
        t0 = time.perf_counter()
        segments, _ = self._load().transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                             condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        self.last_transcribe_ms = (time.perf_counter() - t0) * 1000
        return text

    # -- listening ----------------------------------------------------------------------------

    def start(self, on_text: Callable[[str], None], on_state: Callable[[str], None] = lambda s: None) -> None:
        """Begin listening. Callbacks run on a worker thread: marshal to the UI yourself."""
        if self.listening:
            return
        self._stop.clear()
        self._cancel.clear()
        self.listening = True
        threading.Thread(target=self._run, args=(on_text, on_state), name="stt", daemon=True).start()

    def stop(self) -> None:
        """Finish now and transcribe what was heard."""
        self._stop.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._stop.set()

    def _run(self, on_text, on_state) -> None:
        import sounddevice as sd

        frames: queue.Queue[np.ndarray] = queue.Queue()
        audio: list[np.ndarray] = []
        ep = Endpointer(self.cfg.listen_silence)
        started = time.monotonic()
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                callback=lambda data, n, t, status: frames.put(data[:, 0].copy())):
                on_state("listening")
                while not self._stop.is_set():
                    try:
                        chunk = frames.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    audio.append(chunk)
                    rms = float(np.sqrt(np.mean(chunk * chunk)))
                    self.level = min(1.0, rms * 8)
                    state = ep.push(rms)
                    elapsed = time.monotonic() - started
                    if state == "end" or elapsed > self.cfg.listen_max:
                        break
                    if not ep.speech and elapsed > NO_SPEECH_TIMEOUT:
                        self._cancel.set()
                        break
        except Exception as e:
            log.warning("microphone error: %s", e)
            self.listening = False
            self.level = 0.0
            on_state("error")
            return
        self.listening = False
        self.level = 0.0
        if self._cancel.is_set() or not audio:
            on_state("cancelled")
            return
        on_state("thinking")
        try:
            text = self.transcribe(np.concatenate(audio))
        except Exception as e:
            log.warning("transcription failed: %s", e)
            text = ""
        on_text(text)
