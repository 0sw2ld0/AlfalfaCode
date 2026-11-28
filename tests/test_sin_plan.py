"""`--no-plan` (antes `--sin-plan`, que sigue valiendo): una app que no enseña el plan ni el cierre del turno
(CuyMercado) no se los ofrece al modelo."""
import asyncio

import pytest

from alfalfa.terminal import CIERRE_MANUAL, MANUAL, MANUAL_BASE, SIN_PLAN, TerminalManager, main


@pytest.fixture
def gestor(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    creados = []

    def crear(**opciones):
        m = TerminalManager('Encargo.', home=tmp_path / f'alfalfa{len(creados)}', **opciones)
        creados.append(m)
        return m

    yield crear
    for m in creados:
        m.close()


def test_sin_plan_no_ofrece_el_plan_ni_el_cierre(gestor):
    rt = gestor(sin_plan=True, read_only=True).runtime()
    assert set(SIN_PLAN) <= rt.excluded_tools
    assert 'write_file' in rt.excluded_tools, 'se suma a --solo-lectura, no la sustituye'
    # El manual tampoco pide lo que no tiene: ni wrap_up ni propose_session.
    manual = rt.manual('gpt-6-luna')
    assert manual.startswith(MANUAL_BASE) and CIERRE_MANUAL not in manual
    assert 'wrap_up' not in manual and 'propose_session' not in manual
    # Si el modelo la pide igual (una sesión reanudada que ya la usaba), se rechaza.
    with pytest.raises(ValueError, match='deshabilitada'):
        asyncio.run(rt.tools.call('update_plan', {'plan': [{'step': 'Leer', 'status': 'in_progress'}]}))


def test_por_defecto_sigue_todo(gestor):
    rt = gestor().runtime()
    assert not set(SIN_PLAN) & rt.excluded_tools
    assert MANUAL == MANUAL_BASE + CIERRE_MANUAL
    assert rt.manual('gpt-6-luna').startswith(MANUAL)


def test_la_opcion_sale_en_la_ayuda(capsys):
    with pytest.raises(SystemExit):
        main(['--help'])
    assert '--no-plan' in capsys.readouterr().out
