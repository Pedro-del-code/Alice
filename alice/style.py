"""Estilo de conversa adaptativo: espelha o registro do visitante (tamanho, formalidade, emojis). Regras simples e determinísticas."""
import re

from .search import fold

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_INFORMAL = {"vc", "tb", "tbm", "ta", "kk", "kkk", "kkkk", "rs", "blz", "mano", "ne", "pq", "pra", "pro", "cara", "vei", "mds", "opa"}
_FORMAL = {"prezado", "prezada", "senhor", "senhora", "cordialmente", "gostaria", "solicito", "atenciosamente", "poderia"}


def hint(message: str, previous_user_msgs: list[str] | None = None) -> str:
    msgs = [m for m in (previous_user_msgs or [])[-3:]] + [message]
    avg = sum(len(m) for m in msgs) / len(msgs)
    words = set(re.findall(r"[a-z]+", fold(" ".join(msgs))))
    informal, formal = bool(words & _INFORMAL), bool(words & _FORMAL)
    emoji = any(_EMOJI.search(m) for m in msgs)
    parts = []
    if avg < 60:
        parts.append("o visitante escreve curto: responda curto (1 a 3 frases)")
    elif avg < 250:
        parts.append("responda em 2 a 5 frases")
    else:
        parts.append("o visitante escreve bastante: pode elaborar, sem encher")
    if informal and not formal:
        parts.append("linguagem leve e informal")
    elif formal:
        parts.append("tom mais cuidadoso e polido")
    if emoji:
        parts.append("pode usar no máximo 1 emoji, se combinar")
    return "=== ESTILO PARA ESTA RESPOSTA ===\n" + "; ".join(parts) + "."
