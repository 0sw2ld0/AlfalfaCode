"""Gramática y lectura de parches de apply_patch.

Traducción a Python de codex-rs/apply-patch/src/parser.rs y
streaming_parser.rs (OpenAI Codex, Apache-2.0; ver NOTICE, LICENSE y
PROCEDENCIA.md de esta carpeta). La gramática Lark oficial:

    start: begin_patch hunk+ end_patch
    begin_patch: "*** Begin Patch" LF
    end_patch: "*** End Patch" LF?

    hunk: add_hunk | delete_hunk | update_hunk
    add_hunk: "*** Add File: " filename LF add_line+
    delete_hunk: "*** Delete File: " filename LF
    update_hunk: "*** Update File: " filename LF change_move? change?
    filename: /(.+)/
    add_line: "+" /(.+)/ LF -> line

    change_move: "*** Move to: " filename LF
    change: (change_context | change_line)+ eof_line?
    change_context: ("@@" | "@@ " /(.+)/) LF
    change_line: ("+" | "-" | " ") /(.+)/ LF
    eof_line: "*** End of File" LF

Como Codex, se toleran espacios alrededor de los marcadores y un heredoc
(<<'EOF' … EOF) envolviendo el parche. Sin `*** Environment ID:`: AlfalfaCode
tiene una sola carpeta de trabajo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BEGIN = '*** Begin Patch'
END = '*** End Patch'
ADD = '*** Add File: '
DELETE = '*** Delete File: '
UPDATE = '*** Update File: '
MOVE = '*** Move to: '
EOF_MARKER = '*** End of File'
CONTEXT = '@@ '
EMPTY_CONTEXT = '@@'
CABECERAS = ("Cabeceras válidas: '*** Add File: {ruta}', '*** Delete File: {ruta}', "
             "'*** Update File: {ruta}'")


class ParchInvalido(ValueError):
    def __init__(self, texto, linea=None):
        super().__init__(texto if linea is None else 'Bloque inválido en la línea ' + str(linea) + ': ' + texto)
        self.linea = linea


@dataclass
class Trozo:
    """UpdateFileChunk: contexto opcional, líneas viejas y nuevas."""
    contexto: str | None = None
    viejas: list = field(default_factory=list)
    nuevas: list = field(default_factory=list)
    # Pares (índice en viejas, índice en nuevas) de las líneas de contexto.
    contexto_indices: list = field(default_factory=list)
    fin_de_archivo: bool = False

    def contexto_linea(self, texto):
        self.contexto_indices.append((len(self.viejas), len(self.nuevas)))
        self.viejas.append(texto)
        self.nuevas.append(texto)

    def vacio(self):
        return not self.viejas and not self.nuevas


@dataclass
class Bloque:
    tipo: str  # 'add' | 'delete' | 'update'
    ruta: str
    contenido: str = ''
    destino: str | None = None
    trozos: list = field(default_factory=list)
    linea: int = 0


def _limites(lineas):
    """check_patch_boundaries_lenient: marcas de inicio y fin, o un heredoc que las envuelve."""
    def estrictas(ls):
        primera = ls[0].strip() if ls else None
        ultima = ls[-1].strip() if ls else None
        if primera == BEGIN and ultima == END:
            return None
        if primera != BEGIN:
            return "La primera línea del parche debe ser '*** Begin Patch'."
        return "La última línea del parche debe ser '*** End Patch'."
    error = estrictas(lineas)
    if error is None:
        return lineas
    if (len(lineas) >= 4 and lineas[0] in ('<<EOF', "<<'EOF'", '<<"EOF"') and lineas[-1].endswith('EOF')
            and estrictas(lineas[1:-1]) is None):
        return lineas[1:-1]
    raise ParchInvalido(error)


def leer(texto):
    """Lista de bloques del parche; ParchInvalido con la línea si no cumple la gramática."""
    if not isinstance(texto, str):
        raise ParchInvalido('El parche debe ser texto.')
    lineas = _limites(texto.strip().splitlines())
    bloques, modo, numero = [], 'inicio', 0

    def comprobar_update(linea):
        if not bloques or bloques[-1].tipo != 'update':
            return
        actual = bloques[-1]
        if not actual.trozos:
            raise ParchInvalido("El bloque de '" + actual.ruta + "' está vacío.", actual.linea)
        if actual.trozos[-1].vacio():
            raise ParchInvalido('El bloque de cambios no contiene ninguna línea.' if linea == END else
                                "Línea inesperada: '" + linea + "'. Cada línea debe empezar por ' ' "
                                '(contexto), \'+\' (añadida) o \'-\' (quitada).', numero)

    def cabecera(recortada):
        nonlocal modo
        if recortada == END:
            comprobar_update(recortada)
            modo = 'fin'
            return True
        for marca, tipo in ((ADD, 'add'), (DELETE, 'delete'), (UPDATE, 'update')):
            if recortada.startswith(marca):
                comprobar_update(recortada)
                ruta = recortada[len(marca):]
                if not ruta.strip():
                    raise ParchInvalido('Falta la ruta en la cabecera.', numero)
                bloques.append(Bloque(tipo, ruta, linea=numero))
                modo = tipo
                return True
        return False

    for posicion, linea in enumerate(lineas):
        numero += 1
        if linea.endswith('\r'):
            linea = linea[:-1]
        recortada = linea.strip()
        if posicion == len(lineas) - 1 and recortada == END and modo not in ('inicio', 'fin'):
            # StreamingPatchParser::finish: la última línea se compara recortada.
            comprobar_update(END)
            modo = 'fin'
            continue
        if modo == 'inicio':
            if recortada != BEGIN:
                raise ParchInvalido("La primera línea del parche debe ser '*** Begin Patch'.")
            modo = 'empezado'
        elif modo in ('empezado', 'delete'):
            if not cabecera(recortada):
                raise ParchInvalido("'" + recortada + "' no es una cabecera válida. " + CABECERAS, numero)
        elif modo == 'add':
            if cabecera(recortada):
                continue
            if linea.startswith('+'):
                bloques[-1].contenido += linea[1:] + '\n'
                continue
            raise ParchInvalido("'" + recortada + "' no es una cabecera válida. " + CABECERAS, numero)
        elif modo == 'update':
            linea_cambio = linea.rstrip()
            if cabecera(linea_cambio):
                continue
            actual = bloques[-1]
            trozos = actual.trozos
            if trozos and trozos[-1].fin_de_archivo:
                if not linea_cambio:
                    continue
                if linea_cambio != EMPTY_CONTEXT and not linea_cambio.startswith(CONTEXT):
                    raise ParchInvalido("Se esperaba un marcador @@ al empezar el bloque, llegó: '" + linea + "'",
                                        numero)
            if not trozos and actual.destino is None and linea_cambio.startswith(MOVE):
                actual.destino = linea_cambio[len(MOVE):]
                continue
            if (linea_cambio == EMPTY_CONTEXT or linea_cambio.startswith(CONTEXT)) and trozos and trozos[-1].vacio():
                raise ParchInvalido("Línea inesperada: '" + linea + "'. Cada línea debe empezar por ' ' (contexto), "
                                    "'+' (añadida) o '-' (quitada).", numero)
            if linea_cambio == EMPTY_CONTEXT:
                trozos.append(Trozo())
                continue
            if linea_cambio.startswith(CONTEXT):
                trozos.append(Trozo(contexto=linea_cambio[len(CONTEXT):]))
                continue
            if linea_cambio == EOF_MARKER:
                if trozos and trozos[-1].vacio():
                    raise ParchInvalido('El bloque de cambios no contiene ninguna línea.', numero)
                if trozos:
                    trozos[-1].fin_de_archivo = True
                continue
            if not linea:
                if not trozos:
                    trozos.append(Trozo())
                trozos[-1].contexto_linea('')
                continue
            if linea[0] in ' +-':
                if not trozos:
                    trozos.append(Trozo())
                if linea[0] == ' ':
                    trozos[-1].contexto_linea(linea[1:])
                elif linea[0] == '+':
                    trozos[-1].nuevas.append(linea[1:])
                else:
                    trozos[-1].viejas.append(linea[1:])
                continue
            if trozos and not trozos[-1].vacio():
                raise ParchInvalido("Se esperaba un marcador @@ al empezar el bloque, llegó: '" + linea + "'", numero)
            raise ParchInvalido("Línea inesperada: '" + linea + "'. Cada línea debe empezar por ' ' (contexto), "
                                "'+' (añadida) o '-' (quitada).", numero)
        elif modo == 'fin':
            if recortada:
                raise ParchInvalido("La última línea del parche debe ser '*** End Patch'.")
    if modo != 'fin':
        raise ParchInvalido("La última línea del parche debe ser '*** End Patch'.")
    return bloques
