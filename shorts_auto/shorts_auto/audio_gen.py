"""배경음악·효과음 자동 생성 (numpy 합성, 저작권 걱정 없음).

1) 클립마다 분위기(신남/슬픔/놀람/잔잔)를 자막·파일명 단어와 영상의 움직임·밝기로 판단
2) 분위기 구간에 맞춰 BGM을 이어 붙여 생성 (구간 사이는 크로스페이드)
3) 반응 시점에 분위기에 맞는 효과음을 고르고, 없으면 합성해 둔다
"""
import wave
from pathlib import Path

import numpy as np

SR = 48000

MOOD_KO = {"happy": "신남", "sad": "슬픔", "surprise": "놀람", "calm": "잔잔"}
KEYWORDS = {
    "sad": ["슬", "한숨", "눈물", "우울", "텅", "사라", "카드값", "빈털", "허탈", "좌절", "지침", "피곤",
            "망", "외로", "혼자", "삼각김밥", "월세", "청구"],
    "happy": ["신나", "신난", "댄스", "춤", "기쁨", "기뻐", "월급", "치킨", "맛있", "행복", "웃", "축하",
              "할인", "최고", "득템", "만족", "입금"],
    "surprise": ["놀람", "놀라", "헉", "갑자기", "알림", "충격", "뭐", "결제", "?!", "어라", "설마"],
}
REACTION_SFX = {"happy": "sparkle", "sad": "sadtrombone", "surprise": "boing", "calm": "pop"}
DING_WORDS = ["알림", "결제", "입금", "문자", "메시지", "카톡", "청구"]


# ---------------------------------------------------------------- 분위기 판단
def detect_mood(text, motion_level, brightness):
    """(mood, 이유)."""
    scores = {m: sum(text.count(w) for w in ws) for m, ws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        hits = [w for w in KEYWORDS[best] if w in text]
        return best, f"단어 '{', '.join(hits[:3])}' → {MOOD_KO[best]}"
    if motion_level > 5:
        return "happy", f"움직임이 큼({motion_level:.1f}) → {MOOD_KO['happy']}"
    if motion_level < 1.5 and brightness < 80:
        return "sad", f"움직임 적고 어두움({brightness:.0f}) → {MOOD_KO['sad']}"
    return "calm", f"뚜렷한 단서 없음 → {MOOD_KO['calm']}"


def wants_ding(text):
    return any(w in text for w in DING_WORDS)


# ---------------------------------------------------------------- 합성 기본 요소
def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def pluck(f, dur, decay=6.0):
    t = _t(dur)
    w = np.sin(2 * np.pi * f * t) + 0.5 * np.sin(4 * np.pi * f * t) + 0.2 * np.sin(6 * np.pi * f * t)
    return w * np.exp(-t * decay) * np.minimum(1, t * 400)


def pad(f, dur):
    t = _t(dur)
    w = sum(np.sin(2 * np.pi * f * d * t) for d in (0.997, 1.0, 1.003)) / 3
    w += 0.25 * np.sin(4 * np.pi * f * t)
    env = np.minimum(1, t / 0.4) * np.minimum(1, (dur - t) / 0.6)
    return w * np.clip(env, 0, 1)


def bass(f, dur):
    t = _t(dur)
    return (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * np.exp(-t * 4) * np.minimum(1, t * 300)


def kick(dur=0.25):
    t = _t(dur)
    f = 50 + 70 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14)


def hat(dur=0.06, rng=np.random.default_rng(1)):
    n = rng.standard_normal(int(dur * SR))
    n = np.diff(n, prepend=0)
    return n * np.exp(-_t(dur) * 60) * 0.3


def _add(buf, sig, at, gain=1.0):
    i = int(at * SR)
    if i >= len(buf):
        return
    sig = sig[: len(buf) - i]
    buf[i:i + len(sig)] += sig * gain


def triad(root, minor=False):
    return [root, root + (3 if minor else 4), root + 7]


# ---------------------------------------------------------------- 분위기별 음악
def render_mood(mood, dur):
    buf = np.zeros(int(dur * SR) + SR)
    if mood == "happy":          # C장조 I-V-vi-IV, 120bpm, 킥·하이햇·베이스·아르페지오
        bpm, prog = 120, [(60, 0), (67, 0), (69, 1), (65, 0)]
        beat = 60 / bpm
        n_beats = int(dur / beat) + 1
        for b in range(n_beats):
            root, mi = prog[(b // 4) % 4]
            at = b * beat
            _add(buf, bass(hz(root - 24), beat * 0.9), at, 0.55)
            if b % 2 == 0:
                _add(buf, kick(), at, 0.7)
            for h in (0, 0.5):
                _add(buf, hat(), at + h * beat, 0.3)
            notes = triad(root, mi)
            for k, h in enumerate((0, 0.5)):
                _add(buf, pluck(hz(notes[(b * 2 + k) % 3] + 12), beat), at + h * beat, 0.22)
    elif mood == "sad":          # A단조 i-VI-III-VII, 느린 패드 + 피아노풍 단음
        bpm, prog = 64, [(57, 1), (53, 0), (48, 0), (55, 0)]
        beat = 60 / bpm
        bar = beat * 4
        for c in range(int(dur / bar) + 1):
            root, mi = prog[c % 4]
            for n in triad(root, mi):
                _add(buf, pad(hz(n), bar + 0.6), c * bar, 0.16)
            for k in range(4):
                _add(buf, pluck(hz(triad(root, mi)[k % 3] + 12), beat * 1.6, decay=2.5),
                     c * bar + k * beat, 0.18)
    elif mood == "surprise":     # D단조 긴장감: 8분음표 펄스 + 반음 충돌
        bpm = 110
        e = 60 / bpm / 2
        for k in range(int(dur / e) + 1):
            root = 50 if (k // 8) % 2 == 0 else 51
            _add(buf, pluck(hz(root - 12), e * 0.9, decay=12), k * e, 0.5)
            _add(buf, pluck(hz(root + 12), e * 0.6, decay=18), k * e, 0.12)
            if k % 4 == 0:
                _add(buf, kick(), k * e, 0.5)
            _add(buf, hat(), k * e, 0.2)
    else:                         # 잔잔: F장조 패드 + 드문 단음
        bpm, prog = 80, [(65, 0), (60, 0), (62, 1), (58, 0)]
        bar = 60 / bpm * 4
        for c in range(int(dur / bar) + 1):
            root, mi = prog[c % 4]
            for n in triad(root, mi):
                _add(buf, pad(hz(n), bar + 0.6), c * bar, 0.14)
            _add(buf, pluck(hz(root + 12), bar / 2, decay=3), c * bar + bar / 4, 0.15)
    return buf[: int(dur * SR)]


def _normalize(x, rms_db=-20.0, peak=0.89):
    rms = np.sqrt(np.mean(x ** 2)) + 1e-9
    x = x * (10 ** (rms_db / 20) / rms)
    return np.clip(x, -peak, peak)


def write_wav(path, mono_or_stereo):
    x = mono_or_stereo
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def build_bgm(sections, total, out: Path, fade=0.5):
    """sections: [(start, mood)] (시간순). 분위기가 바뀌는 곳은 크로스페이드."""
    merged = []
    for st, m in sections:
        if not merged or merged[-1][1] != m:
            merged.append((st, m))
    master = np.zeros(int((total + 1) * SR))
    for i, (st, m) in enumerate(merged):
        en = merged[i + 1][0] if i + 1 < len(merged) else total
        a, b = max(0.0, st - fade / 2), min(total, en + fade / 2)
        seg = _normalize(render_mood(m, b - a))
        n_f = int(fade * SR)
        env = np.ones(len(seg))
        if i > 0:
            env[:n_f] = np.linspace(0, 1, min(n_f, len(seg)))[: len(env[:n_f])]
        if i + 1 < len(merged):
            env[-n_f:] = np.linspace(1, 0, min(n_f, len(seg)))[-len(env[-n_f:]):]
        _add(master, seg * env, a)
    master = master[: int(total * SR)]
    t = _t(total)
    master *= np.minimum(1, t / 0.4) * np.clip((total - t) / 1.2, 0, 1)
    # 약간의 스테레오감: 좌우를 아주 짧게 어긋나게
    right = np.concatenate([np.zeros(240), master[:-240]])
    write_wav(out, np.stack([master, right], axis=1) * 0.9)
    return merged


# ---------------------------------------------------------------- 효과음
def _sfx(kind):
    if kind == "whoosh":
        n = np.random.default_rng(2).standard_normal(int(0.4 * SR))
        t = _t(0.4)
        sweep = np.convolve(n, np.ones(6) / 6, mode="same") - np.convolve(n, np.ones(60) / 60, mode="same")
        return sweep * np.sin(np.pi * t / 0.4) ** 2 * 1.4
    if kind == "pop":
        t = _t(0.14)
        f = 1400 * np.exp(-t * 18) + 500
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 30)
    if kind == "ding":           # 알림음: 두 음 벨
        out = np.zeros(int(0.9 * SR))
        for at, m in ((0, 88), (0.13, 93)):
            t = _t(0.75)
            bell = (np.sin(2 * np.pi * hz(m) * t) + 0.4 * np.sin(2 * np.pi * hz(m) * 2.76 * t)) * np.exp(-t * 5)
            _add(out, bell, at, 0.6)
        return out
    if kind == "boing":          # 놀람: 튀어 오르는 음
        t = _t(0.5)
        f = 180 + 520 * np.abs(np.sin(t * 22)) * np.exp(-t * 4)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    if kind == "sadtrombone":    # 슬픔: '빠밤빠밤~' 내려가는 4음
        out = np.zeros(int(2.2 * SR))
        for i, m in enumerate((58, 57, 56, 55)):
            d = 0.42 if i < 3 else 1.0
            t = _t(d)
            vib = 1 + (0.012 * np.sin(2 * np.pi * 6 * t) if i == 3 else 0)
            f = hz(m) * vib
            ph = 2 * np.pi * np.cumsum(f) / SR
            tone = np.sin(ph) + 0.6 * np.sin(2 * ph) + 0.35 * np.sin(3 * ph) + 0.2 * np.sin(4 * ph)
            env = np.minimum(1, t / 0.04) * np.minimum(1, (d - t) / 0.08)
            _add(out, tone * env * 0.35, i * 0.42)
        return out
    if kind == "sparkle":        # 신남: 올라가는 반짝임
        out = np.zeros(int(0.8 * SR))
        for i, m in enumerate((84, 88, 91, 96)):
            _add(out, pluck(hz(m), 0.4, decay=10), i * 0.06, 0.35)
        return out
    raise KeyError(kind)


SFX_KINDS = ("whoosh", "pop", "ding", "boing", "sadtrombone", "sparkle")


def ensure_sfx(kind, folder: Path) -> Path:
    path = folder / f"{kind}.wav"
    if not path.exists():
        write_wav(path, _normalize(_sfx(kind), rms_db=-16))
    return path
