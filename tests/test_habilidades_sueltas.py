"""Habilidades sueltas (SKILL.md) como las de Claude Code y Codex, sin complemento."""
import json

import pytest

from alfalfa.complementos import cargador as modulo
from alfalfa.complementos.cargador import CargadorComplementos, ErrorComplemento


def skill(carpeta, nombre, descripcion, cuerpo='Pasos.'):
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / 'SKILL.md').write_text('---\nname: ' + nombre + '\ndescription: ' + descripcion + '\n---\n' + cuerpo)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    casa, hogar, proyecto = tmp_path / 'casa', tmp_path / 'hogar', tmp_path / 'proyecto'
    for c in (casa, hogar, proyecto / '.git', proyecto / 'sub'):
        c.mkdir(parents=True)
    monkeypatch.setattr(modulo, 'CASA', casa)
    return casa, hogar, proyecto


def test_lee_las_del_usuario_y_las_del_proyecto(entorno):
    casa, hogar, proyecto = entorno
    skill(casa / '.claude' / 'skills' / 'revisar', 'revisar', 'Revisa un cambio.')
    skill(casa / '.codex' / 'skills' / 'arte', 'cuycards-art', 'Genera arte de CuyCards.')
    skill(casa / '.codex' / 'skills' / '.system' / 'imagegen', 'imagegen', 'Herramienta de Codex.')
    skill(hogar / 'skills' / 'notas', 'notas', 'Toma notas.')
    skill(proyecto / '.agents' / 'skills' / 'publicar', 'publicar', 'Publica la app.')
    skill(proyecto / 'sub' / '.claude' / 'skills' / 'local', 'local', 'Sólo de sub.')
    c = CargadorComplementos(hogar, proyecto / 'sub').cargar()
    ids = sorted(h for p in c.complementos for h in p.habilidades)
    assert ids == ['proyecto-agents:publicar', 'proyecto-claude-sub:local', 'usuario-alfalfa:notas',
                   'usuario-claude:revisar', 'usuario-codex:cuycards-art']  # .system no entra
    texto = c.instrucciones()
    assert '- Habilidad usuario-codex:cuycards-art: cuycards-art — Genera arte de CuyCards.' in texto
    assert 'ábrela antes de empezar y sigue sus pasos' in texto
    abierta = c.abrir_habilidad('usuario-claude:revisar')
    assert abierta['contenido'].endswith('Pasos.')


def test_si_cambia_despues_de_descubrirla_no_se_abre(entorno):
    casa, hogar, proyecto = entorno
    skill(casa / '.claude' / 'skills' / 'x', 'x', 'X.')
    c = CargadorComplementos(hogar, proyecto).cargar()
    (casa / '.claude' / 'skills' / 'x' / 'SKILL.md').write_text('---\nname: x\ndescription: X.\n---\nOtra cosa')
    with pytest.raises(ErrorComplemento):
        c.abrir_habilidad('usuario-claude:x')


def test_un_enlace_que_escapa_no_entra_y_se_pueden_desactivar(entorno, tmp_path):
    casa, hogar, proyecto = entorno
    fuera = tmp_path / 'fuera'
    skill(fuera, 'secreta', 'No debería entrar.')
    carpeta = casa / '.claude' / 'skills'
    carpeta.mkdir(parents=True)
    (carpeta / 'enlace').symlink_to(fuera)
    skill(carpeta / 'buena', 'buena', 'Sí.')
    c = CargadorComplementos(hogar, proyecto).cargar()
    assert sorted(h for p in c.complementos for h in p.habilidades) == ['usuario-claude:buena']
    (hogar / 'complementos.json').write_text(json.dumps({'habilidades_sueltas': False}))
    c = CargadorComplementos(hogar, proyecto).cargar()
    assert c.complementos == [] and c.diagnosticos == []


def test_la_sesion_las_ofrece_al_modelo(entorno, monkeypatch):
    from alfalfa.terminal import TerminalManager
    casa, hogar, proyecto = entorno
    skill(casa / '.claude' / 'skills' / 'revisar', 'revisar', 'Revisa un cambio.')
    monkeypatch.chdir(proyecto)
    m = TerminalManager('x', home=hogar)
    try:
        rt = m.runtime()
        assert 'usuario-claude:revisar' in rt.system_instructions(m.task['model'])
        assert 'abrir_habilidad' in {t['function']['name'] for t in rt.complementos_tools}
    finally:
        m.close()
