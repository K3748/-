import json
from pathlib import Path

DEFAULTS = {
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "max_total_sec": 58.0,          # 쇼츠 최대 길이보다 약간 짧게
    "analysis_fps": 15,             # 분석용 샘플링 fps
    "max_head_trim": 1.5,           # 앞에서 최대 몇 초까지 잘라낼지
    "max_tail_trim": 2.0,
    "tail_safety": 0.1,             # AI 영상 마지막 프레임 깨짐 대비
    "reaction_lead": 0.6,           # 반응 시작 전에 남겨 둘 여유
    "max_wait_before_reaction": 1.2,
    "max_clip_sec": 6.0,            # 클립 하나가 이보다 길면 속도를 높이거나 자름
    "max_speedup": 1.25,
    "framing": "auto",              # auto | crop | blur
    "zoom_punch": True,             # 반응 시점에 살짝 확대
    "zoom_amount": 1.12,
    "caption_from_filename": True,
    "use_whisper": True,
    "font": "auto",
    "font_size": 82,
    "caption_margin_v": 520,        # 쇼츠 UI에 가리지 않도록 하단 여백
    "bgm_volume": 0.35,
    "sfx_volume": 0.8,
    "target_lufs": -14.0,
    "qc_max_rounds": 2,
}


def load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    if path and path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg
