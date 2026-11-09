"""Instrucciones `AGENTS.md` por proyecto, como Codex.

Traducción de codex-rs/core/src/agents_md.rs (OpenAI Codex, Apache-2.0; ver
NOTICE, LICENSE y PROCEDENCIA.md de esta carpeta):

1. La raíz del proyecto es el antecesor más cercano de la carpeta de trabajo
   que contiene `.git` (archivo o carpeta). Sin ella sólo cuenta la carpeta.
2. De la raíz a la carpeta de trabajo, ambas incluidas, en cada carpeta se toma
   `AGENTS.override.md` si existe y si no `AGENTS.md`, y se concatenan en ese
   orden. Nunca se sube más allá de la raíz.
3. Las del proyecto comparten un tope de 32 KiB (`project_doc_max_bytes`): el
   archivo que no cabe se recorta y se avisa; los siguientes se omiten.
4. Las del usuario (`$ALFALFA_HOME/AGENTS.override.md` o `AGENTS.md`, como las
   de `~/.codex`) van primero, separadas por `--- project-doc ---`.

Además, para que un proyecto preparado para otra herramienta funcione sin tocar nada
(alfalfa/importadas), las del proyecto siguen este orden, todas dentro del mismo tope:

a. `developer_instructions` de `~/.codex/config.toml` y de `.codex/config.toml` (Codex
   las pone delante de AGENTS.md).
b. En cada carpeta, de la raíz a la de trabajo: AGENTS.override.md, AGENTS.md o el primer
   `project_doc_fallback_filenames` de Codex que exista; y después los de Claude Code:
   CLAUDE.md, .claude/CLAUDE.md y CLAUDE.local.md.
c. Los archivos de `instructions` de opencode.json.

Un archivo que ya entró (el mismo, un enlace a él o con el mismo texto) no se repite, y un
CLAUDE.md que sólo importa AGENTS.md (`@AGENTS.md`) no añade nada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NOMBRE = 'AGENTS.md'
LOCAL = 'AGENTS.override.md'
MARCADORES = ('.git',)
MAX_BYTES = 32 * 1024
SEPARADOR = '\n\n--- project-doc ---\n\n'
GUIA = 'Recorta las instrucciones del proyecto para que quepan en el tope.'
# Los de Claude Code, en el orden en que los lee: el compartido, el de .claude y el personal.
CLAUDE = ('CLAUDE.md', '.claude/CLAUDE.md', 'CLAUDE.local.md')
IMPORTACION = re.compile(r'^\s*@(\S+)\s*$', re.MULTILINE)


@dataclass
class Instrucciones:
    usuario: str | None = None
    # Ruta del AGENTS.md del usuario, para la lista «Archivos de memoria» del panel.
    usuario_ruta: Path | None = None
    proyecto: list = field(default_factory=list)  # [(ruta, texto)]
    avisos: list = field(default_factory=list)
    raiz: Path | None = None

    def fuentes(self):
        return [ruta for ruta, _ in self.proyecto]

    def texto(self):
        """legacy_text de Codex: usuario, separador y las del proyecto en orden."""
        partes = [self.usuario] if self.usuario else []
        proyecto = '\n\n'.join(texto for _, texto in self.proyecto)
        if partes and proyecto:
            return partes[0] + SEPARADOR + proyecto
        return partes[0] if partes else proyecto


def raiz_del_proyecto(carpeta, marcadores=MARCADORES):
    """find_nearest_ancestor_with_markers: la carpeta o un antecesor con un marcador."""
    actual = Path(carpeta).resolve()
    for candidata in (actual, *actual.parents):
        if any((candidata / marcador).exists() for marcador in marcadores):
            return candidata
    return None


def carpetas(carpeta):
    """De la raíz del proyecto a la carpeta de trabajo, ambas incluidas."""
    actual = Path(carpeta).resolve()
    raiz = raiz_del_proyecto(actual)
    if raiz is None:
        return [actual], None
    lista, cursor = [], actual
    while True:
        lista.append(cursor)
        if cursor == raiz or cursor.parent == cursor:
            break
        cursor = cursor.parent
    return list(reversed(lista)), raiz


def elegir(carpeta, alternativos=()):
    """AGENTS.override.md tiene prioridad sobre AGENTS.md y éste sobre los alternativos de Codex
    (candidate_filenames con project_doc_fallback_filenames)."""
    for nombre in (LOCAL, NOMBRE, *alternativos):
        ruta = carpeta / nombre
        if ruta.is_file():
            return ruta
    return None


def _leer(ruta, maximo):
    with open(ruta, 'rb') as archivo:
        datos = archivo.read(maximo + 1)
    return datos[:maximo], len(datos) > maximo


def cargar(carpeta, hogar=None, max_bytes=MAX_BYTES):
    """Las instrucciones para una sesión que trabaja en `carpeta`."""
    resultado = Instrucciones()
    if hogar is not None:
        ruta = elegir(Path(hogar))
        if ruta is not None:
            try:
                datos, sobra = _leer(ruta, max_bytes)
                texto = datos.decode('utf-8', errors='replace')
                if sobra:
                    resultado.avisos.append('Sólo se cargaron ' + str(max_bytes) + ' bytes de ' + str(ruta) + '. '
                                            + GUIA)
                if texto.strip():
                    resultado.usuario = texto
                    resultado.usuario_ruta = ruta
            except OSError as exc:
                resultado.avisos.append('No se pudo leer ' + str(ruta) + ': ' + (exc.strerror or type(exc).__name__))
    if max_bytes <= 0:
        return resultado
    directorios, resultado.raiz = carpetas(carpeta)
    from .. import importadas
    try:
        antes, despues, alternativos, avisos = importadas.instrucciones(carpeta)
    except Exception as exc:  # noqa: BLE001 - el AGENTS.md del proyecto entra aunque falle lo ajeno
        antes, despues, alternativos, avisos = [], [], [], ['Configuraciones de otras herramientas: '
                                                            + type(exc).__name__]
    resultado.avisos += avisos
    candidatos = list(antes)
    for directorio in directorios:
        candidatos.append((elegir(directorio, alternativos), None))
        candidatos += [(directorio / nombre, None) for nombre in CLAUDE if (directorio / nombre).is_file()]
    candidatos += despues
    limite = (resultado.raiz or Path(carpeta)).resolve()
    resto, vistos, textos = max_bytes, set(), set()
    for ruta, texto in candidatos:
        if ruta is None:
            continue
        real = ruta.resolve() if texto is None else ruta
        if real in vistos:
            continue
        if texto is None and not _dentro(real, limite):
            # Un AGENTS.md o CLAUDE.md que enlaza fuera del proyecto (p. ej. a ~/.aws/credentials) no entra.
            resultado.avisos.append('No se leen las instrucciones de ' + str(ruta) + ': enlazan fuera del proyecto.')
            vistos.add(real)
            continue
        vistos.add(real)
        if resto <= 0:
            resultado.avisos.append('Se omitieron las instrucciones de ' + str(ruta) + ' porque se agotó el tope de '
                                    + str(max_bytes) + ' bytes. ' + GUIA)
            break
        if texto is None:
            try:
                datos, sobra = _leer(ruta, resto)
            except OSError as exc:
                resultado.avisos.append('No se pudo leer ' + str(ruta) + ': ' + (exc.strerror or type(exc).__name__))
                continue
            texto = datos.decode('utf-8', errors='replace')
        else:
            datos = texto.encode('utf-8')
            sobra = len(datos) > resto
            if sobra:
                datos = datos[:resto]
                texto = datos.decode('utf-8', errors='ignore')
        if sobra:
            resultado.avisos.append('Sólo se cargaron ' + str(resto) + ' bytes de ' + str(ruta) + ' dentro del tope de '
                                    + str(max_bytes) + ' bytes. ' + GUIA)
        if ruta.name in ('CLAUDE.md', 'CLAUDE.local.md'):
            texto = _sin_importaciones_repetidas(texto, ruta.parent, vistos)
        normal = ' '.join(texto.split())
        if not normal or normal in textos:
            continue
        textos.add(normal)
        resultado.proyecto.append((ruta, texto))
        resto -= len(datos)
    return resultado


def _dentro(ruta, carpeta):
    try:
        Path(ruta).relative_to(carpeta)
        return True
    except ValueError:
        return False


def _sin_importaciones_repetidas(texto, carpeta, vistos):
    """Quita las líneas `@archivo` de un CLAUDE.md que apuntan a algo ya cargado (el `@AGENTS.md` habitual)."""
    # Relativa al archivo, como Claude Code; desde .claude/CLAUDE.md también se acepta la de la carpeta.
    bases = [carpeta] + ([carpeta.parent] if carpeta.name == '.claude' else [])

    def cambiar(m):
        try:
            repetida = any((base / m.group(1)).expanduser().resolve() in vistos for base in bases)
        except OSError:
            repetida = False
        return '' if repetida else m.group(0)
    return IMPORTACION.sub(cambiar, texto)
