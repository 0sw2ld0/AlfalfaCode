"""`alfalfa --aligerar-sesiones`: pasa todas las sesiones guardadas al formato ligero.

Una sesión vieja sólo se convierte cuando se vuelve a abrir, y su handoff.json
sólo se acota cuando el agente usa otra herramienta. Así, las sesiones que ya no
se tocan seguían ocupando lo mismo (una llegó a 581 MB). Esta orden hace con
todas a la vez lo mismo que hace AlfalfaCode al abrirlas:

- session.json pasa a la versión 3: la conversación y las fotos del espacio de
  trabajo van a `messages.jsonl` y `fotos.jsonl` (ver `proveedores/nativo/historial.py`).
- Las resoluciones «not_relevant», que no deciden nada, salen de la sesión a
  `resoluciones-no-relevantes.jsonl`; con `descartar` ni se guardan.
- handoff.json se acota como lo escribe ahora el registro de evidencia.

No pierde nada (salvo lo que se pida descartar) y no toca las sesiones que
están en marcha: las salta y lo dice.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


def _tamaño(carpeta):
    total = 0
    for raiz, _, archivos in os.walk(carpeta):
        for nombre in archivos:
            try:
                total += os.lstat(os.path.join(raiz, nombre)).st_size
            except OSError:
                continue
    return total


def aligerar_nativa(directorio, descartar=False):
    """Una carpeta `native/<id>`: session.json a la versión 3 y handoff.json acotado."""
    from ..evidencia.registro import acotar_handoff, archivar_no_relevantes
    from ..proveedores.nativo import historial
    from ..proveedores.nativo.herramientas import atomic_json
    directorio = Path(directorio)
    ruta = directorio / 'session.json'
    if ruta.is_file() and not ruta.is_symlink():
        guardado = json.loads(ruta.read_text())
        vieja = isinstance(guardado, dict) and guardado.get('version') != historial.VERSION
        tiene_no_relevantes = isinstance(guardado, dict) and any(
            isinstance(fila, dict) and fila.get('status') == 'not_relevant'
            for fila in guardado.get('resolution_records') or [])
        if vieja or tiene_no_relevantes:
            estado = historial.leer(ruta)
            archivar_no_relevantes(estado, directorio, descartar)
            # Como al abrirla: primero los JSONL, luego session.json.
            historial.escribir(directorio, dict(estado, version=historial.VERSION))
        elif descartar:
            archivar_no_relevantes({}, directorio, descartar)
    entrega = directorio / 'handoff.json'
    if entrega.is_file() and not entrega.is_symlink():
        valor = json.loads(entrega.read_text())
        if isinstance(valor, dict):
            acotado = acotar_handoff(valor)
            if acotado != valor:
                atomic_json(entrega, acotado)


def aligerar(sesiones, descartar=False):
    """Todas las sesiones de la carpeta `sesiones`. Devuelve una fila por sesión."""
    filas = []
    sesiones = Path(sesiones)
    if not sesiones.is_dir():
        return filas
    for carpeta in sorted(sesiones.iterdir()):
        if not carpeta.is_dir() or carpeta.is_symlink():
            continue
        fila = {'id': carpeta.name, 'antes': _tamaño(carpeta)}
        try:
            cerrojo = os.open(carpeta / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            filas.append(dict(fila, despues=fila['antes'], estado='error', error=str(exc)))
            continue
        try:
            try:
                fcntl.flock(cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                filas.append(dict(fila, despues=fila['antes'], estado='en_marcha'))
                continue
            try:
                nativas = carpeta / 'native'
                if nativas.is_dir() and not nativas.is_symlink():
                    for nativa in sorted(nativas.iterdir()):
                        if nativa.is_dir() and not nativa.is_symlink():
                            aligerar_nativa(nativa, descartar)
                fila['estado'] = 'aligerada'
            except (OSError, ValueError) as exc:
                # Una sesión rota no para las demás; se queda como estaba.
                fila.update(estado='error', error=str(exc)[:300])
        finally:
            os.close(cerrojo)
        fila['despues'] = _tamaño(carpeta)
        filas.append(fila)
    return filas


def _mb(n):
    return f'{n / 1e6:,.1f} MB'.replace(',', ' ')


def orden(home, descartar=False, como_json=False):
    """Lo que hace `alfalfa --aligerar-sesiones`; devuelve el código de salida."""
    filas = aligerar(Path(home) / 'sesiones', descartar)
    antes = sum(f['antes'] for f in filas)
    despues = sum(f['despues'] for f in filas)
    if como_json:
        print(json.dumps({'sesiones': filas, 'antes': antes, 'despues': despues}, ensure_ascii=False))
    else:
        for fila in filas:
            if fila['estado'] == 'en_marcha':
                print(fila['id'] + '  en marcha: no se toca')
            elif fila['estado'] == 'error':
                print(fila['id'] + '  no se pudo aligerar: ' + fila.get('error', ''))
            elif fila['antes'] != fila['despues']:
                print(fila['id'] + '  ' + _mb(fila['antes']) + ' → ' + _mb(fila['despues']))
        cuantas = '1 sesión' if len(filas) == 1 else f'{len(filas)} sesiones'
        print(f'{cuantas}: {_mb(antes)} → {_mb(despues)} (se liberan {_mb(max(0, antes - despues))}).')
        if not descartar:
            archivos = [p for p in (Path(home) / 'sesiones').glob('*/native/*/resoluciones-no-relevantes.jsonl')]
            peso = sum(p.stat().st_size for p in archivos if p.is_file())
            if peso:
                print('Las resoluciones sin relación guardadas aparte ocupan ' + _mb(peso)
                      + '. Nada las lee: con --descartar-no-relevantes se borran.')
    return 1 if any(f['estado'] == 'error' for f in filas) else 0
