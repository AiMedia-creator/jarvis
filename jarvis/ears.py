"""Ohren: Mikrofon (sounddevice) + lokale Spracherkennung (faster-whisper). Kostenlos, ohne Cloud."""

from __future__ import annotations

import queue
import time

import numpy as np

from . import config

RATE = 16000
BLOCK = 1600  # 0,1 s
# Whisper "erfindet" bei Stille gern Saetze; die werfen wir weg.
HALLUCINATIONS = ("untertitel", "danke fürs zuschauen", "danke für's zuschauen", "vielen dank fürs zuschauen",
                  "copyright", "amara.org", "bis zum nächsten mal", "tschüss.", "musik")
# Fachbegriffe, die Whisper sonst gern verhoert ("Upwerk", "Hab Spot"). Erweiterbar: whisper_vokabular in der Konfig.
VOCAB = ("Jarvis, Upwork, Lead, Leads, Lead-Score, Proposal, Pipeline, Deal, CRM, HubSpot, Gmail, Google Drive, "
         "LinkedIn, Shopify, Klaviyo, ElevenLabs, Claude, Execute Mode, Advisor Mode, Cashflow, ROI, Retainer")


# Unter dieser mittleren Log-Wahrscheinlichkeit rät Whisper nur noch (gemessen: klare Sprache > -0,7; Geräusche,
# Echo-Reste und abgeschnittene Fetzen < -1,0).
MIN_AVG_LOGPROB = -1.0


def sprach_text(segments) -> str:
    """Fügt erkannte Segmente zusammen und lässt geratene weg: Geräusche und Echo-Reste werden sonst zu
    erfundenen Sätzen ("Und, ey, ey."), auf die Jarvis dann antwortet."""
    teile = []
    for segment in segments:
        sicher = getattr(segment, "avg_logprob", None)
        if sicher is not None and sicher < MIN_AVG_LOGPROB:
            continue
        teile.append(str(getattr(segment, "text", "")).strip())
    return clean_transcript(" ".join(t for t in teile if t))


def vocabulary_prompt() -> str:
    extra = str(config.get("whisper_vokabular", "") or "").strip()
    owner = str(config.get("owner_name", "") or "").strip()
    return f"{owner + ', ' if owner else ''}{VOCAB}{', ' + extra if extra else ''}."[:600]


def clean_transcript(text: str) -> str:
    """Verwirft typische Whisper-Erfindungen bei Stille und ein bloss nachgeplappertes Vokabular."""
    text = " ".join((text or "").split())
    low = text.lower()
    if not text or (any(h in low for h in HALLUCINATIONS) and len(text) < 45):
        return ""
    words = [w.strip(".,!?;:").lower() for w in text.split()]
    vocab = {w.strip(".,").lower() for w in vocabulary_prompt().replace(",", " ").split()}
    if len(words) >= 3 and all(w in vocab for w in words if w):  # Modell hat nur die Wortliste wiederholt
        return ""
    return text


class Ears:
    def __init__(self):
        self._models = {}

    def _whisper(self, size: str | None = None):
        from . import sprache

        size = size or sprache.whisper_standard()
        if size not in self._models:
            from faster_whisper import WhisperModel

            print(f"[ohr] lade Spracherkennung '{size}' (beim ersten Mal Download) ...")
            self._models[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return self._models[size]

    def record(self, max_seconds: float = 20.0, wait_seconds: float = 8.0) -> np.ndarray | None:
        """Wartet auf Sprache, nimmt auf, bis ca. 1 s Stille ist. None = nichts gesprochen."""
        import sounddevice as sd

        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(indata[:, 0].copy())

        chunks: list[np.ndarray] = []
        with sd.InputStream(samplerate=RATE, channels=1, blocksize=BLOCK, dtype="float32", callback=callback):
            ambient = [float(np.sqrt(np.mean(q.get() ** 2))) for _ in range(4)]
            threshold = max(float(config.get("mic_threshold", "0.02")), 3 * (sum(ambient) / len(ambient)))
            started = False
            silence = 0
            begin = time.monotonic()
            while True:
                block = q.get()
                level = float(np.sqrt(np.mean(block ** 2)))
                if not started:
                    if level > threshold:
                        started = True
                        chunks.append(block)
                    elif time.monotonic() - begin > wait_seconds:
                        return None
                    continue
                chunks.append(block)
                silence = silence + 1 if level < threshold else 0
                if silence >= 10 or len(chunks) * BLOCK / RATE > max_seconds:
                    break
        audio = np.concatenate(chunks)
        return audio if len(audio) / RATE > 0.4 else None

    def transcribe(self, audio: np.ndarray) -> str:
        from . import sprache
        code = sprache.whisper_sprache()
        standard = sprache.whisper_standard()
        if code is None:
            code = sprache.waehle_mehrsprachig(self._whisper(standard).detect_language(audio)[2])
        model = self._whisper(sprache.whisper_modell(standard, code))
        segments, _ = model.transcribe(audio, language=code, beam_size=5, vad_filter=True,
                                                 condition_on_previous_text=False,
                                                 initial_prompt=sprache.whisper_vorgabe(vocabulary_prompt(), code))
        return sprach_text(segments)

    def listen(self) -> str:
        audio = self.record()
        return self.transcribe(audio) if audio is not None else ""
