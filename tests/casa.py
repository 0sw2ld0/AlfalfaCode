"""Un sitio de verdad fuera del sandbox, para las pruebas que comprueban que no se escribe ahí.

Antes usaban `Path.home()`. Con un HOME temporal (lo natural para aislar la suite, o el de una
sesión que exporta el suyo) la casa cuelga de $TMPDIR o de /private/tmp, donde el sandbox sí deja
escribir: esas pruebas fallaban sin probar nada. La casa de la base de usuarios no depende de $HOME.
"""
import os
import pwd
import tempfile
from pathlib import Path

# Las carpetas del fixture `fuera_del_sandbox` se crean en ~/Library/Caches con este prefijo. Otra suite
# que corra a la vez puede crear una en cualquier momento: quien vigile esa carpeta debe ignorarlas.
PREFIJO_FUERA_DEL_SANDBOX = 'alfalfa-prueba '


def casa_real():
    """La casa del usuario según la base de usuarios del sistema, no según $HOME."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def bajo_temporales(ruta):
    """¿La ruta, resuelta, cae en una carpeta temporal donde el sandbox deja escribir?"""
    real = os.path.realpath(ruta)
    raices = {os.path.realpath(tempfile.gettempdir()), os.path.realpath('/private/tmp'), os.path.realpath('/tmp')}
    return any(real == r or real.startswith(r.rstrip('/') + '/') for r in raices)


def base_fuera_del_sandbox():
    """~/Library/Caches de la casa real (o la casa), o None si también cuelga de un temporal."""
    casa = casa_real()
    base = casa / 'Library' / 'Caches'
    base = base if base.is_dir() else casa
    return None if bajo_temporales(base) or not base.is_dir() else base
