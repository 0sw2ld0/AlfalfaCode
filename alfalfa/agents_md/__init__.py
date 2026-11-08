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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

NOMBRE = 'AGENTS.md'
LOCAL = 'AGENTS.override.md'
MARCADORES = ('.git',)
MAX_BYTES = 32 * 1024
SEPARADOR = '\n\n--- project-doc ---\n\n'
GUIA = 'Recorta las instrucciones del proyecto para que quepan en el tope.'


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


def elegir(carpeta):
    """AGENTS.override.md tiene prioridad sobre AGENTS.md (candidate_filenames)."""
    for nombre in (LOCAL, NOMBRE):
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
                    resultado.avisos.append('Sólo se cargaron ' + str(max_bytes) + ' bytes de ' + str(ruta) + '. ' + GUIA)
                if texto.strip():
                    resultado.usuario = texto
                    resultado.usuario_ruta = ruta
            except OSError as exc:
                resultado.avisos.append('No se pudo leer ' + str(ruta) + ': ' + (exc.strerror or type(exc).__name__))
    if max_bytes <= 0:
        return resultado
    directorios, resultado.raiz = carpetas(carpeta)
    resto = max_bytes
    for directorio in directorios:
        ruta = elegir(directorio)
        if ruta is None:
            continue
        if resto <= 0:
            resultado.avisos.append('Se omitieron las instrucciones de ' + str(ruta) + ' porque se agotó el tope de '
                                    + str(max_bytes) + ' bytes. ' + GUIA)
            break
        try:
            datos, sobra = _leer(ruta, resto)
        except OSError as exc:
            resultado.avisos.append('No se pudo leer ' + str(ruta) + ': ' + (exc.strerror or type(exc).__name__))
            continue
        if sobra:
            resultado.avisos.append('Sólo se cargaron ' + str(resto) + ' bytes de ' + str(ruta) + ' dentro del tope de '
                                    + str(max_bytes) + ' bytes. ' + GUIA)
        texto = datos.decode('utf-8', errors='replace')
        if texto.strip():
            resultado.proyecto.append((ruta, texto))
            resto -= len(datos)
    return resultado
