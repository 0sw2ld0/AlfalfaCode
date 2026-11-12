"""Carpetas de habilidades del proyecto que sólo escribe el arnés con su tarjeta."""
import os
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


def carpetas_descubiertas(cwd, limite=200_000):
    """Sólo metadatos: incluye habilidades de futuros cwd y destinos de enlaces previos."""
    pendientes, encontradas, vistas = [Path(cwd).resolve()], set(), 0
    while pendientes:
        with os.scandir(pendientes.pop()) as entradas:
            for entrada in entradas:
                vistas += 1
                if vistas > limite:
                    raise ValueError('No se pudo completar la comprobación de las carpetas de habilidades.')
                nombre = _clave(entrada.name)
                if nombre in {'.' + f for f in FORMATOS}:
                    encontradas.add(Path(entrada.path).resolve())
                elif nombre != '.git' and entrada.is_dir(follow_symlinks=False):
                    pendientes.append(Path(entrada.path))
    return tuple(sorted(encontradas))
