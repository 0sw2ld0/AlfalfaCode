"""Conformidad con PACAI 0.3: los ejemplos del protocolo (tests/fixtures/pacai, Apache-2.0, sin tocar) y el digest.

`valid/` debe pasar el esquema y las reglas de sesión; `invalid/mensajes.jsonl` debe fallar el esquema y cada
sesión de `invalid/sesiones/` debe rechazarse por el motivo que dice su primera línea.
"""
import hashlib
import json
from pathlib import Path

import pytest

from alfalfa.pacai.esquema import validar
from alfalfa.pacai.jcs import canonicalize, digest
from alfalfa.pacai.verificador import Session

EJEMPLOS = Path(__file__).with_name('fixtures') / 'pacai'


def leer(ruta):
    return [json.loads(linea) for linea in ruta.read_text(encoding='utf-8').splitlines() if linea.strip()]


@pytest.mark.parametrize('ruta', sorted((EJEMPLOS / 'valid').glob('*.jsonl')), ids=lambda r: r.name)
def test_las_sesiones_validas_se_aceptan(ruta):
    assert Session.check(leer(ruta)) == []


def test_hay_ejemplos():
    assert len(list((EJEMPLOS / 'valid').glob('*.jsonl'))) >= 10
    assert len(list((EJEMPLOS / 'invalid' / 'sesiones').glob('*.jsonl'))) >= 15


INVALIDOS = leer(EJEMPLOS / 'invalid' / 'mensajes.jsonl')


@pytest.mark.parametrize('mensaje', INVALIDOS, ids=[m['x_motivo'] for m in INVALIDOS])
def test_los_mensajes_invalidos_se_rechazan(mensaje):
    mensaje = dict(mensaje)
    mensaje.pop('x_motivo')
    assert validar(mensaje)


@pytest.mark.parametrize('ruta', sorted((EJEMPLOS / 'invalid' / 'sesiones').glob('*.jsonl')), ids=lambda r: r.name)
def test_las_sesiones_invalidas_se_rechazan_por_su_motivo(ruta):
    mensajes = leer(ruta)
    motivo = mensajes.pop(0)['x_motivo']
    fallos = Session.check(mensajes)
    assert any(motivo in f for f in fallos), (motivo, fallos)


def test_digest_es_sha256_de_los_argumentos_canonizados():
    args = {'b': [1, 2.5, 'ñ'], 'a': {'y': True, 'x': None}}
    assert canonicalize(args) == '{"a":{"x":null,"y":true},"b":[1,2.5,"ñ"]}'
    esperado = hashlib.sha256('{"a":{"x":null,"y":true},"b":[1,2.5,"ñ"]}'.encode()).hexdigest()
    assert digest(args) == 'sha256:' + esperado
    # El orden de las claves no cambia el digest; un argumento distinto, sí.
    assert digest({'a': 1, 'b': 2}) == digest({'b': 2, 'a': 1})
    assert digest({'a': 1}) != digest({'a': 2})


@pytest.mark.parametrize('valor, texto', [
    (1e30, '1e+30'), (4.5, '4.5'), (0.002, '0.002'), (100, '100'), (-0.0, '0'), (1e-7, '1e-7'),
    (123456789012345680000.0, '123456789012345680000')])
def test_jcs_escribe_los_numeros_como_ecmascript(valor, texto):
    assert canonicalize(valor) == texto


def test_jcs_rechaza_lo_que_no_es_json():
    for malo in (float('nan'), float('inf'), {1: 'a'}, {'a': {1, 2}}):
        with pytest.raises((ValueError, TypeError)):
            canonicalize(malo)
