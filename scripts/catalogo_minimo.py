"""Rehace `alfalfa/catalogo/minimo.json`, la copia mínima de models.dev que viaja con el paquete.

    python scripts/catalogo_minimo.py [api.json]

Sin argumento baja https://models.dev/api.json. Guarda sólo los proveedores que
AlfalfaCode usa sin configurar nada más (OpenAI, Ollama Cloud, OpenRouter,
OpenCode Zen y NVIDIA) y, de ellos, los modelos vigentes que llaman
herramientas. Basta para el primer arranque sin internet; con red, el catálogo
completo la sustituye en `~/.alfalfa/catalogo/`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from alfalfa import catalogo  # noqa: E402

PROVEEDORES = ('openai', 'ollama-cloud', 'openrouter', 'opencode', 'nvidia')
# De OpenRouter, con cientos de modelos, sólo los gratis.
SOLO_GRATIS = ('openrouter', 'nvidia')


def main():
    crudo = json.loads(Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else catalogo._bajar())
    completos = catalogo.validar(crudo)
    minimo = {}
    for ident in PROVEEDORES:
        datos = completos.get(ident)
        if not datos:
            continue
        modelos = {k: f for k, f in datos['modelos'].items()
                   if f.get('herramientas') and f.get('estado') != 'deprecated'
                   and (ident not in SOLO_GRATIS or catalogo.es_gratis(datos, f))}
        if modelos:
            minimo[ident] = dict(datos, modelos=modelos)
    destino = RAIZ / 'alfalfa' / 'catalogo' / 'minimo.json'
    destino.write_text(json.dumps({'fuente': catalogo.URL, 'fecha': int(time.time()), 'proveedores': minimo},
                                  ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(destino, sum(len(d['modelos']) for d in minimo.values()), 'modelos')


if __name__ == '__main__':
    main()
