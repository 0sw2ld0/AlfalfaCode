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

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        return httpx.AsyncClient(base_url=self.url(), headers=self.cabeceras(), timeout=timeout,
                                 trust_env=False)

    def ficha(self, modelo):
        """La de Ollama Cloud en el catálogo, para los modelos de la nube; los locales no tienen."""
        from .. import catalogo
        nombre = str(modelo or '')
        try:
            host = urlparse(self.url()).hostname or ''
        except ValueError:
            host = ''
        nube = nombre.endswith('cloud') or host == 'ollama.com' or host.endswith('.ollama.com')
        return catalogo.ficha('ollama-cloud', nombre) if nube else None

    def modelos(self):
        from .. import catalogo
        from . import con_prefijo
        with cliente_de_lista() as cliente:
            respuesta = cliente.get(self.url() + '/api/tags', headers=self.cabeceras())
            respuesta.raise_for_status()
            filas = respuesta.json()['models']
        resultado = []
        for fila in filas:
            nombre = fila.get('name', '')
            # Ollama Cloud se paga con su plan: del catálogo sólo se toma la ventana.
            resumen = {k: v for k, v in catalogo.resumen(self.ficha(nombre)).items() if k != 'precio'}
            # Lo que dice Ollama manda sobre el catálogo.
            propia = (fila.get('details') or {}).get('context_length') if isinstance(fila.get('details'), dict) \
                else None
            if type(propia) is int and propia > 0:
                resumen['ventana'] = propia
            resultado.append(dict(fila, name=con_prefijo(self.id, nombre), proveedor=self.id, nombre=nombre,
                                  **resumen))
        return resultado

    def estado(self):
        from .. import ajustes
        return dict(super().estado(), url=ajustes.ollama_url(), clave=bool(self.clave()))
