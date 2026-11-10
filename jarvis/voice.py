"""Jarvis' Stimme: Saetze werden synthetisiert und nacheinander abgespielt (macOS: afplay)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading

from . import tts


class Voice:
    def __init__(self, muted: bool = False):
        self.muted = muted
        self._texts: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._generation = 0
        self.stopped = False
        self._proc: subprocess.Popen | None = None
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def say(self, text: str) -> None:
        print(f"JARVIS > {text}", flush=True)
        if not self.muted and not self.stopped:
            self._texts.put((self._generation, text))

    def stop(self) -> None:
        self.stopped = True
        self._generation += 1
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def resume(self) -> None:
        self.stopped = False

    def wait(self) -> None:
        self._texts.join()
        self._audio.join()

    def _synth_loop(self) -> None:
        while True:
            gen, text = self._texts.get()
            try:
                audio = tts.synthesize(text) if gen == self._generation else None
            except Exception as exc:
                print(f"[stimme] Synthese fehlgeschlagen: {exc}")
                audio = None
            self._audio.put((gen, audio))
            self._texts.task_done()

    def _play_loop(self) -> None:
        while True:
            gen, audio = self._audio.get()
            try:
                if audio and gen == self._generation:
                    self._play(audio)
            except Exception as exc:
                print(f"[stimme] Wiedergabe-Fehler: {exc}")
            finally:
                self._audio.task_done()

    def _play(self, audio: bytes) -> None:
        fd, path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            if sys.platform == "darwin":
                self._proc = subprocess.Popen(["afplay", path])
                self._proc.wait()
            else:
                import pygame

                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy() and not self.stopped:
                    pygame.time.wait(30)
                pygame.mixer.music.unload()
        finally:
            os.remove(path)
