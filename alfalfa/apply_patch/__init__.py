"""apply_patch completo para AlfalfaCode.

Traducción de codex-rs/apply-patch (parser.rs, streaming_parser.rs,
file_update.rs y text_file.rs; OpenAI Codex, Apache-2.0; ver NOTICE, LICENSE y
PROCEDENCIA.md de esta carpeta). La búsqueda tolerante es la de
`edicion_tolerante.seek_sequence`, ya portada.

Diferencia deliberada con Codex: el parche es todo o nada. Primero se calcula
el resultado de todos los bloques en memoria (cada bloque ve lo que dejaron
los anteriores) y sólo si todos encajan se escribe; Codex aplica en orden y
deja los primeros cambios si falla uno posterior.
"""
from __future__ import annotations

from ..edicion_tolerante import seek_sequence
from .parser import Bloque, ParchInvalido, Trozo, leer  # noqa: F401


class NoEncaja(ValueError):
    """Un bloque no se puede aplicar al contenido actual (ComputeReplacements)."""


def _lineas(texto):
    """SourceFile::parse: [(texto, final de línea o None)] y el final preferido."""
    lineas, preferido, inicio, i = [], None, 0, 0
    while i < len(texto):
        c = texto[i]
        if c == '\r' and texto[i + 1:i + 2] == '\n':
            fin, largo = '\r\n', 2
        elif c in '\r\n':
            fin, largo = c, 1
        else:
            i += 1
            continue
        preferido = preferido or fin
        lineas.append([texto[inicio:i], fin])
        i += largo
        inicio = i
    if inicio < len(texto):
        lineas.append([texto[inicio:], None])
    return lineas, preferido or '\n'


def _reemplazos(originales, ruta, trozos):
    """compute_replacements: (inicio, cuántas quitar, líneas nuevas) ordenados."""
    reemplazos, indice = [], 0
    for trozo in trozos:
        if trozo.contexto is not None:
            encontrado = seek_sequence(originales, [trozo.contexto], indice, False)
            if encontrado is None:
                raise NoEncaja("No se encontró el contexto '" + trozo.contexto + "' en " + ruta + '.')
            indice = encontrado + 1
        if not trozo.viejas:
            # Sólo añade: al final del archivo, como Codex.
            reemplazos.append((len(originales), 0, list(trozo.nuevas)))
            continue
        patron, nuevas = trozo.viejas, trozo.nuevas
        encontrado = seek_sequence(originales, patron, indice, trozo.fin_de_archivo)
        if encontrado is None and patron and patron[-1] == '':
            # Una línea vacía final suele ser el salto de línea del archivo.
            patron = patron[:-1]
            if nuevas and nuevas[-1] == '':
                nuevas = nuevas[:-1]
            encontrado = seek_sequence(originales, patron, indice, trozo.fin_de_archivo)
        if encontrado is None:
            raise NoEncaja('No se encontraron las líneas esperadas en ' + ruta + ':\n' + '\n'.join(trozo.viejas))
        viejo, nuevo = 0, 0
        for ctx_viejo, ctx_nuevo in trozo.contexto_indices:
            if ctx_viejo >= len(patron) or ctx_nuevo >= len(nuevas):
                break
            if viejo != ctx_viejo or nuevo != ctx_nuevo:
                reemplazos.append((encontrado + viejo, ctx_viejo - viejo, nuevas[nuevo:ctx_nuevo]))
            viejo, nuevo = ctx_viejo + 1, ctx_nuevo + 1
        if viejo != len(patron) or nuevo != len(nuevas):
            reemplazos.append((encontrado + viejo, len(patron) - viejo, nuevas[nuevo:]))
        indice = encontrado + len(patron)
    reemplazos.sort(key=lambda r: r[0])
    for anterior, siguiente in zip(reemplazos, reemplazos[1:]):
        if anterior[0] + anterior[1] > siguiente[0]:
            raise NoEncaja('Dos bloques de ' + ruta + ' cambian las mismas líneas.')
    return reemplazos


def derivar(original, trozos, ruta):
    """derive_new_contents_from_chunks: el texto nuevo conservando los finales de línea."""
    lineas, preferido = _lineas(original)
    reemplazos = _reemplazos([l[0] for l in lineas], ruta, trozos)
    nuevas, fuente = [], 0
    for inicio, cuantas, segmento in reemplazos:
        nuevas.extend(lineas[fuente:inicio])
        nuevas.extend([texto, preferido] for texto in segmento)
        fuente = inicio + cuantas
    nuevas.extend(lineas[fuente:])
    return ''.join(texto + (fin or preferido) for texto, fin in nuevas)


def planificar(bloques, leer_texto):
    """[(ruta, texto nuevo o None si se borra, acción)] tras aplicar todos los bloques en memoria.

    leer_texto(ruta) devuelve el texto actual, None si no existe, o lanza
    ValueError si no es un archivo regular editable.
    """
    if not bloques:
        raise ParchInvalido('No se modificó ningún archivo: el parche no tiene bloques.')
    virtual, orden, acciones = {}, [], {}

    def actual(ruta):
        return virtual[ruta] if ruta in virtual else leer_texto(ruta)

    def poner(ruta, texto, accion):
        if ruta not in virtual:
            orden.append(ruta)
            acciones[ruta] = accion
        elif accion == 'D' and acciones.get(ruta) == 'A':
            acciones[ruta] = 'D'
        elif accion != 'D' and acciones.get(ruta) == 'D':
            acciones[ruta] = 'M'
        virtual[ruta] = texto

    for bloque in bloques:
        if bloque.tipo == 'add':
            previo = actual(bloque.ruta)
            poner(bloque.ruta, bloque.contenido, 'A' if previo is None else 'M')
        elif bloque.tipo == 'delete':
            if actual(bloque.ruta) is None:
                raise NoEncaja('No se puede borrar ' + bloque.ruta + ': no existe.')
            poner(bloque.ruta, None, 'D')
        else:
            original = actual(bloque.ruta)
            if original is None:
                raise NoEncaja('No se puede actualizar ' + bloque.ruta + ': no existe.')
            nuevo = derivar(original, bloque.trozos, bloque.ruta)
            if bloque.destino and bloque.destino != bloque.ruta:
                poner(bloque.destino, nuevo, 'A' if actual(bloque.destino) is None else 'M')
                poner(bloque.ruta, None, 'D')
            else:
                poner(bloque.ruta, nuevo, 'M')
    return [(ruta, virtual[ruta], acciones[ruta]) for ruta in orden]
