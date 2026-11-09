"""Qué servidores MCP y agentes importados autorizó el usuario: `$ALFALFA_HOME/importadas.json`.

Sólo guarda la clave de cada uno (tipo, nombre y archivo de origen), una huella de su
definición y si el servidor tiene red. La huella es un HMAC-SHA256 con una clave aleatoria de
este equipo: no deja recuperar ningún valor de la configuración (ni sus claves) y, si la
definición cambia (otra orden, otros argumentos, otro entorno), deja de valer y se vuelve a
preguntar, como `trust` de Codex por proyecto pero por elemento.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

ARCHIVO = 'importadas.json'


class Autorizaciones:
    def __init__(self, hogar):
        self.ruta = Path(hogar) / ARCHIVO
        self.error = None
        self.datos = self._leer()

    def _leer(self):
        vacio = {'autorizadas': {}}
        if not self.ruta.exists():
            return vacio
        try:
            if self.ruta.is_symlink() or not self.ruta.is_file() or self.ruta.stat().st_size > 1024 * 1024:
                raise ValueError('debe ser un archivo regular de hasta 1 MB')
            datos = json.loads(self.ruta.read_text(encoding='utf-8'))
            if not isinstance(datos, dict) or not isinstance(datos.get('autorizadas', {}), dict):
                raise ValueError('formato desconocido')
        except (OSError, ValueError) as exc:
            # Ilegible: nada cuenta como autorizado y no se sobrescribe hasta que se autorice algo.
            self.error = ARCHIVO + ' inválido (' + (str(exc) if not isinstance(exc, json.JSONDecodeError)
                                                     else 'JSON') + '): nada importado está autorizado.'
            return vacio
        datos.setdefault('autorizadas', {})
        return datos

    def _clave_hmac(self, crear):
        clave = self.datos.get('clave')
        if isinstance(clave, str) and len(clave) == 64:
            return bytes.fromhex(clave)
        if not crear:
            return None
        self.datos['clave'] = secrets.token_hex(32)
        return bytes.fromhex(self.datos['clave'])

    def huella(self, elemento, crear=False):
        clave = self._clave_hmac(crear)
        if clave is None:
            return None
        cuerpo = json.dumps([elemento.tipo, elemento.nombre, str(elemento.origen), elemento.crudo],
                            sort_keys=True, ensure_ascii=False, default=str)
        return hmac.new(clave, cuerpo.encode('utf-8'), hashlib.sha256).hexdigest()

    def registro(self, elemento):
        fila = self.datos['autorizadas'].get(elemento.clave)
        return fila if isinstance(fila, dict) else None

    def estado(self, elemento):
        """autorizado, cambio (se autorizó otra definición) o pendiente."""
        fila = self.registro(elemento)
        if fila is None:
            return 'pendiente'
        return 'autorizado' if hmac.compare_digest(str(fila.get('huella', '')),
                                                   self.huella(elemento) or '') else 'cambio'

    def autorizar(self, elemento, red=True):
        fila = {'huella': self.huella(elemento, crear=True)}
        if elemento.tipo == 'mcp':
            fila['red'] = bool(red)
        self.datos['autorizadas'][elemento.clave] = fila

    def retirar(self, elemento):
        return self.datos['autorizadas'].pop(elemento.clave, None) is not None

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporal = tempfile.mkstemp(dir=self.ruta.parent, prefix='.importadas-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as archivo:
                json.dump(self.datos, archivo, ensure_ascii=False, indent=2, sort_keys=True)
            os.chmod(temporal, 0o600)
            os.replace(temporal, self.ruta)
        except BaseException:
            Path(temporal).unlink(missing_ok=True)
            raise
        self.error = None
