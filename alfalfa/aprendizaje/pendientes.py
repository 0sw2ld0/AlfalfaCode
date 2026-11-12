"""Lo que propone la revisión al cerrar el turno y espera al usuario.

Cada propuesta es un JSON en $ALFALFA_HOME/aprendido/pendientes/<id>.json con su diff.
Aplicarla (la tarjeta de la app, `{"tipo":"aprendizaje"}` por --stdio o
`alfalfa --aprendido aplicar ID`) vuelve a validarla contra lo que hay ahora: si
el almacén o la habilidad cambiaron desde la propuesta, se rechaza.

El archivo de una propuesta no es de fiar por sí solo (el hogar está protegido del
sandbox, pero no de todo): el diff que se enseña y lo que se aplica se recalculan
siempre desde sus `operaciones` o su `cambio`, y si no coinciden con el diff guardado
la propuesta se descarta. Lo que se aplica pasa las mismas validaciones y el escáner.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from . import hogar
from .habilidades import Habilidades
from .memoria import Memoria
from .rutas import escribir_privado, leer

ID = re.compile(r'^[0-9a-f]{12}$')
MAX_PENDIENTES = 50


class ErrorDePendiente(ValueError):
    pass


def huella(texto):
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


class Pendientes:
    def __init__(self, home=None):
        self.home = hogar(home)
        self.base = self.home / 'aprendido' / 'pendientes'

    def _ruta(self, ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ErrorDePendiente('Id de propuesta inválido.')
        return self.base / (ident + '.json')

    def guardar(self, propuesta):
        if len(self.listar()) >= MAX_PENDIENTES:
            raise ErrorDePendiente('Hay demasiadas propuestas sin revisar; aplica o descarta alguna.')
        ident = uuid4().hex[:12]
        datos = dict(propuesta, id=ident, creada=time.time())
        escribir_privado(self.home, self.base / (ident + '.json'), json.dumps(datos, ensure_ascii=False))
        return datos

    def leer(self, ident):
        texto = leer(self.home, self._ruta(ident), None)
        if not texto:
            raise ErrorDePendiente('No hay ninguna propuesta ' + str(ident) + '.')
        return json.loads(texto)

    def guardar_memoria(self, cwd, almacen, operaciones, **extra):
        """Una propuesta de memoria con su base y su diff calculados aquí."""
        memoria = Memoria(self.home, cwd)
        base = memoria.texto(almacen)
        entradas, _ = memoria.planear(almacen, operaciones)
        return self.guardar(dict(extra, tipo='memoria', almacen=almacen, operaciones=operaciones, base=huella(base),
                                 diff=memoria.diff(almacen, entradas), cwd=str(cwd)))

    def verificar(self, propuesta):
        """El diff recalculado desde lo que de verdad se aplicaría; ErrorDePendiente si ya no vale o no
        coincide con el guardado (una propuesta falsificada que enseña un diff y aplica otra cosa)."""
        cwd = Path(str(propuesta.get('cwd') or ''))
        if not cwd.is_absolute():
            raise ErrorDePendiente('Propuesta sin carpeta.')
        try:
            if propuesta.get('tipo') == 'memoria':
                memoria = Memoria(self.home, cwd)
                almacen = propuesta.get('almacen')
                if huella(memoria.texto(almacen)) != propuesta.get('base'):
                    raise ErrorDePendiente('La memoria ' + str(almacen) + ' cambió desde la propuesta: se descarta.')
                entradas, _ = memoria.planear(almacen, propuesta.get('operaciones'))
                diff = memoria.diff(almacen, entradas)
            elif propuesta.get('tipo') == 'habilidad':
                diff = Habilidades(self.home, cwd).verificar_cambio(propuesta.get('cambio'))
            else:
                raise ErrorDePendiente('Propuesta de un tipo desconocido.')
        except ErrorDePendiente:
            raise
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            raise ErrorDePendiente('La propuesta ya no vale: ' + str(exc)) from None
        if not diff or diff != propuesta.get('diff'):
            raise ErrorDePendiente('La propuesta no coincide con su diff: se descarta.')
        return diff

    def listar(self):
        """Las propuestas que siguen valiendo; las que no (cambió su base o no coinciden con su diff)
        se descartan."""
        if not self.base.is_dir() or self.base.is_symlink():
            return []
        filas = []
        for ruta in sorted(self.base.glob('*.json')):
            try:
                propuesta = self.leer(ruta.stem)
                self.verificar(propuesta)
            except ErrorDePendiente:
                try:
                    self.descartar(ruta.stem)
                except (OSError, ValueError):
                    pass
                continue
            except (OSError, ValueError):
                continue
            filas.append(propuesta)
        return sorted(filas, key=lambda f: f.get('creada', 0))

    def descartar(self, ident):
        ruta = self._ruta(ident)
        if not ruta.exists():
            raise ErrorDePendiente('No hay ninguna propuesta ' + ident + '.')
        ruta.unlink()

    def aplicar(self, ident, con_tocados=False):
        """Aplica la propuesta y la quita de la lista. Devuelve un texto para el usuario (y, con
        `con_tocados`, las rutas que escribió)."""
        tocados = []
        propuesta = self.leer(ident)
        try:
            self.verificar(propuesta)
        except ErrorDePendiente:
            self.descartar(ident)
            raise
        cwd = Path(propuesta['cwd'])
        if propuesta.get('tipo') == 'memoria':
            memoria = Memoria(self.home, cwd)
            almacen = propuesta['almacen']
            memoria.aplicar(almacen, propuesta['operaciones'])
            texto = 'Guardado en la memoria ' + almacen + '.'
        elif propuesta.get('tipo') == 'habilidad':
            cambio = propuesta['cambio']
            tocados = Habilidades(self.home, cwd).aplicar(cambio, propuesta.get('origen')).get('tocados', [])
            texto = ('Borrada' if cambio['accion'] == 'borrar' else 'Guardada') + ' la habilidad ' \
                + cambio['alcance'] + ':' + cambio['nombre'] + '.'
        else:
            raise ErrorDePendiente('Propuesta de un tipo desconocido.')
        self.descartar(ident)
        return (texto, tocados) if con_tocados else texto


def para_el_cliente(propuesta):
    """Lo que viaja en el evento `aprendizaje_propuesto` y en `--aprendido pendientes --json`."""
    # `clase` (memoria o habilidad): `tipo` es el del sobre del protocolo.
    datos = {k: propuesta.get(k) for k in ('id', 'motivo', 'diff', 'sesion', 'turno', 'creada')}
    datos['clase'] = propuesta.get('tipo')
    if propuesta.get('tipo') == 'memoria':
        datos['almacen'] = propuesta.get('almacen')
    else:
        cambio = propuesta.get('cambio') or {}
        datos.update(accion=cambio.get('accion'), alcance=cambio.get('alcance'), nombre=cambio.get('nombre'))
    return {k: v for k, v in datos.items() if v is not None}
