"""`jarvis bench` : mesure chaque étage sans micro et estime la latence perçue."""
from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from datetime import date

import numpy as np
import soxr

from . import app, hardware, prompts
from .audio import SAMPLE_RATE
from .config import Config
from .llm import load_llm
from .llm.base import Delta, Done
from .stt import clean_transcript, load_stt
from .text.chunker import SpeechChunker

STT_PHRASES = [
    "Quelle heure est-il ?",
    "Allume la lumière du salon et mets une ambiance calme.",
    "Rappelle-moi d'appeler le garage demain à neuf heures.",
]
TTS_PHRASES = ["D'accord, j'allume la lumière du salon.", "Il fait dix-huit degrés et le ciel est dégagé."]
LLM_PROMPTS = [
    "Salut Jarvis, ça va ?",
    "Donne-moi une idée de dîner rapide pour ce soir.",
    "Pourquoi le ciel est bleu ?",
]


def _best_of(fn: Callable[[], object], repeats: int) -> tuple[float, object]:
    best, result = float("inf"), None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


def run(cfg: Config, repeats: int = 3) -> int:
    print(f"Matériel : {hardware.detect()}")
    print(f"Modèles  : STT {cfg.stt.backend} {cfg.stt.model} ({cfg.stt.device}) | "
          f"LLM {cfg.llm.model} | voix {cfg.tts.voice}\n")

    start = time.perf_counter()
    tts = app.build_tts(cfg)
    tts.warmup()
    print(f"[voix] chargement {_ms(time.perf_counter() - start)}")
    tts_first = [_best_of(lambda p=p: next(iter(tts.synthesize(p))), repeats)[0] for p in TTS_PHRASES]
    tts_ms = statistics.median(tts_first)
    print(f"[voix] premier son d'une phrase : {_ms(tts_ms)}")

    samples = [(p, soxr.resample(np.concatenate(list(tts.synthesize(p))), tts.sample_rate, SAMPLE_RATE))
               for p in STT_PHRASES]
    start = time.perf_counter()
    stt = load_stt(cfg.stt)
    stt.warmup()
    print(f"\n[STT]  chargement + chauffe {_ms(time.perf_counter() - start)}")
    stt_times = []
    for _, audio in samples:
        best, text = _best_of(lambda a=audio: stt.transcribe(a), repeats)
        stt_times.append(best)
        print(f"[STT]  {len(audio) / SAMPLE_RATE:.1f} s d'audio → {_ms(best)}  {clean_transcript(str(text))!r}")
    stt_ms = statistics.median(stt_times)

    llm = load_llm(cfg.llm)
    llm.check()
    system = prompts.system_prompt(cfg.user_name, date.today())
    start = time.perf_counter()
    llm.warmup(system)
    print(f"\n[LLM]  chargement + prompt système {_ms(time.perf_counter() - start)}")
    first_chunks = []
    for prompt in LLM_PROMPTS:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        chunker = SpeechChunker()
        first_token = first_chunk = None
        done = Done()
        text = []
        start = time.perf_counter()
        for event in llm.stream(messages):
            if isinstance(event, Delta):
                now = time.perf_counter() - start
                first_token = now if first_token is None else first_token
                text.append(event.text)
                if chunker.feed(event.text) and first_chunk is None:
                    first_chunk = now
            elif isinstance(event, Done):
                done = event
        total = time.perf_counter() - start
        first_chunk = first_chunk if first_chunk is not None else total
        first_chunks.append(first_chunk)
        print(f"[LLM]  1er token {_ms(first_token or total)} | 1re phrase {_ms(first_chunk)} | "
              f"total {_ms(total)} | {done.tokens_per_s:.0f} tok/s | "
              f"prompt {done.prompt_tokens} tok en {done.prompt_ms:.0f} ms")
        print(f"       {''.join(text).strip()!r}")
    chunk_ms = statistics.median(first_chunks)

    end_silence = cfg.vad.end_silence_ms / 1000
    print("\nLatence perçue estimée (fin de ta phrase → première syllabe) :")
    print(f"  question au LLM : silence {_ms(end_silence)} + STT {_ms(stt_ms)} + 1re phrase {_ms(chunk_ms)}"
          f" + voix {_ms(tts_ms)} = {_ms(end_silence + stt_ms + chunk_ms + tts_ms)}")
    print(f"  réflexe (heure, date) : {_ms(end_silence + stt_ms + tts_ms)}")
    return 0
