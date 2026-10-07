"""자막 텍스트 결정 + ASS 자막 파일 생성."""
import platform
import re
from pathlib import Path


def text_for_clip(path: Path, cfg, log) -> list[str]:
    """우선순위: 같은 이름의 .txt → captions.txt → 음성 인식 → 파일명."""
    side = path.with_suffix(".txt")
    if side.exists():
        return [l.strip() for l in side.read_text(encoding="utf-8").splitlines() if l.strip()]
    table = path.parent / "captions.txt"
    if table.exists():
        for line in table.read_text(encoding="utf-8").splitlines():
            if "|" in line:
                name, text = line.split("|", 1)
                if name.strip() in (path.name, path.stem):
                    return [t.strip() for t in text.split("/") if t.strip()]
    if cfg["use_whisper"]:
        lines = _whisper(path, log)
        if lines:
            return lines
    if cfg["caption_from_filename"]:
        name = re.sub(r"^[\d\s_\-.]+", "", path.stem).replace("_", " ").strip()
        # 자동 생성 파일명(kling_2026..., 해시 등)은 자막으로 쓰지 않음
        if name and not re.search(r"\d{6,}|[0-9a-f]{6,}", name) and not re.match(
                r"^(kling|runway|sora|veo|gen|pika|hailuo|vid|video|clip)\b", name, re.I):
            return [name]
    return []


def _whisper(path, log):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    try:
        model = WhisperModel("small", compute_type="int8")
        segs, _ = model.transcribe(str(path), language="ko", vad_filter=True)
        lines = [s.text.strip() for s in segs if s.text.strip()]
        if lines:
            log(f"  음성 인식 자막: {lines}")
        return lines
    except Exception as e:  # 인식 실패는 치명적이지 않음
        log(f"  음성 인식 실패: {e}")
        return []


def default_font():
    return {"Windows": "Malgun Gothic", "Darwin": "Apple SD Gothic Neo"}.get(
        platform.system(), "Noto Sans CJK KR")


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def wrap(text, limit=12):
    """한 줄이 너무 길면 공백 기준으로 두 줄로 나눈다."""
    if len(text) <= limit or " " not in text:
        return text
    words, best = text.split(" "), None
    for i in range(1, len(text.split(" "))):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b))
        if best is None or score < best[0]:
            best = (score, a + "\\N" + b)
    return best[1]


def write_ass(events, cfg, out: Path):
    """events: [(start, end, text)] 최종 타임라인 기준."""
    font = default_font() if cfg["font"] == "auto" else cfg["font"]
    W, H, fs = cfg["width"], cfg["height"], cfg["font_size"]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Main,{font},{fs},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,"
        f"80,80,{cfg['caption_margin_v']},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for s, e, text in events:
        pop = r"{\fad(60,80)\fscx70\fscy70\t(0,140,\fscx108\fscy108)\t(140,220,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Main,,0,0,0,,{pop}{wrap(text)}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
