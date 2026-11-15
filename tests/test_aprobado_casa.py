"""Lo aprobado vale para la misma orden aunque se escriba un poco distinto, y la tarjeta del modo
Manual ofrece «No volver a preguntar».

Antes:
- la clave «en esta sesión» guardaba la carpeta y las rutas tal cual: `pytest -q` aprobado en
  «server» volvía a preguntar en «server/» o «./server», y la edición de `src/mod.py`, en `./src/mod.py`;
- la tarjeta del modo Manual (`npm test`, `make`) nunca ofrecía «No volver a preguntar».
"""
import asyncio
import os
import subprocess
import sys

import pytest

from alfalfa import reglas
from alfalfa.permisos import sesion
from alfalfa.permisos.bajo_demanda import SIEMPRE
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.terminal import TerminalManager

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')


# ---------------------------------------------------------------- claves normalizadas
@pytest.mark.parametrize('cwd', ['server/', './server', 'server/.', 'server//'])
def test_la_misma_carpeta_da_la_misma_clave(cwd):
    assert sesion.clave_de_orden(['pytest', '-q'], cwd) == sesion.clave_de_orden(['pytest', '-q'], 'server')


@pytest.mark.parametrize('cwd', [None, '', '.', './'])
def test_la_raiz_es_siempre_la_misma_clave(cwd):
    assert sesion.clave_de_orden(['ls'], cwd) == sesion.clave_de_orden(['ls'])


def test_otra_carpeta_sigue_siendo_otra_clave():
    assert sesion.clave_de_orden(['pytest'], 'server') != sesion.clave_de_orden(['pytest'], 'client')
    assert sesion.clave_de_orden(['pytest'], 'server') != sesion.clave_de_orden(['pytest'], 'server/sub')


def test_el_mismo_archivo_da_la_misma_clave_de_edicion():
    assert sesion.claves_de_edicion(['./src/mod.py']) == sesion.claves_de_edicion(['src/mod.py'])
    assert sesion.claves_de_edicion(['src//mod.py']) == sesion.claves_de_edicion(['src/mod.py'])
    assert sesion.claves_de_edicion(['src/otro.py']) != sesion.claves_de_edicion(['src/mod.py'])


def test_una_clave_guardada_antes_sigue_valiendo():
    # Las sesiones guardadas antes del cambio tienen claves sin normalizar de rutas ya normales.
    vieja = '["orden",["pytest","-q"],"server",false,false]'
    assert sesion.clave_de_orden(['pytest', '-q'], 'server') == vieja


# ---------------------------------------------------------------- la regla dentro del sandbox
def test_guardar_dentro_del_sandbox_no_lleva_fuera_del_sandbox(tmp_path):
    nuevas = reglas.guardar_para_siempre(tmp_path, ['make', 'test'], 'x', dentro_del_sandbox=True)
    assert nuevas.reglas[-1].decision == reglas.PERMITIDA and nuevas.reglas[-1].sin_sandbox is False
    assert 'fuera_del_sandbox' not in (tmp_path / reglas.ARCHIVO).read_text()


class Respuesta:
    def __init__(self, respuesta):
        self.respuesta, self.peticiones, self.denied = respuesta, [], 0

    async def __call__(self, args):
        self.peticiones.append(dict(args))
        return self.respuesta


@pytest.fixture
def manual(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    (raiz / 'server').mkdir(parents=True)
    (raiz / 'Makefile').write_text('test:\n\t@echo probado\n')
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    monkeypatch.chdir(raiz)
    hogar = tmp_path / 'alfalfa'
    m = TerminalManager('Prueba.', home=hogar)
    m.task['modo'] = 'manual'
    rt = m.runtime()

    def llamar(aprobacion, **argumentos):
        rt.approval_policy = aprobacion
        return asyncio.run(rt.tools.call('run_command', dict(purpose='Prueba', **argumentos)))

    try:
        yield llamar, raiz, hogar
    finally:
        m.close()


@real
def test_la_tarjeta_de_manual_ofrece_no_volver_a_preguntar_y_sigue_en_el_sandbox(manual):
    llamar, raiz, hogar = manual
    primera = Respuesta(SIEMPRE)
    resultado = llamar(primera, argv=['make', 'test'], prefix_rule=['make', 'test'])
    peticion, = primera.peticiones
    assert peticion['motivo'] == 'modo' and peticion['prefijo'] == ['make', 'test']
    assert resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
    guardada = reglas.desde_hogar(hogar).reglas[-1]
    assert guardada.texto() == 'make test' and not guardada.sin_sandbox
    otra = Respuesta(None)
    resultado = llamar(otra, argv=['make', 'test'])
    assert otra.peticiones == [] and resultado['sandbox'] == 'seatbelt'


@real
def test_lo_aprobado_en_la_sesion_vale_con_la_carpeta_escrita_de_otra_forma(manual):
    llamar, raiz, hogar = manual
    primera = Respuesta(sesion.SESION)
    llamar(primera, argv=['touch', 'x.txt'], cwd='server')
    assert len(primera.peticiones) == 1
    for cwd in ('server/', './server'):
        otra = Respuesta(None)
        resultado = llamar(otra, argv=['touch', 'x.txt'], cwd=cwd)
        assert otra.peticiones == [] and resultado['exit_code'] == 0, cwd
