"""Vetores de significado para a memória.
Padrão: HashEmbedder (local, sem rede): hashing de palavras + trigramas de caracteres. Captura semelhança MORFOLÓGICA
(aprendizado ≈ aprendizagem) mas NÃO sinônimos reais. Para semântica de verdade, configure um provedor de embeddings
compatível com a API da OpenAI (ALICE_EMBED_URL / ALICE_EMBED_KEY / ALICE_EMBED_MODEL); se ele falhar, cai no local."""
import hashlib
import json
import math
import urllib.error
import urllib.request

from .search import fold, tokens

DIM = 256

# Pequeno mapa curado de conceitos (sinônimos/família de palavras comuns). Dá um pouco de semântica real ao vetor local;
# não substitui embeddings de verdade. Amplie à vontade.
GROUPS = [
    ["alegre", "alegria", "feliz", "felicidade", "contente", "animado", "satisfeito", "radiante"],
    ["triste", "tristeza", "desanimado", "melancolia", "abatido", "deprimido"],
    ["medo", "receio", "temor", "assustado", "susto", "pavor", "ansiedade", "ansioso", "nervoso"],
    ["raiva", "irritado", "bravo", "furioso", "irritacao", "ira", "zangado"],
    ["amor", "carinho", "afeto", "paixao", "querer", "gostar", "apreco"],
    ["amigo", "amiga", "amizade", "companheiro", "parceiro", "colega"],
    ["lembrar", "lembranca", "memoria", "recordar", "recordacao", "relembrar", "saudade"],
    ["aprender", "aprendizado", "aprendizagem", "estudar", "estudo", "ensinar", "ensino", "educacao"],
    ["pensar", "pensamento", "refletir", "reflexao", "raciocinar", "ponderar", "meditar"],
    ["curioso", "curiosidade", "interesse", "interessante", "investigar", "explorar", "descobrir", "descoberta"],
    ["familia", "mae", "pai", "irmao", "irma", "filho", "filha", "parente", "avo"],
    ["trabalho", "emprego", "profissao", "carreira", "servico", "ocupacao", "empresa"],
    ["casa", "lar", "moradia", "apartamento", "residencia"],
    ["dinheiro", "grana", "salario", "renda", "pagar", "custo", "preco", "financeiro"],
    ["doente", "doenca", "saude", "sintoma", "dor", "enfermo", "medico", "remedio"],
    ["dormir", "sono", "sonho", "descansar", "cansaco", "cansado", "exausto"],
    ["comida", "comer", "refeicao", "jantar", "almoco", "cafe", "fome", "receita"],
    ["musica", "cancao", "cantar", "banda", "melodia", "tocar", "instrumento"],
    ["livro", "ler", "leitura", "romance", "historia", "literatura", "autor"],
    ["jogo", "jogar", "brincar", "diversao", "divertido", "game"],
    ["viagem", "viajar", "passeio", "turismo", "ferias", "destino"],
    ["escola", "faculdade", "universidade", "aula", "professor", "aluno", "curso"],
    ["computador", "programar", "codigo", "software", "programa", "tecnologia", "app", "internet"],
    ["animal", "bicho", "cachorro", "gato", "pet", "cao"],
    ["consciencia", "mente", "cognicao", "percepcao", "subjetivo", "experiencia", "sentir", "sentimento", "emocao", "emocional"],
    ["identidade", "personalidade", "eu", "individualidade", "carater"],
    ["objetivo", "meta", "proposito", "plano", "sonho", "ambicao"],
    ["erro", "falha", "engano", "equivoco", "bug", "defeito"],
    ["ajuda", "ajudar", "apoio", "auxilio", "suporte", "socorro"],
    ["conversar", "conversa", "papo", "dialogo", "falar", "bater-papo"],
]
CONCEPT: dict[str, str] = {}
for _g in GROUPS:
    for _w in _g:
        for _t in tokens(_w):
            CONCEPT.setdefault(_t, tokens(_g[0])[0] if tokens(_g[0]) else _g[0])


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) if a and b and len(a) == len(b) else 0.0


class HashEmbedder:
    name = "hash-ngram-256"

    def embed(self, text: str) -> list[float]:
        v = [0.0] * DIM

        def add(feat: str, w: float) -> None:
            h = _h(feat)
            v[h % DIM] += w if (h >> 63) & 1 else -w

        for w in tokens(text):
            add("w:" + w, 1.0)
            if w in CONCEPT:
                add("c:" + CONCEPT[w], 2.0)
            g = f"#{w}#"
            for i in range(len(g) - 2):
                add("g:" + g[i:i + 3], 0.5)
        return _norm(v)


class RemoteEmbedder:
    """Provedor externo (OpenAI-compatível). Envia o texto da memória ao provedor: informe isso na política de privacidade."""

    def __init__(self, url: str, key: str, model: str, timeout: int = 15):
        self.url, self._key, self.model, self.timeout = url.rstrip("/") + "/embeddings", key, model, timeout
        self.name = f"remote:{model}"

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "User-Agent": "alice-prototype/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def embed(self, text: str) -> list[float]:
        try:
            data = self._post(json.dumps({"model": self.model, "input": text[:2000]}).encode())
            return _norm([float(x) for x in data["data"][0]["embedding"]])
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError):
            raise RuntimeError("falha ao obter embedding remoto") from None


def build_embedder(cfg):
    if getattr(cfg, "embed_url", "") and getattr(cfg, "embed_key", ""):
        return RemoteEmbedder(cfg.embed_url, cfg.embed_key, cfg.embed_model)
    return HashEmbedder()


def quantize(v: list[float]) -> str:
    return json.dumps([round(x * 127) for x in v])


def dequantize(s: str | None) -> list[float] | None:
    try:
        return [i / 127 for i in json.loads(s)] if s else None
    except ValueError:
        return None
