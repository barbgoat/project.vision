"""M5: a referência da impressão, aprendida de recortes de sacos OK (relevo.referencia_pasta).

Cada saco OK ensinado é um par de ficheiros na pasta:
    <nome>.png        o recorte normalizado do M2 (Saco.imagem, BGR)
    <nome>_util.png   a zona a inspecionar (Saco.mascara_util, 0/255)
A BIapp grava-os (Verificar rugas → "É OK: ensinar") a partir do saco que o M2 acabou de localizar;
o M5 só os lê. Os recortes com o mesmo tamanho que alinham uns com os outros formam um grupo (um
produto, uma face); a impressão de um grupo é a tinta que se repete nos seus sacos. Cada saco
inspecionado usa o grupo com que melhor se parece.

A pasta lê-se uma vez e fica em memória enquanto os ficheiros não mudarem (nomes, tamanhos e datas).
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from modulos.m5_relevo import linhas
from nucleo.config import RAIZ
from nucleo.contrato import Saco
from nucleo.imagens import guardar_imagem, ler_imagem

log = logging.getLogger(__name__)

SUFIXO_UTIL = "_util"
TAMANHO_TOL_MM = 8.0  # recortes com tamanhos mais diferentes do que isto são de outro produto


@dataclass(frozen=True)
class Grupo:
    """Os sacos OK de um produto e de uma face: a impressão deles e o tamanho do recorte."""

    referencia: linhas.Referencia
    forma: tuple[int, int]  # (altura, largura) do recorte do 1.º saco, em px
    nomes: tuple[str, ...]  # os ficheiros que contaram


def pasta_absoluta(pasta: str | Path) -> Path:
    """A pasta da configuração: relativa à raiz do projeto, como o fundo_referencia."""
    caminho = Path(pasta)
    return caminho if caminho.is_absolute() else RAIZ / caminho


def guardar_recorte(saco: Saco, pasta: str | Path, nome: str) -> Path:
    """Ensina um saco OK: grava o recorte e a zona a inspecionar na pasta (criada se faltar). Devolve
    o caminho do recorte."""
    destino = pasta_absoluta(pasta)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"{nome}.png"
    guardar_imagem(caminho, saco.imagem)
    guardar_imagem(destino / f"{nome}{SUFIXO_UTIL}.png", saco.mascara_util)
    return caminho


def recortes(pasta: str | Path) -> list[Path]:
    """Os recortes da pasta (sem os ficheiros _util), por ordem do nome."""
    caminho = pasta_absoluta(pasta)
    if not caminho.is_dir():
        return []
    return sorted(f for f in caminho.glob("*.png") if not f.stem.endswith(SUFIXO_UTIL))


def carregar(pasta: str, margem_mm: float, ppm: float, p: dict) -> tuple[Grupo, ...]:
    """Os grupos da pasta ("" = sem referência). Lê os ficheiros só quando mudam."""
    if not pasta:
        return ()
    assinatura = tuple((f.name, f.stat().st_size, f.stat().st_mtime_ns)
                       for f in sorted(pasta_absoluta(pasta).glob("*.png")))
    return _grupos(str(pasta_absoluta(pasta)), assinatura, margem_mm, ppm,
                   tuple(sorted((k, v) for k, v in p.items() if not isinstance(v, (list, tuple)))))


def escolher(grupos: tuple[Grupo, ...], prep: linhas.Preparado) -> Grupo | None:
    """O grupo com o mesmo tamanho com que o saco melhor se parece (None se nenhum serve)."""
    melhor, melhor_ncc = None, -1.0
    for grupo in grupos:
        if not _mesmo_tamanho(grupo.forma, prep.cinza.shape, prep.ppm):
            continue
        ncc = linhas.correlacao(grupo.referencia.pequena, prep.pequena)
        if ncc > melhor_ncc:
            melhor, melhor_ncc = grupo, ncc
    return melhor


def _mesmo_tamanho(a: tuple[int, ...], b: tuple[int, ...], ppm: float) -> bool:
    return all(abs(x - y) <= TAMANHO_TOL_MM * ppm for x, y in zip(a[:2], b[:2]))


@functools.lru_cache(maxsize=4)
def _grupos(pasta: str, assinatura: tuple, margem_mm: float, ppm: float, chave_p: tuple) -> tuple[Grupo, ...]:
    p = {**linhas.PARAMETROS, **dict(chave_p)}
    k = 2 * int(round(margem_mm * ppm)) + 1
    modelos: list[tuple[str, linhas.Preparado]] = []
    for caminho in recortes(pasta):
        imagem = ler_imagem(caminho)
        util_caminho = caminho.with_name(f"{caminho.stem}{SUFIXO_UTIL}.png")
        util = ler_imagem(util_caminho) if util_caminho.exists() else None
        if imagem is None or util is None or util.shape[:2] != imagem.shape[:2]:
            log.warning("Referência das rugas: %s ignorado (falta o %s, ou não é uma imagem)",
                        caminho.name, util_caminho.name)
            continue
        util = cv2.erode((util[..., 0] > 0).astype(np.uint8), np.ones((k, k), np.uint8)) > 0
        cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY).astype(np.float32)
        modelos.append((caminho.name, linhas.preparar(cinza, util, ppm, p)))

    grupos: list[list[tuple[str, linhas.Preparado]]] = []
    for nome, modelo in modelos:
        for grupo in grupos:
            base = grupo[0][1]
            if _mesmo_tamanho(base.tinta.shape, modelo.tinta.shape, ppm) and \
                    linhas.correlacao(base.pequena, modelo.pequena) >= linhas.ALINHAMENTO_MIN:
                grupo.append((nome, modelo))
                break
        else:
            grupos.append([(nome, modelo)])
    feitos = []
    for grupo in grupos:
        ref = linhas.referencia([modelo for _, modelo in grupo], p)
        for matriz in (ref.pequena, ref.impressao):
            if matriz is not None:
                matriz.flags.writeable = False  # partilhada entre threads
        feitos.append(Grupo(ref, grupo[0][1].tinta.shape, tuple(nome for nome, _ in grupo)))
    if modelos:
        log.info("Referência das rugas (%s): %d sacos OK em %d grupo(s): %s", pasta, len(modelos), len(feitos),
                 ", ".join(str(len(g.nomes)) for g in feitos))
    return tuple(feitos)
