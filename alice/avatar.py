"""Fase 5 — Avatar como módulo INDEPENDENTE (Art. 25–27). Só recebe (estado, aparência) e devolve SVG;
não conhece memória nem identidade. Para trocar por outro renderizador (3D, vídeo...), substitua render_svg()."""
import json
import re

from . import audit

STATES = ("idle", "thinking", "speaking", "curious", "paused", "happy", "tired")
# Só cores podem evoluir; o laço e a estrutura do rosto permanecem (preserva reconhecimento — Art. 27).
DEFAULT_LOOK = {"dress": "#4a78b8", "apron": "#f7f3ea", "hair": "#e8c36a", "bow": "#2c4a7c",
                "skin": "#f3d3b8", "eyes": "#2d5f8a", "bg": "#dfe9f5"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def get_look(c) -> dict:
    r = c.execute("SELECT value FROM system_state WHERE key = 'avatar'").fetchone()
    return {**DEFAULT_LOOK, **(json.loads(r["value"]) if r else {})}


def evolve_look(c, changes: dict, reason: str, actor="admin") -> dict:
    if not reason or not reason.strip():
        raise ValueError("informe o motivo da mudança de aparência")
    bad = [k for k, v in (changes or {}).items() if k not in DEFAULT_LOOK or not _HEX.match(str(v))]
    if bad or not changes:
        raise ValueError(f"mudanças inválidas: {bad or 'vazio'}; use cores #RRGGBB em {sorted(DEFAULT_LOOK)}")
    old, new = get_look(c), {**get_look(c), **changes}
    c.execute("INSERT INTO system_state(key, value) VALUES ('avatar', ?)"
              " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(new),))
    audit.log(c, actor, "avatar_changed", {"keys": sorted(changes), "reason": reason.strip()[:200]})
    return new


def render_svg(state: str = "idle", look: dict | None = None) -> str:
    L = {**DEFAULT_LOOK, **(look or {})}
    st = state if state in STATES else "idle"
    closed = st == "paused"
    if closed:
        eyes = (f'<path d="M72 112q8 6 16 0M112 112q8 6 16 0" stroke="#3b2f2a" stroke-width="3" fill="none" stroke-linecap="round"/>')
    elif st == "happy":
        eyes = ('<path d="M72 114q8-10 16 0M112 114q8-10 16 0" stroke="#3b2f2a" stroke-width="3.5" fill="none" stroke-linecap="round"/>')
    elif st == "tired":
        eyes = ('<path d="M72 114h16M112 114h16" stroke="#3b2f2a" stroke-width="3" stroke-linecap="round"/>'
                f'<circle cx="80" cy="119" r="3" fill="{L["eyes"]}"/><circle cx="120" cy="119" r="3" fill="{L["eyes"]}"/>')
    else:
        dy = -3 if st in ("thinking", "curious") else 0
        eyes = (f'<g class="blink"><ellipse cx="80" cy="112" rx="8" ry="10" fill="#fff"/><ellipse cx="120" cy="112" rx="8" ry="10" fill="#fff"/>'
                f'<circle cx="{82 if st == "thinking" else 80}" cy="{112 + dy}" r="5" fill="{L["eyes"]}"/>'
                f'<circle cx="{122 if st == "thinking" else 120}" cy="{112 + dy}" r="5" fill="{L["eyes"]}"/>'
                f'<circle cx="{83 if st == "thinking" else 81}" cy="{110 + dy}" r="1.6" fill="#fff"/>'
                f'<circle cx="{123 if st == "thinking" else 121}" cy="{110 + dy}" r="1.6" fill="#fff"/></g>')
    brow = ('<path d="M110 94q10-8 20-2" stroke="#8a6a2a" stroke-width="3" fill="none" stroke-linecap="round"/>'
            '<path d="M70 96q10-4 20 0" stroke="#8a6a2a" stroke-width="3" fill="none" stroke-linecap="round"/>') if st == "curious" else \
           ('<path d="M70 96q10-3 20 0M110 96q10-3 20 0" stroke="#8a6a2a" stroke-width="3" fill="none" stroke-linecap="round"/>')
    if st == "speaking":
        mouth = '<ellipse cx="100" cy="140" rx="9" ry="6" fill="#a8453f"><animate attributeName="ry" values="2;7;3;6;2" dur="0.9s" repeatCount="indefinite"/></ellipse>'
    elif st == "happy":
        mouth = '<path d="M84 136q16 20 32 0" stroke="#a8453f" stroke-width="3" fill="#fff" stroke-linecap="round"/>'
    elif closed or st == "tired":
        mouth = '<path d="M92 140q8 3 16 0" stroke="#a8453f" stroke-width="3" fill="none" stroke-linecap="round"/>'
    else:
        mouth = '<path d="M90 138q10 9 20 0" stroke="#a8453f" stroke-width="3" fill="none" stroke-linecap="round"/>'
    bubble = {"thinking": '<g fill="#8a94a6"><circle cx="160" cy="50" r="4"/><circle cx="172" cy="38" r="6"/><circle cx="188" cy="22" r="9"/></g>',
              "curious": '<text x="158" y="48" font-size="34" font-family="Georgia,serif" fill="#8a94a6">?</text>',
              "paused": '<text x="150" y="46" font-size="22" font-family="Georgia,serif" fill="#8a94a6">z z</text>'}.get(st, "")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 240" role="img" aria-label="Avatar da Alice ({st})">
<style>.blink{{animation:b 5s infinite;transform-origin:100px 112px}}@keyframes b{{0%,94%,100%{{transform:scaleY(1)}}97%{{transform:scaleY(.1)}}}}</style>
<rect width="200" height="240" rx="24" fill="{L["bg"]}"/>
<path d="M40 240q4-70 60-70t60 70z" fill="{L["dress"]}"/><path d="M78 240l22-62 22 62z" fill="{L["apron"]}"/>
<rect x="88" y="150" width="24" height="26" rx="8" fill="{L["skin"]}"/>
<path d="M44 130q-8-70 56-74t56 74q-6-12-14-14l-84 0q-8 2-14 14z" fill="{L["hair"]}"/>
<path d="M50 120q0 40 12 52 8-30 4-60zM150 120q0 40-12 52-8-30-4-60z" fill="{L["hair"]}"/>
<ellipse cx="100" cy="118" rx="42" ry="48" fill="{L["skin"]}"/>
<path d="M58 108q6-44 42-44t42 44q-20-26-42-24t-42 24z" fill="{L["hair"]}"/>
<path d="M56 92q44-34 88 0" stroke="{L["bow"]}" stroke-width="7" fill="none" stroke-linecap="round"/>
<path d="M142 88l16-12v26z M142 88l-4-14 20 2z" fill="{L["bow"]}"/>
{brow}{eyes}<circle cx="68" cy="130" r="6" fill="#f0a89a" opacity=".5"/><circle cx="132" cy="130" r="6" fill="#f0a89a" opacity=".5"/>
{mouth}{bubble}</svg>'''
