"""Hook de prueba: registra su entrada y actúa según argv. Lo ejecuta /bin/sh en Seatbelt.

Uso: hook_prueba.py <registro> <modo> [argumento]
"""
import json
import os
import socket
import sys
import time

registro, modo = sys.argv[1], sys.argv[2]
argumento = sys.argv[3] if len(sys.argv) > 3 else ''
entrada = sys.stdin.read()
with open(registro, 'a') as salida:
    salida.write(json.dumps({'modo': modo, 'pid': os.getpid(), 'entrada': json.loads(entrada)}) + '\n')

if modo == 'nada':
    sys.exit(0)
if modo == 'bloquear':
    sys.stderr.write(argumento)
    sys.exit(2)
if modo == 'json':
    print(argumento)
    sys.exit(0)
if modo == 'texto':
    print(argumento)
    sys.exit(0)
if modo == 'salir':
    sys.stderr.write('fallo del hook')
    sys.exit(int(argumento))
if modo == 'dormir':
    time.sleep(float(argumento))
    sys.exit(0)
if modo == 'grande':
    sys.stdout.write('x' * 200000)
    sys.exit(0)
if modo == 'entorno':
    with open(argumento, 'w') as destino:
        json.dump(dict(os.environ), destino)
    sys.exit(0)
if modo == 'escribir':
    resultado = {}
    for ruta in argumento.split(','):
        try:
            with open(ruta, 'w') as destino:
                destino.write('hook')
            resultado[ruta] = 'escrito'
        except OSError as exc:
            resultado[ruta] = type(exc).__name__ + ': ' + str(exc)
    print(json.dumps({'systemMessage': json.dumps(resultado)}))
    sys.exit(0)
if modo == 'red':
    try:
        socket.create_connection(('127.0.0.1', int(argumento)), timeout=2).close()
        resultado = 'conectado'
    except OSError as exc:
        resultado = 'bloqueado: ' + type(exc).__name__
    print(json.dumps({'systemMessage': 'red=' + resultado}))
    sys.exit(0)
sys.exit(99)
