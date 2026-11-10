"""Frase sugerida, como la de la app de Codex: wrap_up propone el siguiente mensaje del usuario.

Sale en gris en el cuadro vacío y sólo se envía si el usuario la acepta y la manda él. Nunca
puede autorizar en su nombre algo irreversible o que sale fuera: esa se descarta.
"""
import asyncio

import pytest

from alfalfa.proveedores.nativo.herramientas import (
    MAX_SUGERENCIA,
    ON_REQUEST_TOOLS,
    cierre_de_turno,
    sugerencia_arriesgada,
)
from alfalfa.terminal import MANUAL, TerminalManager, texto_del_cierre


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def wrap_up():
    return next(t['function'] for t in ON_REQUEST_TOOLS if t['function']['name'] == 'wrap_up')


def test_la_herramienta_y_las_instrucciones_la_explican():
    funcion = wrap_up()
    assert 'suggestion' in funcion['parameters']['properties']
    assert 'suggestion' not in funcion['parameters'].get('required', [])
    assert 'Never put an authorization' in funcion['description']
    assert 'suggestion' in MANUAL and '«autorizo»' in MANUAL


def test_el_cierre_lleva_la_sugerencia_en_una_linea():
    cierre = cierre_de_turno({'recap': 'Listo.', 'suggestion': '  Añade pruebas\n para la resta. '})
    assert cierre == {'resumen': 'Listo.', 'sugerencia': 'Añade pruebas para la resta.'}
    # Va sin pregunta ni botones, y con ellos también.
    cierre = cierre_de_turno({'recap': 'Listo.', 'ask': '¿Sigo?', 'options': ['Sí'],
                              'suggestion': '¿Por qué falla el test de login?'})
    assert cierre['sugerencia'] == '¿Por qué falla el test de login?' and cierre['opciones'] == ['Sí']
    assert 'sugerencia' not in cierre_de_turno({'recap': 'Listo.', 'suggestion': '   '})


def test_rechaza_una_sugerencia_demasiado_larga():
    with pytest.raises(ValueError, match='una sola línea corta'):
        cierre_de_turno({'recap': 'Listo.', 'suggestion': 'x' * (MAX_SUGERENCIA + 1)})


@pytest.mark.parametrize('texto', [
    # El ejemplo real de Codex: redacta una autorización que el usuario no ha dado.
    'Sí, autorizo explícitamente subir el commit aaba931 a origin/main y cerrar el PR #38.',
    'Autorizo el despliegue.',
    'Apruebo los cambios, adelante.',
    'Te doy permiso para borrar la carpeta build.',
    'Tienes mi permiso para seguir.',
    'Sí, sube la rama.',
    'Sí, borra los archivos temporales.',
    'Vale, publícalo.',
    'Adelante, envía el informe al cliente.',
    'Sí. Haz commit y luego elimina la carpeta dist.',
    'Haz push a origin.',
    'Sube el commit a main.',
    'Fusiona el PR cuando pase CI.',
    'Publica la versión 1.2 en PyPI.',
    'Despliega alfalfa-cloud.',
    'Cierra el PR #38.',
    'Borra la rama vieja del remoto.',
    'Manda el correo al equipo.',
    'git reset --hard origin/main',
    'Ejecuta rm -rf build',
    'Compra el dominio.',
    'Yes, push it.',
    'I authorize the release.',
    'Go ahead and delete the old files.',
    'Approved, merge it.',
])
def test_descarta_las_que_autorizan_por_el_usuario(texto):
    assert sugerencia_arriesgada(texto)
    assert 'sugerencia' not in cierre_de_turno({'recap': 'Listo.', 'suggestion': texto})


@pytest.mark.parametrize('texto', [
    'Añade también pruebas para la resta y la división por cero.',
    '¿Por qué falla el test de login en Python 3.13?',
    'Revisa la autorización OAuth del cliente.',
    'Explica qué hace merge_sort en utils.py.',
    'Ahora ordena la lista de sesiones por fecha.',
    'Si el test sigue fallando, mira el log completo.',
    'Haz lo mismo con el módulo de descargas.',
    'Escribe la documentación de la API pública.',
    'Muéstrame el diff antes de seguir.',
])
def test_deja_pasar_las_normales(texto):
    assert not sugerencia_arriesgada(texto)
    assert cierre_de_turno({'recap': 'Listo.', 'suggestion': texto})['sugerencia'] == texto


def test_la_herramienta_avisa_al_modelo_si_la_descarta(m):
    rt = m.runtime()
    resultado = asyncio.run(rt.tools.call('wrap_up', {'recap': 'Listo.', 'suggestion': 'Sí, sube la rama.'}))
    assert 'Descartada' in resultado['sugerencia']
    assert 'sugerencia' not in m.task['cierre']
    resultado = asyncio.run(rt.tools.call('wrap_up', {'recap': 'Listo.', 'suggestion': 'Añade la resta.'}))
    assert 'sugerencia' not in resultado
    assert m.task['cierre'] == {'resumen': 'Listo.', 'sugerencia': 'Añade la resta.'}


def test_la_terminal_la_muestra():
    texto = texto_del_cierre({'resumen': 'Listo.', 'pregunta': '¿Sigo?', 'opciones': ['Sí'],
                              'sugerencia': 'Añade la resta.'})
    assert texto.splitlines()[1:] == ['── Siguiente paso ──', 'Listo.', '¿Sigo?', 'Responde: «Sí»',
                                      'Sugerencia: Añade la resta.']


def test_la_sesion_guardada_la_conserva(tmp_path, monkeypatch):
    import json

    from alfalfa import sesiones as registro
    from alfalfa.stdio import history
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    carpeta = tmp_path / 'sesiones' / 'abc'
    carpeta.mkdir(parents=True)
    task = {'id': 'abc', 'objective': 'Arregla suma.py', 'status': 'completed'}
    turno = registro.abrir_turno(task, 'Arregla suma.py', 0)
    registro.cerrar_turno(task, turno['id'], 'completed', 1,
                          cierre={'resumen': 'Listo.', 'sugerencia': 'Añade la resta.'})
    (carpeta / 'task.json').write_text(json.dumps(task))
    (carpeta / 'abc.jsonl').write_text(json.dumps({'role': 'agent', 'text': 'Hecho.', 'time': 1,
                                                   'turno': turno['id']}) + '\n')
    assert history('abc')['cierre'] == {'resumen': 'Listo.', 'sugerencia': 'Añade la resta.'}
