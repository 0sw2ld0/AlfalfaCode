"""El motor dentro de AlfalfaCode.app: se reconoce y usa los ayudantes ya compilados."""
import asyncio
import os

from alfalfa import apps, empaquetado
from alfalfa.proveedores import alfalfa as proveedor_alfalfa


def _app(tmp_path, nombre='AlfalfaCode.app'):
    """Un bundle con la forma que deja build-app.sh; devuelve (Contents, carpeta del paquete)."""
    contenidos = tmp_path / nombre / 'Contents'
    paquete = contenidos / 'Resources' / 'motor' / 'python' / 'lib' / 'python3.13' / 'site-packages' / 'alfalfa'
    paquete.mkdir(parents=True)
    (contenidos / 'Helpers').mkdir()
    return contenidos, paquete


def _ejecutable(ruta):
    ruta.write_text('#!/bin/sh\n')
    os.chmod(ruta, 0o755)
    return ruta


def test_reconoce_el_paquete_dentro_de_la_app(tmp_path):
    contenidos, paquete = _app(tmp_path)
    assert empaquetado.contenidos(paquete) == contenidos
    assert empaquetado.en_la_app(paquete)


def test_fuera_de_una_app_no_se_confunde(tmp_path):
    # Un clon, un site-packages cualquiera, o un «motor» que no está en un .app.
    assert not empaquetado.en_la_app(tmp_path / 'AlfalfaCode' / 'alfalfa')
    assert not empaquetado.en_la_app(tmp_path / 'venv' / 'lib' / 'python3.13' / 'site-packages' / 'alfalfa')
    otro = tmp_path / 'Contents' / 'Resources' / 'motor' / 'python' / 'site-packages' / 'alfalfa'
    assert not empaquetado.en_la_app(otro)
    # Y el propio paquete de las pruebas, que vive en el clon.
    assert not empaquetado.en_la_app()


def test_el_ayudante_incluido_se_usa_solo_si_es_ejecutable(tmp_path):
    contenidos, paquete = _app(tmp_path)
    assert empaquetado.ayudante('alfalfa-firma-abc', paquete) is None
    (contenidos / 'Helpers' / 'alfalfa-firma-abc').write_text('no ejecutable')
    assert empaquetado.ayudante('alfalfa-firma-abc', paquete) is None
    ruta = _ejecutable(contenidos / 'Helpers' / 'alfalfa-firma-abc')
    assert empaquetado.ayudante('alfalfa-firma-abc', paquete) == ruta
    assert empaquetado.ayudante('alfalfa-firma-abc', tmp_path / 'clon' / 'alfalfa') is None


def test_el_firmante_no_compila_si_la_app_trae_el_ayudante(tmp_path, monkeypatch):
    firmante = proveedor_alfalfa.Firmante(tmp_path / 'hogar')
    incluido = _ejecutable(tmp_path / 'incluido')
    vistos = []
    monkeypatch.setattr(empaquetado, 'ayudante', lambda nombre: vistos.append(nombre) or incluido)
    monkeypatch.setattr(proveedor_alfalfa.shutil, 'which', lambda *_: (_ for _ in ()).throw(AssertionError('swiftc')))
    assert firmante.asegurar() == incluido
    assert vistos == [firmante.binario.name]  # mismo nombre que en $ALFALFA_HOME/bin: prefijo y huella


def test_el_control_de_apps_no_compila_si_la_app_trae_el_ayudante(tmp_path, monkeypatch):
    ayudante = apps.Ayudante(tmp_path / 'hogar')
    incluido = _ejecutable(tmp_path / 'incluido')
    monkeypatch.setattr(empaquetado, 'ayudante', lambda nombre: incluido if nombre == ayudante.ruta.name else None)
    monkeypatch.setattr(apps.shutil, 'which', lambda *_: (_ for _ in ()).throw(AssertionError('swiftc')))
    assert asyncio.run(ayudante.asegurar()) == incluido
