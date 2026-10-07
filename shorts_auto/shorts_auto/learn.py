"""자동 학습.

1) 웹 수집: knowledge_sources.json 의 페이지를 읽어 규칙별 수치를 뽑고,
   서로 다른 출처 2곳 이상이 같은 값(±10%)을 말할 때만 반영한다.
2) 자체 피드백: 내 영상 검사(QC) 결과가 반복해서 같은 문제를 보이면 관련 규칙을 조정한다.
3) 주기 실행: 마지막 학습 후 interval_days 가 지나면 프로그램 시작 시 자동 실행.
모든 변경은 범위·변경 폭 제한을 거치고 data/knowledge.json 에 이유·출처와 함께 기록된다.
"""
import html
import json
import re
import statistics
import time
import urllib.request
from pathlib import Path

from . import knowledge
from .knowledge import DATA, PKG

SOURCES_FILE = PKG / "knowledge_sources.json"
USER_SOURCES_FILE = DATA / "sources_user.json"
STATE_FILE = DATA / "learn_state.json"
QC_HISTORY_FILE = DATA / "qc_history.json"

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}

# 규칙별 추출 패턴과 변환 (페이지 본문에서 찾은 숫자 x → 규칙 값)
EXTRACTORS = {
    "target_lufs": [(r"(?:youtube|you tube)[^.]{0,120}?(-1[0-9](?:\.\d)?)\s*LUFS", lambda x: float(x)),
                    (r"(-1[0-9](?:\.\d)?)\s*LUFS[^.]{0,80}?youtube", lambda x: float(x))],
    "caption_margin_v": [(r"bottom[^.]{0,80}?(\d{3})\s*(?:px|pixels)", lambda x: float(x) + 220),
                         (r"(\d{3})\s*(?:px|pixels)[^.]{0,40}?from the bottom", lambda x: float(x) + 220)],
    "caption_margin_h": [(r"right[^.]{0,60}?(\d{2,3})\s*(?:px|pixels)", lambda x: float(x) + 44)],
    "max_total_sec": [(r"(?:shorts?)[^.]{0,120}?up to (one|two|three|four|five|\d)[ -]minutes?",
                       lambda x: WORDNUM[x.lower()] * 60 - 2)],
    "hook_sec": [(r"first (one|two|three|four|five|\d) seconds", lambda x: float(WORDNUM[x.lower()]))],
}


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path, data):
    DATA.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def page_text(raw_html):
    s = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("−", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) shorts-auto-learner/1.0",
        "Accept-Language": "en,ko;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def extract(param, text):
    """한 페이지에서 param 값 하나(가장 많이 언급된 값)를 뽑는다."""
    found = []
    for pattern, conv in EXTRACTORS.get(param, []):
        for m in re.finditer(pattern, text, re.I):
            try:
                found.append(round(conv(m.group(1)), 2))
            except (KeyError, ValueError):
                pass
    if not found:
        return None
    return statistics.mode(found)


def consensus(observations, min_sources=2, tol=0.10):
    """[(value, url)] → 서로 ±tol 이내로 일치하는 최대 그룹이 min_sources 이상이면 (값, 출처들)."""
    best = []
    for v, _ in observations:
        group = [(w, u) for w, u in observations if abs(w - v) <= abs(v) * tol + 1e-9]
        if len(group) > len(best):
            best = group
    if len({u for _, u in best}) < min_sources:
        return None, [u for _, u in observations]
    return statistics.median(w for w, _ in best), [u for _, u in best]


def learn_from_web(log=print, fetcher=fetch):
    pages = _read(SOURCES_FILE, {"pages": []})["pages"] + _read(USER_SOURCES_FILE, {"pages": []})["pages"]
    rules = knowledge.load_rules()
    obs = {}
    ok = fail = 0
    for p in pages:
        try:
            text = page_text(fetcher(p["url"]))
            ok += 1
        except Exception as e:
            fail += 1
            log(f"  ✖ 수집 실패: {p['url']} ({type(e).__name__})")
            continue
        for param in p["params"]:
            v = extract(param, text)
            if v is not None:
                obs.setdefault(param, []).append((v, p["url"]))
                log(f"  · {param}: {v} ← {p['url']}")
    log(f"  페이지 {ok}개 수집, {fail}개 실패")
    changes = []
    for param, items in obs.items():
        if param not in rules:
            continue
        value, urls = consensus(items)
        r = rules[param]
        if value is None:
            log(f"  - {r['title']}: 출처 간 의견 불일치/부족 → 유지 ({items})")
            continue
        if abs(value - r["value"]) <= abs(r["value"]) * 0.03:
            log(f"  - {r['title']}: 출처 {len(urls)}곳이 현재 값({r['value']})과 일치 → 유지")
            continue
        applied = knowledge.apply_change(
            param, value, f"웹 출처 {len(urls)}곳이 {value}{r['unit']}로 일치",
            [{"title": u, "url": u} for u in urls], "웹 학습")
        if applied is not None:
            changes.append((param, r["value"], applied))
            log(f"  ★ {r['title']}: {r['value']} → {applied}{r['unit']} (출처 {len(urls)}곳)")
    return changes


# ---------------------------------------------------------------- 자체 피드백
def record_qc(project_result):
    """한 번의 제작 결과(QC 요약)를 기록한다."""
    hist = _read(QC_HISTORY_FILE, [])
    hist.append(dict(project_result, at=time.strftime("%Y-%m-%d %H:%M")))
    _write(QC_HISTORY_FILE, hist[-30:])


def learn_from_results(log=print, window=5, need=3):
    hist = _read(QC_HISTORY_FILE, [])[-window:]
    if len(hist) < need:
        log(f"  내 영상 검사 기록 {len(hist)}건 (최소 {need}건 필요) → 자체 보정 보류")
        return []
    rules = knowledge.load_rules()
    changes = []

    def bump(rid, delta, count, what):
        r = rules[rid]
        applied = knowledge.apply_change(rid, r["value"] + delta,
                                         f"최근 {len(hist)}번 중 {count}번 {what}", [], "내 영상 검사")
        if applied is not None:
            changes.append((rid, r["value"], applied))
            log(f"  ★ {r['title']}: {r['value']} → {applied}{r['unit']} ({what} {count}회)")

    tail = sum(1 for h in hist if h.get("tail_fixes", 0) > 0)
    head = sum(1 for h in hist if h.get("head_fixes", 0) > 0)
    if tail >= need:
        bump("max_tail_trim", 0.3, tail, "끝부분 멈춤/검은 화면을 검사 단계에서 추가로 잘라냄")
    if head >= need:
        bump("max_head_trim", 0.3, head, "앞부분 멈춤/검은 화면을 검사 단계에서 추가로 잘라냄")
    errs = [h["first_lufs"] - h["target_lufs"] for h in hist
            if h.get("first_lufs") is not None and h.get("target_lufs") is not None
            and h["first_lufs"] > -60]          # 무음(-70) 기록은 음량 오차가 아니므로 제외
    if len(errs) >= need and abs(statistics.mean(errs)) > 0.7:
        err = statistics.mean(errs)
        r = rules["lufs_pre_offset"]
        applied = knowledge.apply_change("lufs_pre_offset", r["value"] - err,
                                         f"1차 음량이 평균 {err:+.1f}dB 벗어남", [], "내 영상 검사")
        if applied is not None:
            changes.append(("lufs_pre_offset", r["value"], applied))
            log(f"  ★ {r['title']}: {r['value']} → {applied}dB (평균 오차 {err:+.1f}dB)")
    if not changes:
        log("  내 영상 검사 결과: 조정할 반복 문제 없음")
    return changes


# ---------------------------------------------------------------- 실행/주기
def state():
    return _read(STATE_FILE, {"last_run": 0, "interval_days": 7, "last_summary": ""})


def set_interval(days):
    s = state()
    s["interval_days"] = days
    _write(STATE_FILE, s)


def is_due():
    s = state()
    return time.time() - s.get("last_run", 0) >= s.get("interval_days", 7) * 86400


def run(log=print, fetcher=fetch):
    log("=== 자동 학습 시작 ===")
    log("[1] 웹 자료 수집·분석")
    web = learn_from_web(log, fetcher)
    log("[2] 내 영상 검사 기록 분석")
    own = learn_from_results(log)
    s = state()
    s["last_run"] = time.time()
    s["last_summary"] = f"{time.strftime('%Y-%m-%d %H:%M')} · 변경 {len(web) + len(own)}건"
    _write(STATE_FILE, s)
    log(f"=== 학습 완료: 변경 {len(web) + len(own)}건 ===")
    return web + own
