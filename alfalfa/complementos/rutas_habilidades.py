"""Carpetas de habilidades del proyecto que sólo escribe el arnés con su tarjeta."""
import unicodedata
from pathlib import Path

FORMATOS = ('alfalfa', 'agents', 'claude', 'codex')


def _clave(valor):
    return unicodedata.normalize('NFD', str(valor)).casefold()


def carpetas_protegidas(cwd):
    cwd = Path(cwd).resolve()
    raiz = next((p for p in (cwd, *cwd.parents) if (p / '.git').exists()), cwd)
    carpetas = [base / ('.' + formato) / 'skills'
                for base in dict.fromkeys((raiz, cwd)) for formato in FORMATOS]
    # Abrir una sesión dentro de una habilidad tampoco la vuelve escribible.
    carpetas.extend(p for p in (cwd, *cwd.parents)
                    if _clave(p.name) == 'skills' and _clave(p.parent.name) in {'.' + f for f in FORMATOS})
    return tuple(dict.fromkeys(p.resolve() for p in carpetas))


def es_habilidad(cwd, ruta):
    partes = [_clave(p) for p in Path(ruta).parts]
    if any(p in {'.' + f for f in FORMATOS} and partes[i + 1] == 'skills'
           for i, p in enumerate(partes[:-1])):
        return True
    ruta = _clave(ruta)
    return any(ruta == _clave(p) or ruta.startswith(_clave(p).rstrip('/') + '/')
               for p in carpetas_protegidas(cwd))
