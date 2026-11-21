"""Tiny numpy synth for sound effects and a beat bed (no samples, no licences)."""
import numpy as np

SR = 44100
_rng = np.random.default_rng(7)


def _spec(x, fn):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X * fn(f), len(x))


def _bump(f, c, w):
    return np.exp(-0.5 * ((f - c) / w) ** 2)


def _norm(x, g=1.0):
    m = np.abs(x).max()
    return x / m * g if m else x


def kick(dec=0.35):
    n = int(dec * SR); t = np.arange(n) / SR
    f = 45 + 110 * np.exp(-t * 30)
    return np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7) * 1.5)


def clap():
    n = int(0.25 * SR); t = np.arange(n) / SR
    s = _norm(_spec(_rng.standard_normal(n), lambda f: _bump(f, 1500, 700)))
    return s * np.exp(-t * 18) * 0.7


def hat():
    n = int(0.1 * SR); t = np.arange(n) / SR
    s = _norm(_spec(_rng.standard_normal(n), lambda f: np.clip((f - 7000) / 3000, 0, 1)))
    return s * np.exp(-t / 0.04) * 0.3


def bass(freq, length):
    n = int(length * SR); t = np.arange(n) / SR
    f = freq * (1 + 0.6 * np.exp(-t * 40))
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.2)
    s[-2000:] *= np.linspace(1, 0, 2000)
    return np.tanh(s * 2.2) * 0.6


def beat(length, bpm=150):
    """Trap-ish loop: kick/clap/hats + 808."""
    b = 60 / bpm
    out = np.zeros(int(length * SR) + SR)
    def put(sig, t, g=1):
        i = int(t * SR); j = min(len(out), i + len(sig)); out[i:j] += sig[: j - i] * g
    notes = [55, 55, 49, 52]
    bar = 0; t = 0.0
    while t < length:
        put(bass(notes[bar % 4], 4 * b * 0.95), t, 0.8)
        for k in range(4):
            put(kick(), t + k * b, 0.9 if k in (0, 2) else 0.5)
            if k in (1, 3): put(clap(), t + k * b, 0.7)
            put(hat(), t + k * b, 0.7); put(hat(), t + k * b + b / 2, 0.5)
        t += 4 * b; bar += 1
    return _norm(np.tanh(out[: int(length * SR)]), 0.9)


def crickets(length=2.0):
    n = int(length * SR); t = np.arange(n) / SR
    chirp = np.sin(2 * np.pi * 4300 * t) * (np.sin(2 * np.pi * 30 * t) > 0.3)
    gate = (np.sin(2 * np.pi * 1.6 * t) > 0.2).astype(float)
    return chirp * gate * 0.25


def whoosh(length=0.6):
    n = int(length * SR); t = np.arange(n) / SR
    s = _norm(_spec(_rng.standard_normal(n), lambda f: _bump(f, 1200, 900)))
    return s * np.sin(np.pi * t / length) ** 2 * 0.6


def boom(length=1.2):
    n = int(length * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * np.cumsum(38 + 80 * np.exp(-t * 12)) / SR) * np.exp(-t * 3)
    return np.tanh(s * 2) * 0.9


def scratch(length=0.35):
    n = int(length * SR); t = np.arange(n) / SR
    s = _norm(_spec(_rng.standard_normal(n), lambda f: _bump(f, 1800, 900)))
    return s * np.abs(np.sin(2 * np.pi * 9 * t)) * 0.5


def babble(seconds, seed=0, pitch=140):
    """Mock 'voice' for --mock runs: a hummed melody with syllable gating."""
    r = np.random.default_rng(seed)
    n = int(seconds * SR); t = np.arange(n) / SR
    f = pitch * (1 + 0.15 * np.sin(2 * np.pi * r.uniform(2, 4) * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = sum(np.sin(k * ph) / k for k in range(1, 8))
    syl = (np.sin(2 * np.pi * r.uniform(4, 6) * t) > -0.3).astype(float)
    s = _spec(s * syl, lambda fr: _bump(fr, 700, 300) + 0.5 * _bump(fr, 1300, 300))
    return _norm(s, 0.6)


SFX = {"crickets": crickets, "whoosh": whoosh, "boom": boom, "scratch": scratch}
