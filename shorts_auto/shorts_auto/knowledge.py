"""편집 규칙 지식 베이스: 기본 규칙(배포) + 내 PC에서 학습한 값(data/knowledge.json)."""
import json
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent
APP = PKG.parent
DATA = APP / "data"
BASE_FILE = PKG / "knowledge_base.json"
LEARNED_FILE = DATA / "knowledge.json"


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_rules():
    """규칙 사전: {id: {...기본, value(학습 반영), learned: bool, history: [...]}}"""
    base = _read(BASE_FILE, {"rules": {}})["rules"]
    learned = _read(LEARNED_FILE, {"values": {}, "history": []})
    rules = {}
    for rid, r in base.items():
        r = dict(r, id=rid, base_value=r["value"], learned=False)
        if rid in learned["values"]:
            r["value"] = learned["values"][rid]["value"]
            r["learned"] = True
            r["learned_at"] = learned["values"][rid].get("at")
            r["learned_sources"] = learned["values"][rid].get("sources", [])
        r["history"] = [h for h in learned["history"] if h["rule"] == rid]
        rules[rid] = r
    return rules


def values():
    return {rid: r["value"] for rid, r in load_rules().items()}


def history():
    return _read(LEARNED_FILE, {"values": {}, "history": []})["history"]


def apply_change(rid, new_value, reason, sources, origin):
    """범위·변경 폭 제한을 적용해 학습 값을 기록한다. 실제 적용된 값을 돌려준다(변화 없으면 None)."""
    rules = load_rules()
    r = rules[rid]
    old = r["value"]
    step = r.get("max_step")
    if step is not None:
        new_value = max(old - step, min(old + step, new_value))
    new_value = max(r["min"], min(r["max"], new_value))
    new_value = round(float(new_value), 3)
    if abs(new_value - old) < 1e-6:
        return None
    data = _read(LEARNED_FILE, {"values": {}, "history": []})
    now = time.strftime("%Y-%m-%d %H:%M")
    data["values"][rid] = {"value": new_value, "at": now, "sources": sources}
    data["history"].append({"rule": rid, "title": r["title"], "before": old, "after": new_value,
                            "at": now, "origin": origin, "reason": reason, "sources": sources})
    DATA.mkdir(parents=True, exist_ok=True)
    LEARNED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_value


def reset(rid=None):
    """학습 값 되돌리기 (rid 없으면 전체)."""
    data = _read(LEARNED_FILE, {"values": {}, "history": []})
    for k in ([rid] if rid else list(data["values"])):
        if k in data["values"]:
            old = data["values"].pop(k)["value"]
            base = _read(BASE_FILE, {"rules": {}})["rules"][k]
            data["history"].append({"rule": k, "title": base["title"], "before": old, "after": base["value"],
                                    "at": time.strftime("%Y-%m-%d %H:%M"), "origin": "사용자",
                                    "reason": "사용자가 기본값으로 되돌림", "sources": []})
    DATA.mkdir(parents=True, exist_ok=True)
    LEARNED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def basis_text(rid):
    r = load_rules().get(rid)
    if not r:
        return ""
    src = r.get("learned_sources") if r["learned"] else None
    src = src or r["sources"]
    tag = f"학습값 ({r.get('learned_at')})" if r["learned"] else "기본 규칙"
    return f"[{r['title']} = {r['value']}{r['unit']} · {tag}] {r['reason']}" + \
        ("".join(f"\n   - {s['title']}: {s['url']}" for s in src) if src else "")
