"""Escrita segura dos arquivos de saída.

- `atomic_write`: grava num `.<nome>.part` oculto na mesma pasta, faz flush + fsync e só
  então renomeia (`os.replace`) para o nome final. Se algo falhar no meio (disco cheio,
  SSD desconectado, erro de leitura), o `.part` é apagado e nenhum arquivo truncado fica
  com nome de "pronto".
- `unique_stem` / `unique_path`: nunca sobrescreve um resultado anterior; se o nome já
  existe, acrescenta `_2`, `_3`… antes da extensão.
"""
from __future__ import annotations

import os
from contextlib import contextmanager


def part_path(dst: str) -> str:
    d, name = os.path.split(dst)
    return os.path.join(d, f'.{name}.part')


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def fsync_path(path: str):
    """fsync de um arquivo já fechado (ex.: gravado pelo ffmpeg)."""
    with open(path, 'rb+') as f:
        os.fsync(f.fileno())


@contextmanager
def atomic_write(dst: str):
    """Contexto que entrega um arquivo binário aberto para escrita em `.<dst>.part`.

    Na saída normal: flush, fsync, fecha e renomeia para `dst`. Em qualquer exceção
    (inclusive KeyboardInterrupt): fecha, apaga o `.part` e relança a exceção."""
    tmp = part_path(dst)
    f = open(tmp, 'wb')
    try:
        yield f
        f.flush()
        os.fsync(f.fileno())
        f.close()
        os.replace(tmp, dst)
    except BaseException:
        f.close()
        _remove(tmp)
        raise


def _taken(path: str) -> bool:
    return os.path.exists(path) or os.path.exists(part_path(path))


def unique_stem(directory: str, base: str, endings: tuple[str, ...] | list[str]) -> str:
    """Caminho base (sem extensão) livre para todos os `endings` ao mesmo tempo.

    Ex.: unique_stem('/saida', 'C0001_REPARADO', ['.MP4', 'M01.XML'])
    -> '/saida/C0001_REPARADO' ou, se já existir, '/saida/C0001_REPARADO_2' (e assim por diante).
    Assim o clipe e os arquivos que o acompanham (XML, .wav, .h264) ficam com o mesmo número."""
    stem = os.path.join(directory, base)
    n = 1
    while any(_taken(stem + e) for e in endings):
        n += 1
        stem = os.path.join(directory, f'{base}_{n}')
    return stem


def unique_path(path: str) -> str:
    """`/saida/C0001_REPARADO.MP4` -> o próprio caminho, ou `…_2.MP4`, `…_3.MP4` se já existir."""
    d, name = os.path.split(path)
    base, ext = os.path.splitext(name)
    return unique_stem(d, base, [ext]) + ext
