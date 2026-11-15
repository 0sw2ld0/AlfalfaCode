"""El resultado de una herramienta que no cabe en el tope se recorta antes de serializarlo.

Antes se cortaba el JSON ya serializado por el carácter 19 000 y se metía como texto dentro de
otro JSON (`{"truncated": true, "text": "{\\"exit_code\\": 0, \\"output\\": \\"…`). El modelo
recibía JSON cortado y escapado dos veces, perdía los campos que venían detrás (`truncated`,
`classification`, `exit_code`) y, en una orden, justo el final de la salida, que es donde suele
estar el resumen («3 pruebas fallaron»).

Ahora se recorta por el medio el texto más largo (conservando principio y final, por líneas
cuando se puede) hasta que el resultado cabe, y el resto de campos llega entero.
"""
from __future__ import annotations

import json

MAX_RESULTADO = 20000
# Lo que se reserva para el aviso `recortado` y para el margen de cada pasada.
_RESERVA = 400
# Un texto más corto que esto no se recorta: no compensa y el aviso ocuparía casi lo mismo.
_MINIMO = 300


def _medida(valor):
    return len(json.dumps(valor, ensure_ascii=False))


def _mayor(valor, ruta=()):
    """(medida, ruta) del texto o la lista que más ocupa dentro de `valor`."""
    mejor = (0, None)
    if isinstance(valor, str):
        return (_medida(valor), ruta)
    hijos = valor.items() if isinstance(valor, dict) else enumerate(valor) if isinstance(valor, list) else ()
    if isinstance(valor, list) and len(valor) > 1:
        mejor = (_medida(valor), ruta)
    for clave, hijo in hijos:
        candidato = _mayor(hijo, ruta + (clave,))
        # Un texto gana a la lista que lo contiene si ocupa casi todo: recortar dentro es más fino.
        if candidato[1] is not None and (candidato[0] > mejor[0] or (
                isinstance(hijo, str) and candidato[0] * 2 > mejor[0])):
            mejor = candidato
    return mejor


def _leer(valor, ruta):
    for clave in ruta:
        valor = valor[clave]
    return valor


def _poner(valor, ruta, nuevo):
    for clave in ruta[:-1]:
        valor = valor[clave]
    valor[ruta[-1]] = nuevo


def recortar_medio(texto, conservar):
    """`texto` con a lo sumo `conservar` caracteres: el principio y el final, con una marca."""
    if len(texto) <= conservar:
        return texto, 0
    cabeza = max(conservar * 2 // 5, 0)
    cola = max(conservar - cabeza, 0)
    inicio, fin = texto[:cabeza], texto[len(texto) - cola:] if cola else ''
    # Por líneas, si el corte cae cerca de un salto: no se deja media línea a cada lado.
    salto = inicio.rfind('\n')
    if salto > cabeza * 3 // 4:
        inicio = inicio[:salto + 1]
    salto = fin.find('\n')
    if 0 <= salto < cola // 4:
        fin = fin[salto + 1:]
    quitado = texto[len(inicio):len(texto) - len(fin)]
    lineas = quitado.count('\n')
    marca = ('\n…[' + str(len(quitado)) + ' caracteres' + (' (' + str(lineas) + ' líneas)' if lineas else '')
             + ' recortados del medio]…\n')
    return inicio + marca + fin, len(quitado)


def _recortar_lista(lista, sobra):
    """La lista sin los últimos elementos que hagan falta, con un elemento que dice cuántos faltan."""
    quitados = 0
    while len(lista) > 1 and sobra > 0:
        sobra -= _medida(lista.pop()) + 2
        quitados += 1
    lista.append('…[' + str(quitados) + ' elementos más recortados]')
    return quitados


def serializar(resultado, limite=MAX_RESULTADO):
    """El JSON de `resultado` en a lo sumo `limite` caracteres (con margen), sin cortar el JSON."""
    texto = json.dumps(resultado, ensure_ascii=False)
    if len(texto) <= limite:
        return texto
    copia = json.loads(texto) if isinstance(resultado, dict) else {'resultado': json.loads(texto)}
    objetivo = limite - _RESERVA
    campos, caracteres, elementos = [], 0, 0
    for _ in range(64):
        sobra = _medida(copia) - objetivo
        if sobra <= 0:
            break
        medida, ruta = _mayor(copia)
        if ruta is None or medida < _MINIMO:
            break
        valor = _leer(copia, ruta)
        nombre = '.'.join(str(c) for c in ruta)
        if isinstance(valor, list):
            elementos += _recortar_lista(valor, sobra)
        else:
            # La medida cuenta los escapes (\n, \"): se pasa de medida a caracteres.
            por_caracter = medida / max(len(valor), 1)
            conservar = max(int(len(valor) - sobra / por_caracter) - 120, _MINIMO // 2)
            nuevo, quitado = recortar_medio(valor, conservar)
            if not quitado:
                break
            _poner(copia, ruta, nuevo)
            caracteres += quitado
        if nombre not in campos:
            campos.append(nombre)
    partes = []
    if caracteres:
        partes.append(str(caracteres) + ' caracteres del medio')
    if elementos:
        partes.append(str(elementos) + ' elementos del final')
    aviso = ('El resultado no cabía en ' + str(limite) + ' caracteres: se recortaron ' + ' y '.join(partes)
             + ' de «' + '», «'.join(campos) + '». El resto de campos está entero.') if partes else None
    if aviso:
        copia['recortado'] = aviso
    final = json.dumps(copia, ensure_ascii=False)
    if len(final) <= limite:
        return final
    # Sin un campo largo que recortar (miles de campos cortos): el JSON entero como texto,
    # recortado por el medio, pero siempre JSON válido.
    envuelto, _ = recortar_medio(texto, objetivo // 2)
    return json.dumps({'truncated': True, 'recortado': 'El resultado no cabía en ' + str(limite)
                       + ' caracteres y no tenía un campo largo que recortar: va como texto, recortado '
                       'por el medio.', 'text': envuelto}, ensure_ascii=False)
