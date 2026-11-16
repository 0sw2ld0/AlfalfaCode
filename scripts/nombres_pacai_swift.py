"""Rehace `alfalfa-mac/Sources/AlfalfaKit/Pacai/PacaiNames.swift` desde `alfalfa/pacai/nombres.py`.

    python scripts/nombres_pacai_swift.py             # escribe el archivo
    python scripts/nombres_pacai_swift.py --comprobar # sale con 1 si no está al día

La app de Mac habla PACAI con la extensión `alfalfa`, cuyos campos y valores van en inglés. Para devolverlos a los
nombres de v2 que leen sus decodificadores necesita las mismas tablas que el motor: este guion las copia tal cual,
y `tests/test_pacai_nombres_swift.py` falla si alguien cambia `nombres.py` sin rehacer el archivo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from alfalfa.pacai import nombres  # noqa: E402

DESTINO = RAIZ / 'alfalfa-mac' / 'Sources' / 'AlfalfaKit' / 'Pacai' / 'PacaiNames.swift'
ANCHO = 120
SANGRIA = ' ' * 8


def _cadena(texto: str) -> str:
    return json.dumps(texto, ensure_ascii=False)


def _filas(pares: list[str], sangria: str = SANGRIA) -> list[str]:
    """Los pares `"a": "b"` repartidos en líneas de hasta `ANCHO` columnas."""
    filas, actual = [], sangria
    for par in pares:
        trozo = par + ','
        if actual.strip() and len(actual) + 1 + len(trozo) > ANCHO:
            filas.append(actual.rstrip())
            actual = sangria
        actual += ('' if not actual.strip() else ' ') + trozo
    if actual.strip():
        filas.append(actual.rstrip().rstrip(','))
    return filas


def _plano(nombre: str, documento: str, tabla: dict[str, str]) -> list[str]:
    pares = [_cadena(k) + ': ' + _cadena(v) for k, v in sorted(tabla.items())]
    return ['    /// ' + documento, '    static let ' + nombre + ': [String: String] = ['] + _filas(pares) + ['    ]']


def _anidado(nombre: str, documento: str, tablas: dict[str, dict[str, str]]) -> list[str]:
    lineas = ['    /// ' + documento, '    static let ' + nombre + ': [String: [String: String]] = [']
    claves = sorted(tablas)
    for n, clave in enumerate(claves):
        pares = [_cadena(k) + ': ' + _cadena(v) for k, v in sorted(tablas[clave].items())]
        lineas.append(SANGRIA + _cadena(clave) + ': [')
        lineas += _filas(pares, SANGRIA + '    ')
        lineas.append(SANGRIA + ']' + (',' if n < len(claves) - 1 else ''))
    return lineas + ['    ]']


def generar() -> str:
    lineas = [
        '// Generado por scripts/nombres_pacai_swift.py desde alfalfa/pacai/nombres.py: no lo edites a mano.',
        '',
        '/// Los nombres de la extensión `alfalfa` de PACAI: los mismos de `alfalfa/pacai/nombres.py`.',
        'enum PacaiNames {',
    ]
    lineas += _plano('fields', 'Campo de v2 → campo de la extensión (`CAMPOS`).', nombres.CAMPOS) + ['']
    lineas += _anidado('values', 'Por campo inglés, valor de v2 → valor en inglés (`VALORES`).', nombres.VALORES) + ['']
    lineas += _plano('types', 'Tipo de evento v2 → tipo de la extensión (`TIPOS`).', nombres.TIPOS) + ['']
    lineas += _plano('browserFields', 'La herramienta `browser`: campo de v2 → campo inglés (`NAVEGADOR_CAMPOS`).',
                     nombres.NAVEGADOR_CAMPOS) + ['']
    lineas += _anidado('browserValues', 'La herramienta `browser`, por campo inglés (`NAVEGADOR_VALORES`).',
                       nombres.NAVEGADOR_VALORES)
    return '\n'.join(lineas + ['}']) + '\n'


def main() -> int:
    texto = generar()
    if '--comprobar' in sys.argv[1:]:
        if not DESTINO.exists() or DESTINO.read_text(encoding='utf-8') != texto:
            print('PacaiNames.swift no está al día: python scripts/nombres_pacai_swift.py', file=sys.stderr)
            return 1
        return 0
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(texto, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
