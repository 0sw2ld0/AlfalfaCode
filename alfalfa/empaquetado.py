"""AlfalfaCode dentro de `AlfalfaCode.app`.

`alfalfa-mac/build-app.sh` mete en la app un Python propio con este paquete en
`Contents/Resources/motor/python/lib/python3.X/site-packages/alfalfa`, y compila
allí mismo los ayudantes Swift (`firmante.swift`, `ayudante.swift`) en
`Contents/Helpers`, con el mismo nombre que tendrían en `$ALFALFA_HOME/bin`
(prefijo y huella de la fuente). Así quien instala la app desde el DMG no
necesita `swiftc` ni las herramientas de línea de órdenes.

Dentro de la app nada se escribe en el bundle: está firmado y notarizado, y
cambiar un archivo rompería la firma. Por eso `alfalfa --actualizar` no instala
nada en este modo (lo dice `actualizacion.py`).

La CLI suelta (`instalar.sh` o la fórmula de Homebrew) copia de la app del DMG sólo
`Resources/motor` y `Helpers`, con la misma forma, en una carpeta que no es un `.app`
(`~/.local/share/alfalfa` o `libexec` de Homebrew) y deja al lado `instalacion`, un
archivo de texto que dice quién la instaló (`curl` o `brew`) y qué versión.
"""
import os
from pathlib import Path

CARPETA_MOTOR = ('Resources', 'motor')
# Lo que escribe el `git` de sustitución de la app (build-app.sh) en un Mac sin las
# herramientas de línea de órdenes de Apple, donde /usr/bin/git abriría el diálogo de instalarlas.
SIN_GIT = 'AlfalfaCode: git no está disponible en este Mac'
# Lo deja instalar.sh (o la fórmula de Homebrew) junto a `Resources` y `Helpers`.
MARCA = 'instalacion'
QUIENES = ('curl', 'brew')


def contenidos(paquete=None):
    """La carpeta con `Resources/motor` y `Helpers`: `Contents` de la app, o la de la CLI
    instalada aparte (la que lleva `instalacion`). None en un clon o un site-packages."""
    paquete = Path(paquete or Path(__file__).resolve().parent)
    for carpeta in paquete.parents:
        if carpeta.name == CARPETA_MOTOR[1] and carpeta.parent.name == CARPETA_MOTOR[0]:
            raiz = carpeta.parent.parent
            if raiz.name == 'Contents' and raiz.parent.suffix == '.app':
                return raiz
            if _quien(raiz):
                return raiz
            return None
    return None


def _quien(raiz):
    try:
        primera = (raiz / MARCA).read_text(encoding='utf-8').split()
    except (OSError, UnicodeDecodeError):
        return None
    return primera[0] if primera and primera[0] in QUIENES else None


def instalacion(paquete=None):
    """'app' dentro de AlfalfaCode.app, 'curl' o 'brew' en la CLI suelta, None si no."""
    raiz = contenidos(paquete)
    if raiz is None:
        return None
    return 'app' if raiz.name == 'Contents' and raiz.parent.suffix == '.app' else _quien(raiz)


def en_la_app(paquete=None):
    return instalacion(paquete) == 'app'


def empaquetado(paquete=None):
    """¿Es una copia del motor de la app (dentro de ella o copiado de su DMG)? Sin git ni pip."""
    return contenidos(paquete) is not None


def ayudante(nombre, paquete=None):
    """Ruta del ayudante ya compilado `<contenidos>/Helpers/<nombre>`, o None si no está.

    `nombre` lleva la huella de la fuente: si la fuente del paquete no es la que
    se compiló (no debería pasar dentro de una app firmada), no coincide y se
    vuelve a compilar como en un clon.
    """
    raiz = contenidos(paquete)
    if raiz is None:
        return None
    ruta = raiz / 'Helpers' / nombre
    return ruta if ruta.is_file() and os.access(ruta, os.X_OK) else None
