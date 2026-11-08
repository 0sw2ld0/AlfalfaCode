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
"""
import os
from pathlib import Path

CARPETA_MOTOR = ('Resources', 'motor')


def contenidos(paquete=None):
    """La carpeta `Contents` de la app si este paquete va dentro de una, o None."""
    paquete = Path(paquete or Path(__file__).resolve().parent)
    for carpeta in paquete.parents:
        if carpeta.name == CARPETA_MOTOR[1] and carpeta.parent.name == CARPETA_MOTOR[0]:
            raiz = carpeta.parent.parent
            if raiz.name == 'Contents' and raiz.parent.suffix == '.app':
                return raiz
            return None
    return None


def en_la_app(paquete=None):
    return contenidos(paquete) is not None


def ayudante(nombre, paquete=None):
    """Ruta del ayudante ya compilado `Contents/Helpers/<nombre>`, o None si no está.

    `nombre` lleva la huella de la fuente: si la fuente del paquete no es la que
    se compiló (no debería pasar dentro de una app firmada), no coincide y se
    vuelve a compilar como en un clon.
    """
    raiz = contenidos(paquete)
    if raiz is None:
        return None
    ruta = raiz / 'Helpers' / nombre
    return ruta if ruta.is_file() and os.access(ruta, os.X_OK) else None
