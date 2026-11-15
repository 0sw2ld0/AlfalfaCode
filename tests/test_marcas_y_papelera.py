"""Marcar y eliminar sesiones a la vez que otra cosa no pierde nada.

- Dos `--marcar` a la vez (fijar y renombrar desde la barra lateral) leían las dos el mismo
  marcas.json y la segunda en escribir borraba la marca de la primera.
- `--eliminar-sesion` miraba si la sesión estaba en ejecución y después la movía: una que arrancara
  entre medias acababa en la Papelera mientras trabajaba.
"""
import fcntl
import json
import os
import threading
import time
from pathlib import Path

from alfalfa import stdio


def sesion(hogar, ident='abc'):
    d = hogar / 'sesiones' / ident
    d.mkdir(parents=True)
    (d / 'task.json').write_text(json.dumps(dict(id=ident, objective='x', cwd='/repo', status='completed',
                                                 model='m', effort='low', updated_at=1)))
    return d


def test_dos_marcas_a_la_vez_quedan_las_dos(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    d = sesion(tmp_path)
    leer = stdio._marcas

    def lenta(directorio):
        datos = leer(directorio)
        time.sleep(0.2)  # mientras, la otra también lee
        return datos
    monkeypatch.setattr(stdio, '_marcas', lenta)
    hilos = [threading.Thread(target=stdio.marcar, args=('abc', 'titulo', 'Nuevo')),
             threading.Thread(target=stdio.marcar, args=('abc', 'fijada', 'sí'))]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert json.loads((d / 'marcas.json').read_text()) == {'titulo': 'Nuevo', 'fijada': True}


def test_se_puede_marcar_una_sesion_en_marcha(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    d = sesion(tmp_path)
    fd = os.open(d / 'lock', os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)  # el motor de la sesión
    try:
        assert stdio.marcar('abc', 'fijada', 'sí') == {'fijada': True}
    finally:
        os.close(fd)


def test_eliminar_tiene_el_cerrojo_de_la_sesion_mientras_la_mueve(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HOME', str(tmp_path))
    d = sesion(tmp_path / 'home')
    al_mover = []
    mover = Path.rename

    def rename(self, destino):
        # Una sesión que arranca justo ahora: ¿podría tomar el cerrojo?
        fd = os.open(self / 'lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            al_mover.append('libre')
        except OSError:
            al_mover.append('tomado')
        finally:
            os.close(fd)
        return mover(self, destino)
    monkeypatch.setattr(Path, 'rename', rename)
    destino = stdio.eliminar('abc')
    assert al_mover == ['tomado'], 'una sesión podía arrancar mientras se movía a la Papelera'
    assert (destino / 'task.json').is_file() and not d.exists()
