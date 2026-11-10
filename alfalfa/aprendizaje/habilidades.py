"""Habilidades aprendidas: SKILL.md que escribe AlfalfaCode, con su registro de origen.

- usuario: $ALFALFA_HOME/skills/aprendidas/<nombre>/SKILL.md (las descubre el cargador
  como cualquier habilidad de $ALFALFA_HOME/skills).
- proyecto: <raíz del repositorio>/.alfalfa/skills/<nombre>/SKILL.md.

El registro ($ALFALFA_HOME/aprendido/registro.json) es lo único que dice qué creó
AlfalfaCode: una carpeta que no está en él, o cuyo contenido cambió desde la última
escritura del arnés, no se toca. Una habilidad bloqueada tampoco (el usuario sí puede
editarla con `alfalfa --aprendido editar`).

Formato compatible con Claude Code: front matter con `name` (igual a la carpeta) y
`description`, y el cuerpo en Markdown. «Lecciones, no registros» (Hermes Agent): sin
fechas, números de PR ni trozos de conversación.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import clave_de_proyecto, hogar, raiz_de_proyecto
from .escaneo import exigir_limpio
from .memoria import diff
from .rutas import (
    RutaInvalida,
    borrar_archivo,
    clave_de_ruta,
    escribir,
    escribir_privado,
    leer,
    mover_carpeta,
    nombre_valido,
    relativa_valida,
    sin_enlaces,
)

ALCANCES = ('usuario', 'proyecto')
ACCIONES = ('crear', 'parchear', 'escribir_archivo', 'borrar')
MAX_SKILL = 24000
MAX_DESCRIPCION = 1024
MAX_ARCHIVOS = 30
MAX_APOYO = 48 * 1024
# La tarjeta enseña el diff entero: un cambio que no cabe se rechaza, no se recorta.
MAX_DIFF = 120000

# «Lecciones, no registros»: lo que convierte una habilidad en el acta de un día concreto.
_MESES = ('enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre'
          '|january|february|march|april|june|july|august|september|october|november|december')
_REGISTRO = [
    (re.compile(r'\b(?:19|20)\d\d[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b'), 'una fecha'),
    (re.compile(r'(?i)\b\d{1,2}\s+de\s+(?:' + _MESES + r')\b|\b(?:' + _MESES + r')\s+\d{1,2},?\s+(?:19|20)\d\d\b'),
     'una fecha'),
    (re.compile(r'(?i)\b(?:PR|MR|pull\s+request|merge\s+request)\s*#?\s*\d+|/pull/\d+|(?<![\w/&#])#\d{2,}\b'),
     'un número de PR o de incidencia'),
    (re.compile(r'(?im)^\s*(?:[>*-]\s*)?(?:usuario|user|assistant|asistente|claude|alfalfa|yo|tú|human)\s*:\s'),
     'un trozo de conversación'),
    (re.compile(r'(?i)\b(?:el usuario (?:me )?(?:dijo|pidió|escribió)|the user (?:said|asked|wrote)'
                r'|en esta (?:sesión|conversación)|in this (?:session|conversation)|hoy (?:he|hemos)'
                r'|today (?:I|we))\b'),
     'un relato de la sesión'),
]


class ErrorDeHabilidad(ValueError):
    pass


def sha(texto):
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


def front_matter(texto):
    """(name, description) del SKILL.md; ErrorDeHabilidad si no tiene el formato de Claude Code."""
    lineas = texto.splitlines()
    if not lineas or lineas[0].strip() != '---':
        raise ErrorDeHabilidad('SKILL.md empieza con front matter: «---», name: …, description: …, «---».')
    datos, cerrado = {}, False
    for linea in lineas[1:40]:
        if linea.strip() == '---':
            cerrado = True
            break
        if ':' in linea and not linea[:1].isspace():
            clave, valor = linea.split(':', 1)
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in '\'"':
                valor = valor[1:-1]
            datos[clave.strip()] = valor
    if not cerrado:
        raise ErrorDeHabilidad('El front matter de SKILL.md no se cierra con «---».')
    return datos.get('name', ''), datos.get('description', '')


def revisar_skill(nombre, texto, lecciones=True):
    """Valida un SKILL.md completo para la habilidad `nombre`."""
    if not isinstance(texto, str) or not texto.strip():
        raise ErrorDeHabilidad('contenido es el SKILL.md completo.')
    if len(texto) > MAX_SKILL:
        raise ErrorDeHabilidad('SKILL.md pasa de ' + str(MAX_SKILL) + ' caracteres: deja SKILL.md como índice y '
                               'lleva el detalle a archivos de references/ con escribir_archivo.')
    nombre_fm, descripcion = front_matter(texto)
    if nombre_fm != nombre:
        raise ErrorDeHabilidad('El name del front matter tiene que ser «' + nombre + '», como la carpeta.')
    if not descripcion or len(descripcion) > MAX_DESCRIPCION:
        raise ErrorDeHabilidad('description dice en una o dos frases cuándo usar la habilidad (como mucho '
                               + str(MAX_DESCRIPCION) + ' caracteres).')
    if lecciones:
        revisar_lecciones(texto)
    return descripcion


def revisar_lecciones(texto):
    for patron, que in _REGISTRO:
        encontrado = patron.search(texto)
        if encontrado:
            raise ErrorDeHabilidad('Lecciones, no registros: la habilidad tiene ' + que + ' («'
                                   + encontrado.group(0).strip()[:60] + '»). Escribe el procedimiento que vale la '
                                   'próxima vez, sin fechas, números de PR ni trozos de conversación.')


class Habilidades:
    def __init__(self, home=None, cwd=None):
        self.home = hogar(home)
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.proyecto = raiz_de_proyecto(self.cwd)
        self.clave_proyecto = clave_de_proyecto(self.cwd)

    # --- Dónde vive cada una ------------------------------------------------------------

    def raiz(self, alcance):
        """(raíz de seguridad, carpeta de las habilidades aprendidas) del alcance."""
        if alcance == 'usuario':
            return self.home, self.home / 'skills' / 'aprendidas'
        if alcance == 'proyecto':
            if self._proyecto_es_el_hogar():
                raise ErrorDeHabilidad('Esta carpeta es tu carpeta personal: su .alfalfa/skills sería el de todas tus '
                                       'sesiones. Guarda la habilidad con alcance usuario.')
            return self.proyecto, self.proyecto / '.alfalfa' / 'skills'
        raise ErrorDeHabilidad('alcance es usuario o proyecto.')

    def _proyecto_es_el_hogar(self):
        try:
            casa = Path.home().resolve()
        except (OSError, RuntimeError):
            casa = None
        alfalfa = (self.proyecto / '.alfalfa').resolve(strict=False)
        return self.proyecto == casa or alfalfa == self.home.resolve(strict=False) \
            or self.home.resolve(strict=False).is_relative_to(alfalfa)

    def carpeta(self, alcance, nombre):
        raiz, base = self.raiz(alcance)
        carpeta = base / nombre_valido(nombre)
        sin_enlaces(raiz, carpeta)
        return raiz, carpeta

    def clave(self, alcance, nombre, proyecto=None):
        if alcance == 'usuario':
            return 'usuario:' + nombre
        return 'proyecto:' + (proyecto or self.clave_proyecto) + ':' + nombre

    # --- Registro -------------------------------------------------------------------------

    def _ruta_registro(self):
        return self.home / 'aprendido' / 'registro.json'

    def registro(self):
        try:
            texto = leer(self.home, self._ruta_registro(), None)
            datos = json.loads(texto) if texto else {}
        except (OSError, ValueError, RutaInvalida):
            datos = {}
        filas = datos.get('habilidades') if isinstance(datos, dict) else None
        return {k: v for k, v in (filas or {}).items() if isinstance(v, dict)}

    def _guardar_registro(self, filas):
        escribir_privado(self.home, self.home / 'aprendido' / 'registro.json',
                         json.dumps({'habilidades': filas}, ensure_ascii=False, indent=2) + '\n')

    def fila(self, alcance, nombre):
        return self.registro().get(self.clave(alcance, nombre))

    def resolver(self, nombre):
        """`usuario:x`, `proyecto:x` (de esta carpeta) o `x` si sólo hay una: (alcance, nombre)."""
        if not isinstance(nombre, str) or not nombre:
            raise ErrorDeHabilidad('Indica la habilidad.')
        if ':' in nombre:
            alcance, nombre = nombre.split(':', 1)
            if alcance not in ALCANCES:
                raise ErrorDeHabilidad('Usa usuario:NOMBRE o proyecto:NOMBRE.')
            if self.fila(alcance, nombre) is None:
                raise ErrorDeHabilidad('No hay ninguna habilidad aprendida ' + alcance + ':' + nombre + '.')
            return alcance, nombre
        hay = [a for a in ALCANCES if self.fila(a, nombre) is not None]
        if not hay:
            raise ErrorDeHabilidad('No hay ninguna habilidad aprendida «' + nombre + '» (de ti o de esta carpeta).')
        if len(hay) > 1:
            raise ErrorDeHabilidad('Hay dos «' + nombre + '»: usa usuario:' + nombre + ' o proyecto:' + nombre + '.')
        return hay[0], nombre

    # --- Leer -------------------------------------------------------------------------------

    def archivos_actuales(self, alcance, nombre):
        """{ruta relativa: texto} de lo que hay ahora en la carpeta (sólo archivos normales)."""
        raiz, carpeta = self.carpeta(alcance, nombre)
        if not carpeta.is_dir():
            return {}
        archivos = {}
        for ruta in sorted(carpeta.rglob('*')):
            relativa = ruta.relative_to(carpeta)
            if ruta.is_symlink() or any(p.startswith('.') for p in relativa.parts):
                archivos[relativa.as_posix()] = None  # no es nuestro: bloquea cualquier cambio
                continue
            if ruta.is_file():
                archivos[relativa.as_posix()] = leer(raiz, ruta)
        return archivos

    def estado(self, fila, clave):
        """Fila del listado: la del registro y cómo está ahora en el disco."""
        alcance = fila.get('alcance')
        datos = {k: fila.get(k) for k in ('alcance', 'nombre', 'descripcion', 'sesion', 'turno', 'origen',
                                          'creada', 'actualizada', 'bloqueada', 'proyecto')}
        datos['clave'] = clave
        datos['bloqueada'] = bool(fila.get('bloqueada'))
        datos['ruta'] = fila.get('ruta')
        datos['de_esta_carpeta'] = alcance == 'usuario' or clave.split(':')[1] == self.clave_proyecto
        try:
            ruta = Path(fila.get('ruta') or '')
            raiz = self.home if alcance == 'usuario' else Path(fila.get('proyecto') or '')
            sin_enlaces(raiz, ruta)
            if not (ruta / 'SKILL.md').is_file():
                datos['estado'] = 'falta'
            else:
                actual = leer(raiz, ruta / 'SKILL.md')
                datos['estado'] = 'al_dia' if sha(actual) == (fila.get('archivos') or {}).get('SKILL.md') \
                    else 'cambiada'
        except (OSError, ValueError, UnicodeError):
            datos['estado'] = 'ilegible'
        return datos

    def listar(self):
        filas = self.registro()
        return [self.estado(fila, clave) for clave, fila in sorted(filas.items())]

    def ver(self, alcance, nombre):
        fila = self.fila(alcance, nombre)
        if fila is None:
            raise ErrorDeHabilidad('No es una habilidad aprendida.')
        return dict(self.estado(fila, self.clave(alcance, nombre)),
                    archivos=self.archivos_actuales(alcance, nombre))

    # --- Planear cambios (sin escribir) ---------------------------------------------------------

    def _propia(self, alcance, nombre, para_el_agente=True):
        """La fila si la creó AlfalfaCode, sigue como la dejó y (para el agente) no está bloqueada."""
        fila = self.fila(alcance, nombre)
        if fila is None:
            raise ErrorDeHabilidad('«' + nombre + '» no la creó AlfalfaCode (o no existe en ' + alcance + '): no se '
                                   'toca. Crea otra con otro nombre.')
        if para_el_agente and fila.get('bloqueada'):
            raise ErrorDeHabilidad('El usuario bloqueó la habilidad «' + nombre + '»: no se cambia.')
        guardados = fila.get('archivos') or {}
        actuales = self.archivos_actuales(alcance, nombre)
        if para_el_agente and ({k: sha(v) if v is not None else None for k, v in actuales.items()} != guardados):
            raise ErrorDeHabilidad('La habilidad «' + nombre + '» cambió fuera de AlfalfaCode desde que la escribió: '
                                   'no se toca. El usuario puede editarla con alfalfa --aprendido editar.')
        return fila, actuales

    def planear(self, accion, alcance, nombre, contenido=None, buscar=None, reemplazar=None, archivo=None,
                lecciones=True):
        """El cambio que haría la herramienta: {accion, alcance, nombre, antes, despues, diff}."""
        if accion not in ACCIONES:
            raise ErrorDeHabilidad('accion es crear, parchear, escribir_archivo o borrar.')
        # Saltos de Windows (CRLF) valen igual: se guardan como en el resto del repositorio.
        contenido, buscar, reemplazar = (v.replace('\r\n', '\n') if isinstance(v, str) else v
                                         for v in (contenido, buscar, reemplazar))
        if alcance not in ALCANCES:
            raise ErrorDeHabilidad('alcance es usuario o proyecto.')
        raiz, carpeta = self.carpeta(alcance, nombre)
        if accion == 'crear':
            if self.fila(alcance, nombre) is not None or carpeta.exists() or carpeta.is_symlink():
                raise ErrorDeHabilidad('Ya existe una habilidad «' + nombre + '» en ' + alcance + ': no se '
                                       'sobrescribe. Usa parchear si es tuya, o elige otro nombre.')
            if contenido is None or buscar is not None or reemplazar is not None or archivo is not None:
                raise ErrorDeHabilidad('crear lleva nombre, alcance y contenido (el SKILL.md completo).')
            descripcion = revisar_skill(nombre, contenido, lecciones)
            exigir_limpio(contenido, 'La habilidad')
            antes, despues = {}, {'SKILL.md': contenido}
        else:
            fila, antes = self._propia(alcance, nombre)
            descripcion = fila.get('descripcion', '')
            despues = dict(antes)
            if accion == 'parchear':
                if archivo is not None:
                    raise ErrorDeHabilidad('parchear cambia SKILL.md; para otro archivo usa escribir_archivo.')
                actual = antes.get('SKILL.md') or ''
                if contenido is not None:
                    if buscar is not None or reemplazar is not None:
                        raise ErrorDeHabilidad('parchear lleva contenido (reescribirla entera) o buscar y '
                                               'reemplazar, no ambos.')
                    nuevo = contenido
                else:
                    if not isinstance(buscar, str) or not buscar or not isinstance(reemplazar, str):
                        raise ErrorDeHabilidad('parchear necesita buscar y reemplazar (o contenido).')
                    if actual.count(buscar) != 1:
                        raise ErrorDeHabilidad('buscar tiene que aparecer exactamente una vez en SKILL.md '
                                               '(aparece ' + str(actual.count(buscar)) + ').')
                    nuevo = actual.replace(buscar, reemplazar, 1)
                descripcion = revisar_skill(nombre, nuevo, lecciones)
                exigir_limpio(nuevo, 'La habilidad')
                despues['SKILL.md'] = nuevo
            elif accion == 'escribir_archivo':
                relativa = relativa_valida(archivo).as_posix()
                # APFS no distingue mayúsculas: «skill.md» o «References/A.md» pisarían otro archivo.
                parecidas = [k for k in antes if clave_de_ruta(k) == clave_de_ruta(relativa) and k != relativa]
                if clave_de_ruta(relativa) == clave_de_ruta('SKILL.md') or parecidas:
                    raise ErrorDeHabilidad('Ese nombre coincide con ' + (parecidas[0] if parecidas else 'SKILL.md')
                                           + ' sin distinguir mayúsculas; SKILL.md se cambia con parchear.')
                if not isinstance(contenido, str) or buscar is not None or reemplazar is not None:
                    raise ErrorDeHabilidad('escribir_archivo lleva archivo y contenido.')
                if len(contenido.encode('utf-8')) > MAX_APOYO:
                    raise ErrorDeHabilidad('Un archivo de apoyo pasa de 48 KB: divídelo en varios.')
                if relativa not in antes and len(antes) >= MAX_ARCHIVOS:
                    raise ErrorDeHabilidad('Demasiados archivos en la habilidad.')
                if lecciones:
                    revisar_lecciones(contenido)
                exigir_limpio(contenido, 'El archivo')
                sin_enlaces(raiz, carpeta / relativa)
                despues[relativa] = contenido
            else:
                if any(v is not None for v in (contenido, buscar, reemplazar, archivo)):
                    raise ErrorDeHabilidad('borrar sólo lleva nombre y alcance.')
                despues = {}
        if despues == antes:
            raise ErrorDeHabilidad('El cambio no cambia nada.')
        cambio = {'accion': accion, 'alcance': alcance, 'nombre': nombre, 'descripcion': descripcion,
                  'carpeta': str(carpeta), 'antes': antes, 'despues': despues}
        cambio['diff'] = self.diff_de(cambio)
        if len(cambio['diff']) > MAX_DIFF:
            raise ErrorDeHabilidad('El cambio es demasiado grande para enseñarlo entero en la tarjeta de permiso '
                                   '(' + str(len(cambio['diff'])) + ' caracteres de diff): divídelo en cambios '
                                   'más pequeños.')
        return cambio

    def verificar_cambio(self, cambio):
        """Valida un cambio guardado (una propuesta pendiente) como si lo acabara de planear el agente y
        devuelve su diff recalculado; ErrorDeHabilidad si algo no cuadra."""
        if not isinstance(cambio, dict):
            raise ErrorDeHabilidad('Cambio inválido.')
        accion, alcance, nombre = cambio.get('accion'), cambio.get('alcance'), cambio.get('nombre')
        antes, despues = cambio.get('antes'), cambio.get('despues')
        if accion not in ACCIONES or alcance not in ALCANCES or not isinstance(antes, dict) \
                or not isinstance(despues, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                            for k, v in despues.items()):
            raise ErrorDeHabilidad('Cambio inválido.')
        _, carpeta = self.carpeta(alcance, nombre)
        if str(carpeta) != cambio.get('carpeta'):
            raise ErrorDeHabilidad('La habilidad propuesta era de otra carpeta.')
        if accion == 'crear':
            if antes or self.fila(alcance, nombre) is not None or carpeta.exists() or carpeta.is_symlink():
                raise ErrorDeHabilidad('Ya existe una habilidad «' + nombre + '».')
        else:
            _, actuales = self._propia(alcance, nombre)
            if actuales != antes:
                raise ErrorDeHabilidad('La habilidad cambió desde la propuesta.')
        if accion == 'borrar' and despues:
            raise ErrorDeHabilidad('Cambio inválido.')
        if accion != 'borrar' and 'SKILL.md' not in despues:
            raise ErrorDeHabilidad('Falta SKILL.md.')
        for relativa, texto in despues.items():
            if antes.get(relativa) == texto:
                continue
            if relativa == 'SKILL.md':
                revisar_skill(nombre, texto)
            else:
                relativa_valida(relativa)
                if len(texto.encode('utf-8')) > MAX_APOYO:
                    raise ErrorDeHabilidad('Un archivo de apoyo pasa de 48 KB.')
                revisar_lecciones(texto)
            exigir_limpio(texto, 'La habilidad')
        diff = self.diff_de(cambio)
        if diff != cambio.get('diff') or len(diff) > MAX_DIFF:
            raise ErrorDeHabilidad('El cambio no coincide con su diff.')
        return diff

    def diff_de(self, cambio):
        prefijo = ('.alfalfa/skills/' if cambio['alcance'] == 'proyecto' else 'skills/aprendidas/') + cambio['nombre']
        partes = []
        for relativa in sorted(set(cambio['antes']) | set(cambio['despues'])):
            antes, despues = cambio['antes'].get(relativa) or '', cambio['despues'].get(relativa) or ''
            if antes != despues:
                partes.append(diff(antes.rstrip('\n'), despues.rstrip('\n'), prefijo + '/' + relativa))
        return ''.join(partes)

    # --- Escribir --------------------------------------------------------------------------------

    def aplicar(self, cambio, origen=None, para_el_agente=True):
        """Escribe un cambio de `planear`, comprobando antes que nada cambió desde entonces."""
        alcance, nombre = cambio['alcance'], cambio['nombre']
        raiz, carpeta = self.carpeta(alcance, nombre)
        if str(carpeta) != cambio.get('carpeta'):
            raise ErrorDeHabilidad('La habilidad propuesta era de otra carpeta.')
        if cambio['accion'] == 'crear':
            if self.fila(alcance, nombre) is not None or carpeta.exists() or carpeta.is_symlink():
                raise ErrorDeHabilidad('Ya existe una habilidad «' + nombre + '»: no se sobrescribe.')
        else:
            _, actuales = self._propia(alcance, nombre, para_el_agente)
            if actuales != cambio['antes']:
                raise ErrorDeHabilidad('La habilidad cambió desde la propuesta; vuelve a proponerla.')
        filas = self.registro()
        clave = self.clave(alcance, nombre)
        tocados = sorted(r for r in set(cambio['antes']) | set(cambio['despues'])
                         if cambio['antes'].get(r) != cambio['despues'].get(r))
        if cambio['accion'] == 'borrar':
            if carpeta.exists():
                papelera = self.home / 'aprendido' / 'papelera'
                mover_carpeta(raiz, carpeta, self.home,
                              papelera / (str(int(time.time())) + '-' + alcance + '-' + nombre))
            filas.pop(clave, None)
            self._guardar_registro(filas)
            return {'borrada': True, 'clave': clave, 'tocados': [str(carpeta / r) for r in tocados]}
        for relativa, texto in cambio['despues'].items():
            if cambio['antes'].get(relativa) != texto:
                escribir(raiz, carpeta / relativa, texto, modo=0o600 if alcance == 'usuario' else 0o644)
        for relativa in set(cambio['antes']) - set(cambio['despues']):
            borrar_archivo(raiz, carpeta / relativa)
        ahora = time.time()
        anterior = filas.get(clave) or {}
        fila = dict(anterior, alcance=alcance, nombre=nombre, descripcion=cambio.get('descripcion', ''),
                    ruta=str(carpeta), archivos={k: sha(v) for k, v in cambio['despues'].items()},
                    actualizada=ahora, bloqueada=bool(anterior.get('bloqueada')))
        if alcance == 'proyecto':
            fila['proyecto'] = str(self.proyecto)
        if not anterior:
            fila['creada'] = ahora
            for campo in ('sesion', 'turno', 'origen'):
                if origen and origen.get(campo):
                    fila[campo] = origen[campo]
        filas[clave] = fila
        self._guardar_registro(filas)
        return {'guardada': True, 'clave': clave, 'ruta': str(carpeta / 'SKILL.md'),
                'tocados': [str(carpeta / r) for r in tocados]}

    # --- Lo que hace el usuario ------------------------------------------------------------------

    def bloquear(self, alcance, nombre, bloqueada=True):
        filas = self.registro()
        clave = self.clave(alcance, nombre)
        if clave not in filas:
            raise ErrorDeHabilidad('No es una habilidad aprendida.')
        filas[clave]['bloqueada'] = bool(bloqueada)
        self._guardar_registro(filas)
        return self.estado(filas[clave], clave)

    def editar(self, alcance, nombre, contenido):
        """El usuario reescribe SKILL.md: vale aunque esté bloqueada o la cambiara a mano."""
        fila = self.fila(alcance, nombre)
        if fila is None:
            raise ErrorDeHabilidad('No es una habilidad aprendida.')
        antes = self.archivos_actuales(alcance, nombre)
        if any(v is None for v in antes.values()):
            raise ErrorDeHabilidad('La carpeta tiene enlaces o archivos ocultos: arréglala a mano.')
        contenido = str(contenido or '').replace('\r\n', '\n')
        descripcion = revisar_skill(nombre, contenido, lecciones=False)
        cambio = {'accion': 'parchear', 'alcance': alcance, 'nombre': nombre, 'descripcion': descripcion,
                  'carpeta': str(self.carpeta(alcance, nombre)[1]), 'antes': antes,
                  'despues': dict(antes, **{'SKILL.md': contenido})}
        return self.aplicar(cambio, para_el_agente=False)

    def borrar(self, alcance, nombre):
        """El usuario la borra (también bloqueada o cambiada a mano): va a la papelera del hogar."""
        if self.fila(alcance, nombre) is None:
            raise ErrorDeHabilidad('No es una habilidad aprendida.')
        antes = self.archivos_actuales(alcance, nombre)
        cambio = {'accion': 'borrar', 'alcance': alcance, 'nombre': nombre,
                  'carpeta': str(self.carpeta(alcance, nombre)[1]), 'antes': antes, 'despues': {}}
        return self.aplicar(cambio, para_el_agente=False)

    def resumen_para_el_prompt(self):
        """Las aprendidas que el agente puede cambiar, para la revisión al cerrar el turno."""
        return [{'alcance': f['alcance'], 'nombre': f['nombre'], 'descripcion': (f.get('descripcion') or '')[:200],
                 'bloqueada': f['bloqueada']} for f in self.listar() if f['de_esta_carpeta']]
