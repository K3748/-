"""폴더의 AI 영상들을 분석해 YouTube Shorts(1080x1920) MP4를 자동 제작한다.

사용법:
    python make_short.py                       # input/ → output/short.mp4
    python make_short.py -i 내영상폴더 -o output/쿠키.mp4
"""
import argparse
from pathlib import Path

from shorts_auto.config import load_config
from shorts_auto.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AI 영상 → 쇼츠 자동 편집")
    ap.add_argument("-i", "--input", default=ROOT / "input", type=Path)
    ap.add_argument("-o", "--output", default=ROOT / "output" / "short.mp4", type=Path)
    ap.add_argument("-a", "--assets", default=ROOT / "assets", type=Path, help="bgm/, sfx/ 폴더 위치")
    ap.add_argument("-c", "--config", default=ROOT / "config.json", type=Path)
    ap.add_argument("--seed", type=int, default=None, help="BGM 선택 고정용")
    args = ap.parse_args()
    run_pipeline(args.input, args.output, args.assets, load_config(args.config), args.seed)
