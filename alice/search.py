"""Busca textual local (BM25) com normalização de acentos — sem dependências e sem enviar dados a terceiros.
Não é busca semântica por embeddings; é léxica, mas robusta para o português."""
import math
import re
import unicodedata

STOP = set("""para com uma uns umas que por mais como mas foi sao nao nos das dos ele ela eles elas seu sua seus suas
isso isto esse essa esta este aqui ali ser ter tem tinha sobre entre quando muito pelo pela pelos pelas
voce voces eu meu minha meus minhas qual quais onde sera quem porque pois ate depois ainda tambem
the and you are was what""".split())


def fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch))


def tokens(s: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]{3,}", fold(s)):
        if w in STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q = set(tokens(query))
    toks = [tokens(d) for d in docs]
    if not q or not docs:
        return [0.0] * len(docs)
    avg = (sum(len(t) for t in toks) / len(toks)) or 1.0
    n = len(docs)
    df = {w: sum(1 for t in toks if w in t) for w in q}
    scores = []
    for t in toks:
        s = 0.0
        for w in q:
            tf = t.count(w)
            if not tf:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        scores.append(s)
    return scores
