"""Esperar a que la persona conteste un permiso no gasta el presupuesto de tiempo del turno."""
import asyncio
import time

import pytest

from alfalfa.terminal import TerminalManager


@pytest.fixture
def rt(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    gestor = TerminalManager('Prueba del presupuesto de tiempo.', home=tmp_path / 'alfalfa', auto=True)
    try:
        yield gestor.runtime()
    finally:
        gestor.close()


async def test_la_espera_de_un_permiso_de_la_app_no_cuenta(rt):
    """En la app (--stdio) los permisos van por approval_policy: una tarjeta sin contestar
    cortaba el turno con «Presupuesto de 60 minutos agotado»."""
    limite = 3600

    async def persona(args):
        await asyncio.sleep(0.4)  # la tarjeta espera a que alguien conteste
        return True

    rt.approval_policy = persona
    rt.started = time.monotonic() - (limite - 0.2)  # quedaba muy poco tiempo de trabajo
    await rt.approve({'argv': ['curl', 'https://ejemplo.test'], 'purpose': 'Red', 'motivo': 'red'})
    assert rt.espera_permisos >= 0.4
    assert not rt.presupuesto_agotado(limite), 'la espera no es trabajo'


async def test_la_espera_cuenta_tambien_si_se_niega_o_falla(rt):
    async def niega(args):
        await asyncio.sleep(0.2)
        raise ValueError('Comando rechazado; no se ejecutó.')

    rt.approval_policy = niega
    with pytest.raises(ValueError):
        await rt.approve({'argv': ['ls'], 'purpose': 'Ver', 'motivo': 'modo'})
    assert rt.espera_permisos >= 0.2


async def test_el_tiempo_de_trabajo_sigue_contando(rt):
    rt.started = time.monotonic() - 3601
    assert rt.presupuesto_agotado(3600)
