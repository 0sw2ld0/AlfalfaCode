import asyncio
from pathlib import Path

import pytest

from alfalfa import apps


def test_usa_la_cli_incluida_y_respeta_la_compatibilidad(tmp_path, monkeypatch):
    desktop = tmp_path / 'alfalfa-desktop'
    anterior = tmp_path / 'alfalfa-apps'
    ayudante = apps.Ayudante(tmp_path)
    monkeypatch.setattr(apps.empaquetado, 'ayudante', lambda nombre: desktop if nombre == desktop.name else None)
    llamadas = []

    async def asegurar():
        return anterior

    async def ejecutar(ruta, orden, argumentos, env=None):
        llamadas.append((ruta, orden, argumentos, env))
        return {'performed': True, 'method': 'background'} if ruta == desktop else {'hecho': True}

    monkeypatch.setattr(ayudante, 'asegurar', asegurar)
    monkeypatch.setattr(ayudante, '_ejecutar', ejecutar)
    resultado = asyncio.run(ayudante.llamar('accion', pid=7, ventana=0, tipo='doble', ruta='1', rol='AXButton',
                                          **{'sin-cursor': True}))
    assert resultado == {'hecho': True, 'como': 'fondo'}
    assert llamadas[0] == (desktop, 'action', {'pid': 7, 'window': 0, 'kind': 'double-click', 'path': '1',
                                             'role': 'AXButton', 'hide-pointer': True, 'approve': True}, None)
    asyncio.run(ayudante.llamar('lista'))
    asyncio.run(ayudante.llamar('accion', pid=7, tipo='pulsar', accion='AXConfirm', ruta='1', rol='AXButton'))
    assert [(ruta, orden) for ruta, orden, *_ in llamadas[1:]] == [(anterior, 'lista'), (anterior, 'accion')]


@pytest.mark.parametrize('situacion', ['imagen', 'permiso', 'externa'])
def test_captura_del_ayudante_va_al_archivo_de_la_sesion(tmp_path, monkeypatch, situacion):
    desktop = tmp_path / 'alfalfa-desktop'
    destino = tmp_path / 'vista-app.png'
    fuera = tmp_path / 'fuera.png'
    fuera.write_bytes(b'original')
    ayudante = apps.Ayudante(tmp_path)
    monkeypatch.setattr(apps.empaquetado, 'ayudante', lambda nombre: desktop if nombre == desktop.name else None)
    temporal = None

    async def ejecutar(ruta, orden, argumentos, env):
        nonlocal temporal
        assert ruta == desktop and orden == 'inspect'
        assert argumentos == {'pid': 7, 'window': 0, 'max': 400, 'approve': True, 'capture': True}
        temporal = Path(env['TMPDIR'])
        imagen = temporal / 'alfalfa-desktop-fixture' / 'window.png'
        imagen.parent.mkdir()
        imagen.write_bytes(b'captura')
        datos = {'window': 0, 'tree': '[0] Window', 'height': 140, 'width': 250,
                 'elements': [{'index': 1, 'path': '0', 'role': 'AXButton', 'title': 'Sumar'}]}
        if situacion == 'permiso':
            return dict(datos, screenRecordingRequired=True, captureError='Screen Recording permission is required.')
        return dict(datos, capture=str(fuera if situacion == 'externa' else imagen))

    monkeypatch.setattr(ayudante, '_ejecutar', ejecutar)
    estado = asyncio.run(ayudante.llamar('estado', pid=7, ventana=0, max=400, captura=destino))
    assert estado['elementos'] == [{'i': 1, 'ruta': '0', 'rol': 'AXButton', 'titulo': 'Sumar'}]
    assert estado['ancho'] == 250 and temporal is not None and not temporal.exists()
    assert fuera.read_bytes() == b'original'
    if situacion == 'imagen':
        assert estado['captura'] == str(destino) and destino.read_bytes() == b'captura'
    elif situacion == 'permiso':
        assert estado['captura_pide_permiso'] and 'Grabación de pantalla' in estado['captura_error']
    else:
        assert 'ruta de captura inválida' in estado['captura_error']
    assert destino.exists() == (situacion == 'imagen')


def test_error_json_de_la_cli_no_repite_la_accion_con_el_ayudante_anterior(tmp_path, monkeypatch):
    desktop = tmp_path / 'alfalfa-desktop'
    desktop.write_text('#!/bin/sh\necho \'{"error":"La ventana cambió"}\' >&2\nexit 1\n')
    desktop.chmod(0o700)
    monkeypatch.setattr(apps.empaquetado, 'ayudante', lambda nombre: desktop if nombre == desktop.name else None)
    with pytest.raises(ValueError, match='La ventana cambió'):
        asyncio.run(apps.Ayudante(tmp_path).llamar('accion', pid=7, tipo='clic', x=5, y=5))
