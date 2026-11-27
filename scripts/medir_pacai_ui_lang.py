"""Cuenta con `o200k_base` los tokens de las muestras de PACAI UI Lang frente al JSON de hoy, y los del prompt.

    python scripts/medir_pacai_ui_lang.py            # tabla en markdown

Necesita `tiktoken` (`pip install tiktoken`), que no es dependencia de AlfalfaCode. Las muestras están en
`tests/conformidad/muestras-pacai-ui-lang.json` y una prueba comprueba que cada par dice exactamente lo mismo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from alfalfa.pacai import ui_lang  # noqa: E402
from alfalfa.pacai.ui import Catalogo  # noqa: E402

CONFORMIDAD = RAIZ / 'tests' / 'conformidad'


def tokens(texto: str) -> int:
    import tiktoken

    return len(tiktoken.get_encoding('o200k_base').encode(texto))


def main() -> None:
    muestras = json.loads((CONFORMIDAD / 'muestras-pacai-ui-lang.json').read_text(encoding='utf-8'))['muestras']
    declaracion = json.loads((CONFORMIDAD / 'catalogo-cuymercado.json').read_text(encoding='utf-8'))
    print('| Muestra | JSON (car.) | JSON (tok.) | UI Lang (car.) | UI Lang (tok.) | Ahorro |')
    print('|---|---:|---:|---:|---:|---:|')
    for nombre, muestra in muestras.items():
        j, lang = tokens(muestra['json']), tokens(muestra['lang'])
        print(f'| {nombre} | {len(muestra["json"])} | {j} | {len(muestra["lang"])} | {lang} | {1 - lang / j:.0%} |')
        if 'json_reescrito' in muestra:
            r = tokens(muestra['json_reescrito'])
            print(f'| {nombre} (JSON reescribiendo el tablero) | {len(muestra["json_reescrito"])} | {r} | '
                  f'{len(muestra["lang"])} | {lang} | {1 - lang / r:.0%} |')
    print()
    con = Catalogo.de(declaracion, {ui_lang.FLAG: '1'})
    sin = Catalogo.de(declaracion, {ui_lang.FLAG: '0'})       # el JSON: sin el flag a 0 sale UI Lang
    print('| Prompt del perfil ui | Líneas | Tokens |')
    print('|---|---:|---:|')
    for nombre, texto in (('JSON (hoy, catálogo base + CuyMercado)', sin.manual()),
                          ('PACAI UI Lang completo (manual con piezas)', con.manual()),
                          ('sólo el formato (ui_lang.prompt)', ui_lang.prompt(con)),
                          ('sólo las piezas (ui_lang.prompt_piezas)', ui_lang.prompt_piezas(con))):
        print(f'| {nombre} | {len(texto.strip().splitlines())} | {tokens(texto)} |')


if __name__ == '__main__':
    main()
