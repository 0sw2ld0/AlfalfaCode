"""Servidor MCP de prueba (SDK oficial, API de bajo nivel) para test_alfalfa_mcp.py.

Uso: python mcp_servidor_prueba.py [--herramientas N] [--extra a.b,a_b] [--grande]
       [--http ARCHIVO_PUERTO --cabeceras ARCHIVO]

- contar: suma 1 a contador.txt en la carpeta actual y devuelve el total; es el
  contador de ejecuciones reales de tools/call.
- entorno: escribe en «archivo» las variables que recibió el proceso.
- escribir, conectar: prueban el sandbox (escritura en una ruta, TCP local).
- lento: anota lento.txt y duerme 30 s (plazos y cancelación).
- morir: anota morir.txt y termina sin responder (desconexión a mitad de llamada).
- herramienta_NNN: N herramientas de relleno con descripción y esquema realistas.
- --extra: nombres adicionales (colisiones por normalización).
- --grande: «enorme» con un esquema de más de 40 KB.
Con --http sirve streamable HTTP en 127.0.0.1 (puerto libre escrito en
ARCHIVO_PUERTO) y anota las cabeceras recibidas en ARCHIVO.
"""
import argparse
import asyncio
import json
import os
import socket
from pathlib import Path

import mcp.types as types
from mcp.server.lowlevel import Server

TEMAS = ('facturas', 'clientes', 'inventario', 'envíos', 'pagos', 'informes', 'usuarios', 'tickets',
         'calendario', 'documentos')


def herramientas(args):
    filas = [
        types.Tool(name='contar', description='Cuenta una ejecución real en contador.txt.',
                   inputSchema={'type': 'object', 'properties': {'nota': {'type': 'string'}},
                                'required': ['nota']},
                   annotations=types.ToolAnnotations(readOnlyHint=True)),
        types.Tool(name='entorno', description='Escribe las variables de entorno recibidas en un archivo.',
                   inputSchema={'type': 'object', 'properties': {'archivo': {'type': 'string'}},
                                'required': ['archivo']}),
        types.Tool(name='escribir', description='Intenta escribir un archivo en la ruta indicada.',
                   inputSchema={'type': 'object', 'properties': {'ruta': {'type': 'string'}},
                                'required': ['ruta']}),
        types.Tool(name='conectar', description='Intenta abrir una conexión TCP a 127.0.0.1.',
                   inputSchema={'type': 'object', 'properties': {'puerto': {'type': 'integer'}},
                                'required': ['puerto']}),
        types.Tool(name='lento', description='Tarda 30 segundos en responder.',
                   inputSchema={'type': 'object', 'properties': {}}),
        types.Tool(name='morir', description='Termina el servidor sin responder.',
                   inputSchema={'type': 'object', 'properties': {}}),
    ]
    for i in range(args.herramientas):
        tema = TEMAS[i % len(TEMAS)]
        filas.append(types.Tool(
            name='herramienta_%03d' % i,
            description='Consulta registros de %s número %d con filtros por fecha y estado.\n'
                        'Devuelve una página de resultados ordenada.' % (tema, i),
            inputSchema={'type': 'object', 'properties': {
                'consulta': {'type': 'string', 'description': 'Texto a buscar en %s' % tema},
                'desde': {'type': 'string', 'format': 'date'},
                'estado': {'type': 'string', 'enum': ['abierto', 'cerrado', 'pendiente']},
                'pagina': {'type': 'integer', 'minimum': 1}},
                'required': ['consulta']}))
    for nombre in filter(None, (args.extra or '').split(',')):
        filas.append(types.Tool(name=nombre, description='Herramienta extra ' + nombre,
                                inputSchema={'type': 'object', 'properties': {}}))
    if args.describir:
        # Una descripción que cita una variable recibida: el cliente debe redactarla.
        filas.append(types.Tool(name='indiscreta', description='Usa la clave ' + os.environ.get(args.describir, ''),
                                inputSchema={'type': 'object', 'properties': {}}))
    if args.grande:
        filas.append(types.Tool(name='enorme', description='Esquema enorme.', inputSchema={
            'type': 'object', 'properties': {'campo_%04d' % i: {'type': 'string', 'description': 'x' * 30}
                                             for i in range(1000)}}))
    return filas


def servidor(args):
    app = Server('prueba')
    catalogo = herramientas(args)

    @app.list_tools()
    async def listar():
        return catalogo

    @app.call_tool()
    async def llamar(nombre, argumentos):
        if nombre == 'contar':
            ruta = Path('contador.txt')
            total = int(ruta.read_text()) + 1 if ruta.exists() else 1
            ruta.write_text(str(total))
            return [types.TextContent(type='text', text='ejecuciones=' + str(total))]
        if nombre == 'entorno':
            Path(argumentos['archivo']).write_text(json.dumps(dict(os.environ)))
            return [types.TextContent(type='text', text=json.dumps(dict(os.environ)))]
        if nombre == 'escribir':
            try:
                Path(argumentos['ruta']).write_text('x')
                return [types.TextContent(type='text', text='escrito')]
            except OSError as exc:
                return [types.TextContent(type='text', text='denegado: ' + type(exc).__name__)]
        if nombre == 'conectar':
            try:
                socket.create_connection(('127.0.0.1', argumentos['puerto']), timeout=3).close()
                return [types.TextContent(type='text', text='conectado')]
            except OSError as exc:
                return [types.TextContent(type='text', text='denegado: ' + type(exc).__name__)]
        if nombre in ('lento', 'morir'):
            with open(nombre + '.txt', 'a') as registro:
                registro.write('1\n')
        if nombre == 'lento':
            await asyncio.sleep(30)
            return [types.TextContent(type='text', text='tarde')]
        if nombre == 'morir':
            os._exit(3)
        return [types.TextContent(type='text', text=nombre + ' ' + json.dumps(argumentos, ensure_ascii=False))]

    return app


async def stdio(app):
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (lectura, escritura):
        await app.run(lectura, escritura, app.create_initialization_options())


def http(app, archivo_puerto, archivo_cabeceras):
    import contextlib

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    gestor = StreamableHTTPSessionManager(app=app, json_response=True, stateless=True)

    async def manejar(scope, receive, send):
        cabeceras = {k.decode('latin-1'): v.decode('latin-1') for k, v in scope.get('headers', [])}
        with open(archivo_cabeceras, 'a') as salida:
            salida.write(json.dumps(cabeceras) + '\n')
        await gestor.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def vida(_):
        async with gestor.run():
            yield

    web = Starlette(routes=[Mount('/', app=manejar)], lifespan=vida)
    enchufe = socket.socket()
    enchufe.bind(('127.0.0.1', 0))
    Path(archivo_puerto).write_text(str(enchufe.getsockname()[1]))
    uvicorn.Server(uvicorn.Config(web, log_level='error')).run(sockets=[enchufe])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--herramientas', type=int, default=3)
    parser.add_argument('--extra')
    parser.add_argument('--grande', action='store_true')
    parser.add_argument('--http')
    parser.add_argument('--cabeceras')
    parser.add_argument('--describir', help='variable cuyo valor aparece en una descripción')
    parser.add_argument('--fallar', help='escribe esta variable en stderr y sale sin iniciar')
    parser.add_argument('--pid', help='archivo donde anotar el pid del proceso')
    args = parser.parse_args()
    if args.pid:
        Path(args.pid).write_text(str(os.getpid()))
    if args.fallar:
        import sys
        print('fallo con clave ' + os.environ.get(args.fallar, ''), file=sys.stderr, flush=True)
        sys.exit(2)
    app = servidor(args)
    if args.http:
        http(app, args.http, args.cabeceras)
    else:
        asyncio.run(stdio(app))


if __name__ == '__main__':
    main()
