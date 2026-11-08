"""Ollama, local o Ollama Cloud: el formato del motor sin traducir."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from . import Proveedor, cliente_de_lista

LOCALES = ('localhost', '127.0.0.1', '::1')


def validar_url(value, variable='ALFALFA_OLLAMA_URL', nombre='Ollama'):
    """La URL base sin barra final; sólo HTTPS salvo en este mismo equipo."""
    value = str(value or '').rstrip('/')
    url = urlparse(value)
    if url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError(f'La URL de {nombre} ({variable}) debe ser una URL base '
                         'sin credenciales ni ruta.')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in LOCALES):
        raise ValueError(f'{nombre} remoto requiere HTTPS.')
    return value


class Ollama(Proveedor):
    id = 'ollama'
    nombre = 'Ollama'

    def url(self):
        from .. import ajustes
        return validar_url(ajustes.ollama_url())

    def clave(self):
        from .. import ajustes
        return ajustes.ollama_clave()

    def secretos(self):
        return [self.clave()]

    def alcance(self):
        from .. import ajustes
        url = urlparse(ajustes.ollama_url())
        if url.hostname in LOCALES:
            return 'local'
        return 'remote' if url.scheme == 'https' and url.hostname else None

    def cabeceras(self):
        clave = self.clave()
        return {'Authorization': 'Bearer ' + clave} if clave else {}

    def cliente(self, timeout):
        return httpx.AsyncClient(base_url=self.url(), headers=self.cabeceras(), timeout=timeout,
                                 trust_env=False)

    def modelos(self):
        with cliente_de_lista() as cliente:
            respuesta = cliente.get(self.url() + '/api/tags', headers=self.cabeceras())
            respuesta.raise_for_status()
            filas = respuesta.json()['models']
        return [dict(fila, proveedor=self.id, nombre=fila.get('name', '')) for fila in filas]

    def estado(self):
        from .. import ajustes
        return dict(super().estado(), url=ajustes.ollama_url(), clave=bool(self.clave()))
