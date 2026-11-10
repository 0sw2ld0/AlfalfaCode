"""Copias de subida privadas: el diálogo autoriza los mismos bytes que recibe Chromium."""
from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def archivos_de_subida(root, paths, staging):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 10 or not all(isinstance(p, str) for p in paths):
        raise ValueError('paths debe contener entre 1 y 10 rutas de archivos del proyecto.')
    root = root.resolve()
    total = 0
    with tempfile.TemporaryDirectory(prefix='subida-', dir=staging) as folder:
        copies = []
        for index, value in enumerate(paths):
            path = Path(os.path.abspath(root / value))
            if not path.is_relative_to(root) or path == root:
                raise ValueError('Sólo se suben archivos normales dentro del proyecto.')
            parts = path.relative_to(root).parts
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            source = None
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                before = os.fstat(source)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ValueError('No se suben enlaces ni archivos especiales.')
                total += before.st_size
                if total > 20 * 1024 * 1024:
                    raise ValueError('Los archivos de la subida superan el límite de 20 MiB.')
                destination_dir = Path(folder) / str(index)
                destination_dir.mkdir(mode=0o700)
                destination = destination_dir / parts[-1]
                with destination.open('xb') as target:
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(source, min(remaining, 65536))
                        if not chunk:
                            raise ValueError('El archivo cambió mientras se preparaba la subida.')
                        target.write(chunk)
                        remaining -= len(chunk)
                    if os.read(source, 1):
                        raise ValueError('El archivo creció mientras se preparaba la subida.')
                after = os.fstat(source)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('El archivo cambió mientras se preparaba la subida.')
                destination.chmod(0o400)
                copies.append(str(destination))
            except OSError:
                raise ValueError('No se puede abrir la subida sin atravesar enlaces ni salir del proyecto.') from None
            finally:
                if source is not None:
                    os.close(source)
                os.close(directory)
        yield copies
