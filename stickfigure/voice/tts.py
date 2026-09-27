"""Text-to-speech with Kokoro (onnxruntime, CPU), spoken sentence by sentence as text streams in.

A worker thread synthesizes and plays queued sentences; `level` (0..1) follows the loudness of
what's playing so the figure's head can bob along. stop() cuts speech off immediately.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time

import numpy as np

from stickfigure.config import CONFIG, Config

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")


class SentenceSplitter:
    """Feed cumulative streamed text; get back newly completed sentences."""

    def __init__(self) -> None:
        self._consumed = 0

    def feed(self, full_text: str) -> list[str]:
        if len(full_text) < self._consumed:  # the text was cleaned/shortened upstream: restart
            self._consumed = 0
        pending = full_text[self._consumed:]
        out = []
        pos = 0
        for m in _SENTENCE_END.finditer(pending):
            sentence = pending[pos:m.start()].strip() + pending[m.start():m.end()].strip()
            if sentence.strip():
                out.append(sentence.strip())
            pos = m.end()
        self._consumed += pos
        return out

    def flush(self, full_text: str) -> list[str]:
        rest = full_text[self._consumed:].strip()
        self._consumed = len(full_text)
        return [rest] if rest else []


def speakable(text: str) -> str:
    """Strip things that sound bad read aloud."""
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_#`~]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, cfg: Config = CONFIG):
        self.cfg = cfg
        self.enabled = cfg.voice_enabled
        self.voice = cfg.tts_voice
        self.speed = cfg.tts_speed
        self.volume = cfg.tts_volume
        self.level = 0.0
        self._kokoro = None
        self._q: queue.Queue[tuple[int, str, str, float]] = queue.Queue()
        self._generation = 0  # bumped by stop(): queued/playing speech from older generations is dropped
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()
        self.last_first_audio_ms = 0.0

    # -- public -----------------------------------------------------------------------------

    def say(self, text: str, voice: str | None = None, speed: float | None = None, force: bool = False) -> None:
        """Queue a sentence. `voice`/`speed` override the defaults for this sentence (used for previews)."""
        if (self.enabled or force) and speakable(text):
            self._q.put((self._generation, speakable(text), voice or self.voice, speed or self.speed))

    def stream(self) -> "SpeechStream":
        """For streamed replies: stream.feed(cumulative_text) ... stream.finish(final_text)."""
        return SpeechStream(self)

    def stop(self) -> None:
        self._generation += 1
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self.level = 0.0

    @property
    def speaking(self) -> bool:
        return self.level > 0.01 or not self._q.empty()

    def warm_up(self) -> None:
        """Load the model (downloading it the first time) off the UI thread."""
        self._q.put((-1, "", self.voice, self.speed))

    # -- worker -------------------------------------------------------------------------------

    def _load(self):
        if self._kokoro is None:
            import os

            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            from stickfigure.voice.models import kokoro_files

            model, voices = kokoro_files()
            t0 = time.perf_counter()
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(2, (os.cpu_count() or 4) // 2)  # leave cores for physics/UI
            session = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
            self._kokoro = Kokoro.from_session(session, str(voices))
            self._kokoro.create("Ready.", voice=self.voice)  # first inference is slow; do it now
            log.info("Kokoro ready in %.1fs", time.perf_counter() - t0)
        return self._kokoro

    def synthesize(self, text: str, voice: str | None = None, speed: float | None = None) -> tuple[np.ndarray, int]:
        return self._load().create(text, voice=voice or self.voice, speed=speed or self.speed, lang="en-us")

    def _run(self) -> None:
        import sounddevice as sd

        while True:
            gen, text, voice, speed = self._q.get()
            try:
                if gen == -1:
                    self._load()
                    continue
                if gen != self._generation:
                    continue
                t0 = time.perf_counter()
                samples, sr = self.synthesize(text, voice, speed)
                if gen != self._generation:
                    continue
                self.last_first_audio_ms = (time.perf_counter() - t0) * 1000
                self._play(samples, sr, gen, sd)
            except Exception as e:  # voice must never take the app down
                log.warning("TTS failed: %s", e)
            finally:
                self.level = 0.0

    def _play(self, samples: np.ndarray, sr: int, gen: int, sd) -> None:
        samples = np.asarray(samples, dtype=np.float32) * self.volume
        block = int(sr * 0.03)
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as out:
            for i in range(0, len(samples), block):
                if gen != self._generation:
                    return
                chunk = samples[i:i + block]
                rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
                self.level = min(1.0, rms * 6)
                out.write(chunk.reshape(-1, 1))


class SpeechStream:
    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self.splitter = SentenceSplitter()

    def feed(self, full_text: str) -> None:
        for s in self.splitter.feed(full_text):
            self.speaker.say(s)

    def finish(self, full_text: str) -> None:
        for s in self.splitter.flush(full_text):
            self.speaker.say(s)
