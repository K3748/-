"""효과음/BGM 자산 찾기. 없으면 FFmpeg로 기본 효과음을 합성한다."""
import random
from pathlib import Path

from .ffmpeg_utils import run

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

SYNTH = {
    # 전환용 '슉'
    "whoosh": "anoisesrc=d=0.35:c=pink:a=0.6,highpass=f=600,lowpass=f=5000,"
              "afade=t=in:d=0.2:curve=exp,afade=t=out:st=0.2:d=0.15",
    # 반응용 '뿅'
    "pop": "sine=f=900:d=0.15,vibrato=f=25:d=0.6,afade=t=out:st=0.05:d=0.1,volume=0.7",
}


def _files(folder: Path):
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in AUDIO_EXT) if folder.exists() else []


def find_sfx(assets: Path, kind: str) -> Path:
    """assets/sfx 에서 파일명에 kind 가 들어간 효과음을 찾는다."""
    cands = [p for p in _files(assets / "sfx") if kind in p.stem.lower()]
    if cands:
        return random.choice(cands)
    from .audio_gen import ensure_sfx
    return ensure_sfx(kind, assets / "sfx" / "_generated_v2")


def find_bgm(assets: Path, seed=None):
    files = _files(assets / "bgm")
    if not files:
        return None
    return random.Random(seed).choice(files)
