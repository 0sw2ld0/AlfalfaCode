"""La limpieza diaria no borra las instantáneas de un proyecto en un disco desconectado.

Antes `limpiar_todo` borraba el almacén de toda carpeta que no existiera en ese momento: con el disco
externo sin montar, se perdían todas las instantáneas (y con ellas «deshacer») de sus proyectos.
"""
from uuid import uuid4

from alfalfa.deshacer import instantaneas
from alfalfa.deshacer.instantaneas import Almacen


def almacen_de(hogar, carpeta, origen):
    carpeta.mkdir(parents=True)
    a = Almacen(hogar, carpeta)
    a.guardar('s1', 'x')
    (a.dir / 'carpeta').write_text(str(origen))  # como si el proyecto estuviera en `origen`
    return a.dir


def test_un_disco_desconectado_conserva_sus_instantaneas(tmp_path):
    hogar = tmp_path / 'hogar'
    sin_montar = '/Volumes/AlfalfaSinMontar-' + uuid4().hex[:8] + '/proyecto'
    almacen = almacen_de(hogar, tmp_path / 'p1', sin_montar)
    instantaneas.limpiar_todo(hogar)
    assert almacen.is_dir() and (almacen / 'HEAD').is_file()


def test_una_carpeta_borrada_se_sigue_limpiando(tmp_path):
    hogar = tmp_path / 'hogar'
    almacen = almacen_de(hogar, tmp_path / 'p2', tmp_path / 'ya-no-existe')
    instantaneas.limpiar_todo(hogar)
    assert not almacen.exists()


def test_disco_montado(tmp_path):
    from alfalfa.deshacer.instantaneas import _disco_montado
    volumenes = tmp_path / 'Volumes'
    (volumenes / 'Externo').mkdir(parents=True)
    assert _disco_montado(volumenes / 'Externo' / 'borrado', volumenes)
    assert not _disco_montado(volumenes / 'Otro' / 'proyecto', volumenes)
    assert _disco_montado(tmp_path / 'fuera' / 'proyecto', volumenes)
