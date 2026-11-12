"""Servidor de lenguaje simulado para test_diagnosticos.

Cada línea con «ERROR» es un error y cada «TODO» un aviso, en esa línea.
Variables: LSP_SIMULADO_MODO = pull (por defecto) | push | caida | mudo (nunca da diagnósticos);
LSP_SIMULADO_RETRASO = segundos antes de contestar a initialize;
LSP_SIMULADO_REGISTRO = archivo donde apunta cada método que recibe.
"""
import json
import os
import sys
import time

MODO = os.environ.get('LSP_SIMULADO_MODO', 'pull')
RETRASO = float(os.environ.get('LSP_SIMULADO_RETRASO', '0'))
REGISTRO = os.environ.get('LSP_SIMULADO_REGISTRO')
documentos = {}


def leer():
    largo = None
    while True:
        linea = sys.stdin.buffer.readline()
        if not linea:
            sys.exit(0)
        linea = linea.strip()
        if not linea:
            break
        clave, _, valor = linea.partition(b':')
        if clave.lower() == b'content-length':
            largo = int(valor)
    return json.loads(sys.stdin.buffer.read(largo))


def enviar(mensaje):
    cuerpo = json.dumps(dict(mensaje, jsonrpc='2.0')).encode()
    sys.stdout.buffer.write(b'Content-Length: %d\r\n\r\n' % len(cuerpo) + cuerpo)
    sys.stdout.buffer.flush()


def diagnosticos(texto):
    filas = []
    for numero, linea in enumerate(texto.splitlines()):
        for palabra, gravedad in (('ERROR', 1), ('TODO', 2)):
            columna = linea.find(palabra)
            if columna >= 0:
                filas.append({'range': {'start': {'line': numero, 'character': columna},
                                        'end': {'line': numero, 'character': columna + len(palabra)}},
                              'severity': gravedad, 'source': 'simulado', 'code': palabra.lower(),
                              'message': palabra + ' en la línea'})
    return filas


while True:
    mensaje = leer()
    metodo = mensaje.get('method')
    if REGISTRO and metodo:
        with open(REGISTRO, 'a') as registro:
            registro.write(metodo + '\n')
    params = mensaje.get('params') or {}
    if metodo == 'initialize':
        time.sleep(RETRASO)
        if MODO == 'caida':
            sys.exit(3)
        enviar({'id': mensaje['id'], 'result': {'capabilities': {'textDocumentSync': 1}}})
    elif metodo in ('textDocument/didOpen', 'textDocument/didChange'):
        documento = params['textDocument']
        texto = documento['text'] if metodo.endswith('didOpen') else params['contentChanges'][-1]['text']
        documentos[documento['uri']] = texto
        if MODO == 'push':
            enviar({'method': 'textDocument/publishDiagnostics',
                    'params': {'uri': documento['uri'], 'version': documento['version'],
                               'diagnostics': diagnosticos(texto)}})
    elif metodo == 'textDocument/diagnostic':
        if MODO == 'mudo':
            continue
        if MODO == 'push':
            enviar({'id': mensaje['id'], 'error': {'code': -32601, 'message': 'Unhandled method'}})
        else:
            uri = params['textDocument']['uri']
            enviar({'id': mensaje['id'], 'result': {'kind': 'full',
                                                    'items': diagnosticos(documentos.get(uri, ''))}})
    elif metodo == 'shutdown':
        enviar({'id': mensaje['id'], 'result': None})
    elif metodo == 'exit':
        sys.exit(0)
    elif 'id' in mensaje:
        enviar({'id': mensaje['id'], 'result': None})
