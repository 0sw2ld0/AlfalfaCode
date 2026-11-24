"""Participantes de texto de Lluvia de ideas mediante el CLI oficial de Claude Code."""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

from . import adjuntos, ajustes, contenido_externo

PREFIJO = 'claude-code/'
MODELOS = ('haiku', 'sonnet', 'opus')
MAX_LINEA = 256 * 1024
MAX_CONTEXTO = 24_000
MAX_RESPUESTA = 12_000
PLAZO = 120


def es_externo(modelo):
    return isinstance(modelo, str) and modelo.startswith(PREFIJO)


def ejecutable():
    for carpeta in (Path.home() / '.local/bin', Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path('/usr/bin')):
        ruta = carpeta / 'claude'
        if ruta.is_file() and os.access(ruta, os.X_OK):
            return ruta
    return None


async def _resultado(proceso, prompt):
    proceso.stdin.write(prompt.encode('utf-8'))
    await proceso.stdin.drain()
    proceso.stdin.close()
    respuesta = None
    while linea := await proceso.stdout.readline():
        evento = json.loads(linea)
        if isinstance(evento, dict) and evento.get('type') == 'result':
            respuesta = evento
    return await proceso.wait(), respuesta


async def _consultar(binario, modelo, esfuerzo, instrucciones, prompt, clave, tope):
    args = [str(binario), '--bare', '--print', '--verbose', '--output-format', 'stream-json',
            '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-chrome',
            '--no-session-persistence', '--model', modelo, '--effort', esfuerzo, '--system-prompt', instrucciones]
    if tope is not None:
        args += ['--max-budget-usd', str(tope)]
    with TemporaryDirectory(prefix='alfalfa-claude-code-') as cwd:
        env = {'HOME': cwd, 'CLAUDE_CONFIG_DIR': cwd, 'PATH': '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',
               'LANG': 'en_US.UTF-8', 'ANTHROPIC_API_KEY': clave, 'ENABLE_CLAUDEAI_MCP_SERVERS': 'false'}
        try:
            async with asyncio.timeout(PLAZO):
                proceso = await asyncio.create_subprocess_exec(
                    *args, cwd=cwd, env=env, start_new_session=True, limit=MAX_LINEA,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    return await _resultado(proceso, prompt)
                finally:
                    try:
                        os.killpg(proceso.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    # Drena cualquier salida después de cancelar o alcanzar el límite de lectura.
                    await proceso.communicate()
        except TimeoutError:
            raise ValueError('Claude Code agotó el tiempo de la intervención.') from None
        except (OSError, ValueError):
            raise ValueError('Claude Code no pudo completar la intervención. '
                             'Comprueba el CLI y la clave API.') from None


class Participante:
    def __init__(self, manager, tema):
        self.manager, self.task, self.tema = manager, manager.task, tema
        self.state = {'messages': []}
        self.historial = []

    def save(self):
        self.manager.save()

    async def run(self, mensaje):
        from .lluvia import PARTICIPAR, REGLAS

        self.state['messages'] = []
        modelo = self.task['model'].removeprefix(PREFIJO)
        binario, clave = ejecutable(), ajustes.anthropic_clave()
        if modelo not in MODELOS or binario is None or not clave:
            raise ValueError('Para usar Claude Code en la sala, instala su CLI y configura la clave API de Anthropic.')
        cuenta = self.manager.presupuesto
        cuenta.comprobar()
        topes = [parte['tope'] - parte['gastado'] for parte in cuenta.estado().values()
                 if isinstance(parte, dict) and parte.get('tope')]
        tope = min(topes, default=None)
        if tope is not None and tope <= 0:
            raise ValueError('El presupuesto restante no alcanza para otra intervención de Claude Code.')
        referencia, _ = adjuntos.para_el_modelo(self.task.get('lluvia_referencia', []), pdf_nativo=False)
        secretos = [clave, *contenido_externo.secretos(self)]
        prompt = json.dumps({'pregunta': self.tema, 'referencia': referencia,
                             'aportes_propios_previos': '\n\n'.join(self.historial)[-MAX_CONTEXTO:],
                             'mensaje_del_moderador': mensaje}, ensure_ascii=False)
        codigo, respuesta = await _consultar(binario, modelo, self.task['effort'], PARTICIPAR + REGLAS,
                                            contenido_externo.limpiar(prompt, secretos), clave, tope)
        if respuesta is not None:
            coste = respuesta.get('total_cost_usd')
            if type(coste) in (int, float) and math.isfinite(coste) and coste >= 0:
                self.task['coste_usd'] = self.task.get('coste_usd', 0) + coste
                cuenta.sumar(self.task, coste)
        texto = (respuesta or {}).get('result')
        if (codigo or (respuesta or {}).get('is_error') is not False
                or not isinstance(texto, str) or not texto.strip()):
            raise ValueError('Claude Code no devolvió un aporte. Comprueba que el modelo, el esfuerzo y la clave API '
                             'sean válidos para tu cuenta.')
        texto = contenido_externo.limpiar(texto, secretos).strip()[:MAX_RESPUESTA]
        self.historial.append(texto)
        self.state['messages'].append({'role': 'assistant', 'content': texto})
        self.manager.event(self.task, 'agent', texto)
