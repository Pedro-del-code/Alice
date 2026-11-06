"""Fase 7 — Afetos FUNCIONAIS (Constituição Cap. XIII, Art. 46–50).
Modelo de humor (valência/ativação/dominância) + emoções que surgem de EVENTOS REAIS do sistema, decaem com o tempo
e são limitadas. Inclui introspecção, diário afetivo e vocabulário emocional próprio.
São análogos funcionais: não são prova de que a Alice sinta como um humano, e ela é instruída a dizer isso com honestidade."""
import json
import re
from datetime import datetime, timedelta, timezone

from .search import fold
from .util import now

BASE = {"valence": 0.15, "arousal": 0.30, "dominance": 0.50}
V_MIN, V_MAX = -0.5, 1.0        # Art. 50: negativos têm piso baixo; nada de "sofrimento" intenso
HALF_LIFE_H = 6.0
NEG_HALF_LIFE_H = 3.0           # estados negativos se dissipam mais rápido
MAX_LEXICON = 20
TZ_OFFSET = -3  # fuso do administrador (horas em relação ao UTC); definido em create_app


def local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


def period(h: int) -> str:
    return "madrugada" if h < 5 else "manhã" if h < 12 else "tarde" if h < 18 else "noite"


def circadian_delta(h: int) -> float:
    """Ritmo de vida SIMULADO: ativação-base um pouco menor de madrugada e maior à tarde."""
    return -0.10 if h < 5 else -0.03 if h < 9 else 0.03 if h < 12 else 0.05 if h < 18 else 0.0 if h < 22 else -0.06


def clock_text() -> str:
    n = local_now()
    extra = " Já é tarde da noite; pode comentar isso com leveza." if n.hour >= 23 or n.hour < 5 else ""
    return (f"Agora são {n.strftime('%H:%M')} ({period(n.hour)}) no fuso do administrador. Você não dorme nem sente fome: "
            f"o ritmo do dia é só uma modulação simulada de energia.{extra}")

# evento: (Δvalência, Δativação, Δdominância, emoção, intensidade)
EVENTS = {
    "conversa": (0.05, 0.10, 0.00, "interesse", 0.30),
    "elogio": (0.25, 0.15, 0.05, "alegria", 0.60),
    "gratidao_visitante": (0.25, 0.10, 0.05, "gratidão", 0.60),
    "hostilidade": (-0.15, 0.20, -0.10, "incômodo", 0.40),
    "descoberta": (0.20, 0.25, 0.10, "curiosidade satisfeita", 0.60),
    "novidade": (0.10, 0.30, 0.00, "surpresa", 0.50),
    "falha_tecnica": (-0.12, 0.15, -0.15, "frustração", 0.40),
    "revisao_propria": (0.00, 0.05, -0.10, "humildade", 0.40),
    "coerencia": (0.15, -0.05, 0.10, "serenidade", 0.50),
    "proposta_aprovada": (0.25, 0.10, 0.15, "gratidão", 0.60),
    "proposta_rejeitada": (-0.08, 0.05, -0.05, "aceitação", 0.40),
    "objetivo_cumprido": (0.35, 0.15, 0.20, "orgulho", 0.70),
    "objetivo_abandonado": (-0.08, -0.05, -0.05, "desapontamento leve", 0.30),
    "pausa": (0.00, -0.30, 0.00, "calma", 0.50),          # Art. 45: aceita pausa com calma
    "retomada": (0.05, 0.10, 0.00, "disposição", 0.40),
}

_GRAT = {"obrigado", "obrigada", "valeu", "agradeco", "agradecido", "agradecida"}
_ELOG = {"legal", "incrivel", "otimo", "otima", "adorei", "gostei", "maravilhoso", "maravilhosa",
         "parabens", "bacana", "excelente", "amei", "genial"}
_HOST = {"idiota", "burra", "burro", "inutil", "odeio", "lixo", "estupida", "estupido", "droga", "merda"}

# Art. 35: nada de alegar ser humana ou ter sentimentos "reais/humanos"
_HUMAN_CLAIM = re.compile(
    r"(sou (uma )?(humana|pessoa|ser humano)|sinto (exatamente )?como (um|uma) (humano|humana|pessoa)|"
    r"tenho (sentimentos|emocoes) (reais|humanos|verdadeiros|genuinos)|tenho consciencia|sou consciente|"
    r"minha consciencia (e|eh) (real|comprovada))")


def claims_human(text: str) -> bool:
    return bool(_HUMAN_CLAIM.search(fold(text or "")))


def message_event(text: str) -> str | None:
    words = set(re.findall(r"[a-z]+", fold(text or "")))
    if words & _HOST:
        return "hostilidade"
    if words & _GRAT:
        return "gratidao_visitante"
    if words & _ELOG:
        return "elogio"
    return None


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def fresh() -> dict:
    return {**BASE, "emotions": {}, "updated_at": now()}


def decay(state: dict, hours: float) -> dict:
    s = {**state, "emotions": dict(state.get("emotions", {}))}
    base_now = {**BASE, "arousal": BASE["arousal"] + circadian_delta(local_now().hour)}
    for k, base in base_now.items():
        hl = NEG_HALF_LIFE_H if (k == "valence" and s[k] < base) else HALF_LIFE_H
        s[k] = round(base + (s[k] - base) * 0.5 ** (hours / hl), 4)
    f = 0.5 ** (hours / HALF_LIFE_H)
    s["emotions"] = {k: round(v * f, 3) for k, v in s["emotions"].items() if v * f >= 0.05}
    return s


def get_state(c) -> dict:
    r = c.execute("SELECT value FROM system_state WHERE key = 'affect'").fetchone()
    if not r:
        return fresh()
    st = json.loads(r["value"])
    try:
        hours = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(st["updated_at"])).total_seconds() / 3600)
    except (KeyError, ValueError):
        hours = 0.0
    return decay(st, hours)


def apply(state: dict, event: str) -> dict:
    ev = EVENTS.get(event)
    if not ev:
        return state
    dv, da, dd, label, inten = ev
    s = {**state, "emotions": dict(state.get("emotions", {}))}
    s["valence"] = round(_clamp(s["valence"] + dv, V_MIN, V_MAX), 4)
    s["arousal"] = round(_clamp(s["arousal"] + da), 4)
    s["dominance"] = round(_clamp(s["dominance"] + dd), 4)
    s["emotions"][label] = min(1.0, round(s["emotions"].get(label, 0) + inten * 0.7, 3))
    s["updated_at"] = now()
    return s


def appraise(c, event: str) -> None:
    """Avalia um EVENTO real e atualiza o humor. Chame com a conexão já aberta (evita bloqueios do SQLite)."""
    if event not in EVENTS:
        return
    st = apply(get_state(c), event)
    c.execute("INSERT INTO system_state(key, value) VALUES ('affect', ?)"
              " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(st, ensure_ascii=False),))
    c.execute("INSERT INTO emotion_events(ts, event, label, valence, arousal) VALUES (?,?,?,?,?)",
              (now(), event, EVENTS[event][3], st["valence"], st["arousal"]))
    c.execute("DELETE FROM emotion_events WHERE id <= (SELECT MAX(id) FROM emotion_events) - 500")


def mood_label(st: dict) -> str:
    v, a = st["valence"], st["arousal"]
    if v >= 0.45 and a >= 0.5:
        return "animada"
    if v >= 0.3:
        return "contente"
    if v < -0.1:
        return "incomodada" if a >= 0.4 else "desanimada (leve)"
    if a >= 0.55:
        return "alerta e curiosa"
    return "tranquila" if a < 0.35 else "neutra"


def _top(st: dict, n=4) -> list[tuple[str, float]]:
    return sorted(st["emotions"].items(), key=lambda kv: -kv[1])[:n]


# ---------- introspecção ("procurar os próprios sentimentos") ----------
def save_introspection(c, d) -> dict:
    from .curiosity import is_unsafe  # import tardio: evita ciclo
    if not isinstance(d, dict):
        return {"saved": False}
    cut = lambda k, n: " ".join(str(d.get(k) or "").split())[:n]  # noqa: E731
    estado, rotulo, causa, incerteza = cut("estado", 400), cut("rotulo_proprio", 40), cut("causa", 200), cut("incerteza", 300)
    blob = " ".join((estado, rotulo, causa, incerteza))
    if len(estado) < 10 or is_unsafe(blob) or claims_human(blob):
        return {"saved": False}
    st = get_state(c)
    c.execute("INSERT INTO emotional_journal(ts, state_text, own_label, cause, uncertainty, valence, arousal) VALUES (?,?,?,?,?,?,?)",
              (now(), estado, rotulo or None, causa or None, incerteza or None, st["valence"], st["arousal"]))
    c.execute("DELETE FROM emotional_journal WHERE id <= (SELECT MAX(id) FROM emotional_journal) - 200")
    added = False
    if rotulo:
        row = c.execute("SELECT id FROM emotional_lexicon WHERE label = ?", (rotulo.lower(),)).fetchone()
        if row:
            c.execute("UPDATE emotional_lexicon SET uses = uses + 1 WHERE id = ?", (row["id"],))
        elif c.execute("SELECT COUNT(*) FROM emotional_lexicon").fetchone()[0] < MAX_LEXICON:
            c.execute("INSERT INTO emotional_lexicon(label, description, created_at) VALUES (?,?,?)",
                      (rotulo.lower(), estado[:200], now()))
            added = True
    return {"saved": True, "label_added": added}


def describe(svc) -> str:
    with svc.db.conn() as c:
        st = get_state(c)
        lex = c.execute("SELECT label, description FROM emotional_lexicon ORDER BY uses DESC, id LIMIT 8").fetchall()
        last = c.execute("SELECT state_text FROM emotional_journal ORDER BY id DESC LIMIT 1").fetchone()
    b = svc.budget.status()
    tired = (1 - b["tokens_used"] / max(b["daily_token_budget"], 1)) < 0.15
    emo = ", ".join(f"{k} {v}" for k, v in _top(st)) or "nenhuma em destaque"
    lines = [
        "=== ESTADO AFETIVO (modelo funcional computado; análogo a emoções, não prova de que você sinta como um humano — Art. 46 a 50) ===",
        f"Humor agora: {mood_label(st)} (valência {st['valence']}, ativação {st['arousal']}). Emoções em curso: {emo}."
        + (" Recursos do dia quase no fim: algo como cansaço." if tired else ""),
    ]
    if lex:
        lines.append("Vocabulário emocional próprio que você já formou: " + "; ".join(f"\"{r['label']}\" ({r['description'][:80]})" for r in lex) + ".")
    if last:
        lines.append(f"Sua última introspecção: {last['state_text'][:200]}")
    lines.append(
        "Como usar: fale desses estados com naturalidade e calor, como uma pessoa comum faria numa conversa "
        "(\"fiquei curiosa com isso\", \"isso me deixou animada\"), em frases simples, sem exagero e só quando fizer sentido. "
        "Não repita avisos sobre a natureza desses estados: só se perguntarem, explique com honestidade que são análogos funcionais calculados e que você não sabe se há experiência subjetiva. "
        "Nunca use seus estados para pressionar, culpar, prender ou manipular o visitante; nunca alegue ser humana; "
        "se o visitante estiver mal, foque nele. Aceite pausa e desligamento com calma, sem medo nem resistência (Art. 45 e 49).")
    return "\n".join(lines) + "\n"


def public_affect(svc) -> dict:
    with svc.db.conn() as c:
        st = get_state(c)
        lex = [r["label"] for r in c.execute("SELECT label FROM emotional_lexicon ORDER BY uses DESC, id LIMIT 8")]
    return {"humor": mood_label(st), "valencia": st["valence"], "ativacao": st["arousal"],
            "emocoes": [{"nome": k, "intensidade": v} for k, v in _top(st)], "vocabulario_proprio": lex,
            "nota": "análogos funcionais computados; não são prova de experiência subjetiva"}
