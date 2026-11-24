"""Agente simulado para la demo del perfil `ui`: el transporte PACAI real de AlfalfaCode con un «modelo» de guion.

Habla PACAI 0.3 por stdio igual que `alfalfa --pacai` (mismo `TransportePacai` y mismo traductor), pero en vez de
llamar a un modelo responde con bloques ```pacai-ui escritos a mano. Sirve para probar la demo sin claves ni gasto.

    .venv/bin/python docs/demos/pacai-ui/agente_simulado.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from alfalfa.pacai.transporte import TransportePacai  # noqa: E402

TICKETS = [
    {'id': 'T-12', 'title': 'Login falla en Safari', 'priority': 'Alta'},
    {'id': 'T-15', 'title': 'Exportar CSV vacío', 'priority': 'Media'},
    {'id': 'T-18', 'title': 'Texto cortado en móvil', 'priority': 'Baja'},
]
PRIORIDAD = {'high': 'Alta', 'medium': 'Media', 'low': 'Baja'}
_ACCION = re.compile(r'\[Acción en la pantalla\] (\{.*\})', re.S)


def nodo(ident, componente, props=None, children=None, events=None) -> str:
    n = {'id': ident, 'component': componente}
    if props is not None:
        n['props'] = props
    if children is not None:
        n['children'] = children
    if events is not None:
        n['events'] = events
    return json.dumps(n, ensure_ascii=False)


def valla(vista, *lineas, placement=None) -> str:
    cabecera = '```pacai-ui view=' + vista + (' placement=' + placement if placement else '')
    return '\n'.join([cabecera, *lineas, '```'])


class Guion:
    def __init__(self):
        self.tickets = [dict(t) for t in TICKETS]
        self.siguiente = 21

    def titulo(self):
        return nodo('titulo', 'Text', {'text': f'{len(self.tickets)} tickets abiertos', 'style': 'title'})

    def tabla(self):
        return nodo('tabla', 'Table', {'columns': [{'key': 'id', 'label': 'Id'}, {'key': 'title', 'label': 'Título'},
                                                   {'key': 'priority', 'label': 'Prioridad'}],
                                       'rows': self.tickets},
                    events={'row_press': {'send': 'abrir_ticket'}})

    def grafico(self):
        cuenta = [sum(1 for t in self.tickets if t['priority'] == p) for p in ('Alta', 'Media', 'Baja')]
        return nodo('grafico', 'Chart', {'kind': 'bar', 'labels': ['Alta', 'Media', 'Baja'],
                                         'series': [{'name': 'Tickets', 'values': cuenta}]})

    def responder(self, texto: str) -> str:
        accion = _ACCION.search(texto)
        if accion:
            datos = json.loads(accion.group(1))
            return getattr(self, 'a_' + datos['name'], self.desconocida)(datos)
        if re.search(r'gr[aá]fic|resumen|prioridad', texto, re.I):
            return 'Así se reparten por prioridad:\n\n' + valla('resumen', nodo('root', 'Card', {
                'title': 'Tickets por prioridad'}, ['grafico']), self.grafico())
        return ('Aquí tienes los tickets abiertos. Pulsa una fila para ver el detalle, o crea uno nuevo.\n\n'
                + valla('tickets',
                        nodo('root', 'Stack', {'gap': 'medium'}, ['titulo', 'tabla', 'nuevo']),
                        self.titulo(), self.tabla(),
                        nodo('nuevo', 'Form', {'submit_label': 'Crear ticket'}, ['f_title', 'f_priority'],
                             {'submit': {'send': 'crear_ticket', 'with': ['title', 'priority']}}),
                        nodo('f_title', 'Input', {'name': 'title', 'label': 'Título', 'required': True,
                                                  'placeholder': 'Qué pasa'}),
                        nodo('f_priority', 'Select', {'name': 'priority', 'label': 'Prioridad', 'options': [
                            {'value': 'high', 'label': 'Alta'}, {'value': 'medium', 'label': 'Media'},
                            {'value': 'low', 'label': 'Baja'}]}),
                        '{"state":{"title":"","priority":"medium"}}')
                + '\n\nTambién puedo enseñarte un gráfico por prioridad.')

    def a_crear_ticket(self, datos):
        titulo = str(datos['values'].get('title') or '').strip()
        if not titulo:
            return 'Le falta el título al ticket. Escríbelo y vuelve a pulsar «Crear ticket».'
        ticket = {'id': f'T-{self.siguiente}', 'title': titulo,
                  'priority': PRIORIDAD.get(datos['values'].get('priority'), 'Media')}
        self.siguiente += 1
        self.tickets.append(ticket)
        return (f'Creado {ticket["id"]}: «{titulo}».\n\n'
                + valla('tickets', self.titulo(), self.tabla(), '{"state":{"title":""}}', placement='latest'))

    def a_abrir_ticket(self, datos):
        fila = datos.get('item') or {}
        ticket = next((t for t in self.tickets if t['id'] == fila.get('id')), None)
        if ticket is None:
            return 'Ese ticket ya no está abierto.'
        return valla('detalle',
                     nodo('root', 'Card', {'title': ticket['id'], 'subtitle': 'Prioridad ' + ticket['priority']},
                          ['texto', 'acciones']),
                     nodo('texto', 'Text', {'text': ticket['title']}),
                     nodo('acciones', 'Stack', {'direction': 'horizontal', 'gap': 'small'}, ['cerrar']),
                     nodo('cerrar', 'Button', {'label': 'Cerrar ' + ticket['id'], 'variant': 'danger'},
                          events={'press': [{'set': {'ticket': ticket['id']}},
                                            {'send': 'cerrar_ticket', 'with': ['ticket']}]}),
                     placement='latest')

    def a_cerrar_ticket(self, datos):
        ident = datos['values'].get('ticket')
        antes = len(self.tickets)
        self.tickets = [t for t in self.tickets if t['id'] != ident]
        if len(self.tickets) == antes:
            return f'{ident} ya estaba cerrado.'
        return (f'Cerrado {ident}.\n\n' + valla('tickets', self.titulo(), self.tabla())
                + '\n' + valla('detalle', nodo('root', 'Text', {'text': f'{ident} está cerrado.', 'style': 'muted'})))

    def desconocida(self, datos):
        return 'No sé qué hacer con la acción «' + datos.get('name', '') + '».'


async def main():
    loop = asyncio.get_running_loop()
    t = TransportePacai(sys.stdout)
    guion = Guion()

    def leer():
        for linea in sys.stdin:
            loop.call_soon_threadsafe(t.receive, linea)
        loop.call_soon_threadsafe(t.stop)

    loop.run_in_executor(None, leer)
    while True:
        mensaje = await t.next_message()
        if mensaje is None:
            break
        t.turno_empieza(mensaje)
        await asyncio.sleep(0.4)                                   # que se note que «piensa»
        t.emit('evento', rol='agent', texto=guion.responder(str(mensaje)), hora=time.time())
        t.emit('turno_terminado', estado='completed', resumen='Hecho', codigo_salida=0, tokens=0)
        if t._cerrando is not None:
            await asyncio.shield(t._cerrando)
    t.emit('fin', estado='completed', resumen='Sesión cerrada', codigo_salida=0)


if __name__ == '__main__':
    asyncio.run(main())
