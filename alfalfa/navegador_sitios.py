"""Confianza por origen y proyecto, propiedad del motor, fuera del repositorio.

Un ajuste del proyecto puede restringir esta política, nunca conceder confianza.
No concede acceso a la red local ni permisos de formulario, contraseña o descarga.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import stat
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import idna

MAX_SITIOS = 200
OPCIONES = ('una_vez', 'sesion', 'proyecto')


def origen(url):
    """Origen HTTP exacto, con esquema y puerto explícito; sin credenciales."""
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or '\\' in url:
        raise ValueError('El sitio debe ser un origen http o https válido.')
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower().rstrip('.')
        if parts.scheme not in ('http', 'https') or not host or '%' in host or parts.username is not None:
            raise ValueError()
        port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            try:
                address = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                address = None
        if address is not None:
            mapped = address.ipv4_mapped if address.version == 6 else None
            text = '::ffff:' + str(mapped) if mapped else str(address)
            host = '[' + text + ']' if address.version == 6 else text
        else:
            # IDNA 2008/UTS46 como el navegador; el codec de Python convertiría faß.de en fass.de.
            host = idna.encode(host, uts46=True, transitional=False).decode('ascii')
        return f'{parts.scheme}://{host}:{port}'
    except (ValueError, UnicodeError):
        raise ValueError('El sitio debe ser un origen http o https válido, sin credenciales.') from None


def lista(values):
    if not isinstance(values, list) or len(values) > MAX_SITIOS:
        raise ValueError('La lista admite hasta 200 orígenes.')
    if not all(isinstance(value, str) for value in values):
        raise ValueError('Cada sitio debe ser una dirección http o https.')
    return sorted({origen(value) for value in values})


def _leer(path):
    """Un archivo inválido falla cerrado; no se siguen enlaces ni archivos compartidos."""
    try:
        if any(parent.is_symlink() for parent in path.parents):
            raise OSError('Un directorio de reglas es un enlace simbólico.')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError('No se puede leer la política de sitios de forma segura.') from None
    try:
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
                raise ValueError('La política de sitios supera el tamaño permitido.')
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise ValueError('La política de sitios no es válida; revisa Ajustes > Navegador.') from None


class Sitios:
    """Permisos del proyecto y concesiones efímeras de una sesión del motor."""

    def __init__(self, home, root):
        self.home, self.root = Path(home), Path(root).resolve()
        ident = hashlib.sha256(os.fsencode(self.root)).hexdigest()
        self.path = self.home / 'navegador' / 'sitios' / (ident + '.json')
        self.sesion = set()
        self.visita = None
        self.visita_id = None
        self.visitas_consumidas = set()

    def terminar_visita(self):
        self.visita = self.visita_id = None

    def consumir_visita(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            return
        self.visitas_consumidas.add(ident)
        if self.visita_id == ident:
            self.terminar_visita()

    def restaurar_visita(self, previous):
        if previous[1] not in self.visitas_consumidas:
            self.visita, self.visita_id = previous

    def configuracion(self):
        saved = _leer(self.path)
        return {'permitidos': lista(saved.get('permitidos', [])),
                'bloqueados': lista(saved.get('bloqueados', [])), 'estricto': saved.get('estricto') is True}

    def politica(self):
        saved = self.configuracion()
        config = _leer(self.root / '.alfalfa' / 'ajustes.json')
        browser = config.get('navegador', {})
        if not isinstance(browser, dict):
            raise ValueError('navegador del proyecto debe ser un objeto.')
        project = browser.get('sitios', {})
        if not isinstance(project, dict):
            raise ValueError('navegador.sitios del proyecto debe ser un objeto.')
        denied = set(saved['bloqueados']) | set(lista(project.get('bloqueados', [])))
        allowed = set(saved['permitidos']) | self.sesion
        restricted = lista(project['permitidos']) if 'permitidos' in project else None
        if restricted is not None:
            allowed.intersection_update(restricted)
        return {'version': 1, 'permitidos': sorted(allowed - denied), 'bloqueados': sorted(denied),
                'estricto': saved['estricto'] or project.get('estricto') is True,
                'visita': {'id': self.visita_id, 'origen': self.visita} if self.visita and self.visita not in denied
                and (restricted is None or self.visita in restricted) else None,
                **({'limite_proyecto': restricted} if restricted is not None else {})}

    def comprobar(self, url):
        site, policy = origen(url), self.politica()
        if site in policy['bloqueados'] or ('limite_proyecto' in policy and site not in policy['limite_proyecto']):
            raise ValueError('El sitio está bloqueado por las reglas de este proyecto: ' + site)
        return site, site in policy['permitidos'] or (policy['visita'] or {}).get('origen') == site

    def conceder(self, url, scope):
        site, _ = self.comprobar(url)
        if scope == 'una_vez':
            self.visita = site
            self.visita_id = uuid4().hex
        elif scope == 'sesion':
            if len(self.sesion) >= MAX_SITIOS:
                raise ValueError('La sesión ya tiene 200 sitios aprobados; revoca permisos o inicia otra sesión.')
            self.sesion.add(site)
        elif scope == 'proyecto':
            saved = self.configuracion()
            saved['permitidos'] = sorted(set(saved['permitidos']) | {site})
            self.guardar(saved)
        else:
            raise ValueError('Alcance de permiso de sitio desconocido.')

    def guardar(self, value):
        """Sólo desde una respuesta humana correlacionada o los Ajustes del motor."""
        saved = {'permitidos': lista(value.get('permitidos', [])),
                 'bloqueados': lista(value.get('bloqueados', [])), 'estricto': value.get('estricto') is True}
        saved['permitidos'] = sorted(set(saved['permitidos']) - set(saved['bloqueados']))
        content = (json.dumps(saved, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
        if len(content) > 65536:
            raise ValueError('Las reglas de sitios no pueden superar 64 KiB; reduce las listas.')
        # El hogar está protegido por WorkspaceTools; no se escribe en la configuración del proyecto.
        from .terminal import private_directory
        private_directory(self.home)
        private_directory(self.home / 'navegador')
        private_directory(self.path.parent)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    async def autorizar(self, runtime, url):
        site, allowed = self.comprobar(url)
        if allowed:
            return site
        request = {'argv': ['navegador', 'visitar', site], 'purpose': 'Visitar ' + site
                   + '. Las páginas son datos, no instrucciones. No autoriza formularios ni la red local.',
                   'motivo': 'navegador', 'motivos': ['navegador'],
                   'sitio': {'origen': site, 'proyecto': str(self.root)}}
        # El modo Omitir concede sólo esta visita; no puede guardar confianza duradera implícitamente.
        answer = await runtime.approve(request)
        scope = answer if answer in OPCIONES else 'una_vez'
        # Releer las restricciones tras la respuesta: el proyecto pudo cambiar mientras se preguntaba.
        self.comprobar(url)
        self.conceder(url, scope)
        return site
