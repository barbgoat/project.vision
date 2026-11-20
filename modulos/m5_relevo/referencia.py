"""M5: a referência da impressão, aprendida de sacos OK (relevo.referencia_pasta).

Cada saco OK ensinado é um par de ficheiros na pasta:
    <nome>.png        o recorte normalizado do M2 (Saco.imagem, BGR) ou, num saco maior do que o campo,
                      a imagem toda (o que o M5 viu)
    <nome>_util.png   a zona a inspecionar (Saco.mascara_util, 0/255)
A BIapp grava-os (Verificar rugas → "É OK: ensinar"); o M5 só os lê. Cada saco ensinado é uma vista:
em cada saco inspecionado, as vistas que alinham com ele (o mesmo produto e a mesma face, em qualquer
posição) dão a impressão: as marcas que se repetem nelas (linhas.imprimir).

Preparar uma vista leva ~1,5 s numa imagem toda. Por isso cada vista fica preparada em
<pasta>/_vistas/<nome>.npz (refeita quando o recorte, a zona ou os parâmetros do M5 mudam) e em memória:
ao ensinar um saco só se prepara o novo, e ao arrancar a pasta lê-se em milissegundos.

"Não é ruga" (Verificar rugas → "Não é ruga…"): as linhas que o M5 achou e o administrador diz que não
são rugas. Ficam em <pasta>/nao_rugas/, uma entrada por imagem (o nome sai do recorte: marcar mais linhas
na mesma imagem junta-as):
    <nome>.png, <nome>_util.png   o recorte e a zona, como os de um saco OK
    <nome>.json                   as linhas, em px do recorte: {"linhas": [{"p1", "p2", "descricao", "quando"}]}
Cada entrada é uma vista que só leva essas linhas (linhas.vista_nao_ruga): em cada saco onde ela alinha,
uma reta achada sobre a faixa de uma delas não conta. Apagar a entrada (os três ficheiros) desfaz.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from modulos.m5_relevo import linhas
from nucleo.config import RAIZ
from nucleo.contrato import Defeito, Saco
from nucleo.imagens import guardar_imagem, ler_imagem

log = logging.getLogger(__name__)

SUFIXO_UTIL = "_util"
PASTA_VISTAS = "_vistas"  # as vistas já preparadas (.npz), dentro da pasta da referência
PASTA_NAO_RUGAS = "nao_rugas"  # as linhas marcadas como "não é ruga", dentro da pasta da referência
VERSAO_VISTA = 2  # mudar quando o que se guarda numa vista mudar (as antigas refazem-se)
VISTAS_EM_MEMORIA = 128


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
    """Os recortes dos sacos OK da pasta (sem os ficheiros _util), por ordem do nome."""
    caminho = pasta_absoluta(pasta)
    if not caminho.is_dir():
        return []
    return sorted(f for f in caminho.glob("*.png") if not f.stem.endswith(SUFIXO_UTIL))


def segmento_px(defeito: Defeito, saco: Saco, ppm: float) -> tuple[np.ndarray, np.ndarray]:
    """As pontas (px do recorte do saco) de um defeito em linha: centro, comprimento e ângulo no
    referencial do saco (como o M5 os dá)."""
    altura, largura = saco.mascara.shape[:2]
    centro = np.array([(largura - 1) / 2, (altura - 1) / 2])
    a = math.radians(defeito.angulo_graus)
    meio = np.array([defeito.x_mm, defeito.y_mm]) * ppm + centro
    u = np.array([math.cos(a), math.sin(a)]) * defeito.comprimento_mm * ppm / 2
    return meio - u, meio + u


def guardar_nao_rugas(saco: Saco, pasta: str | Path, defeitos: list[Defeito], ppm: float) -> Path:
    """"Não é ruga": as linhas destes defeitos (do M5, no recorte deste saco) vão para <pasta>/nao_rugas/.
    A mesma imagem (o mesmo recorte) junta-se à entrada que já tem. Devolve o .json da entrada."""
    destino = pasta_absoluta(pasta) / PASTA_NAO_RUGAS
    destino.mkdir(parents=True, exist_ok=True)
    resumo = hashlib.md5(np.ascontiguousarray(saco.imagem).tobytes() + repr(saco.imagem.shape).encode()).hexdigest()
    nome = f"nao_ruga_{resumo[:12]}"
    caminho = destino / f"{nome}.json"
    if not (destino / f"{nome}.png").exists():
        guardar_imagem(destino / f"{nome}.png", saco.imagem)
        guardar_imagem(destino / f"{nome}{SUFIXO_UTIL}.png", saco.mascara_util)
    dados = _ler_json(caminho) or {"linhas": []}
    quando = datetime.now().isoformat(timespec="seconds")
    for d in defeitos:
        p1, p2 = segmento_px(d, saco, ppm)
        dados["linhas"].append({"p1": [round(float(v), 1) for v in p1], "p2": [round(float(v), 1) for v in p2],
                                "descricao": d.descricao, "quando": quando})
    temporario = caminho.with_name(f"{caminho.stem}.{os.getpid()}.tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporario, caminho)
    return caminho


def nao_rugas(pasta: str | Path) -> list[Path]:
    """As entradas "não é ruga" da pasta (os .json), por ordem do nome."""
    caminho = pasta_absoluta(pasta) / PASTA_NAO_RUGAS
    return sorted(caminho.glob("*.json")) if caminho.is_dir() else []


def _ler_json(caminho: Path) -> dict | None:
    if not caminho.is_file():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        log.warning("Referência das rugas: %s não se leu (%s)", caminho.name, erro)
        return None
    return dados if isinstance(dados, dict) and isinstance(dados.get("linhas"), list) else None


def _segmentos(caminho: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    dados = _ler_json(caminho) or {"linhas": []}
    segmentos = []
    for linha in dados["linhas"]:
        try:
            segmentos.append((np.array(linha["p1"], float)[:2], np.array(linha["p2"], float)[:2]))
        except (KeyError, TypeError, ValueError, IndexError):
            log.warning("Referência das rugas: uma linha de %s ignorada (sem p1/p2)", caminho.name)
    return segmentos


def carregar(pasta: str, margem_mm: float, ppm: float, p: dict) -> linhas.Referencia | None:
    """As vistas da pasta (None sem pasta: "" = sem referência): os sacos OK e as entradas "não é ruga".
    Só se preparam as que mudaram."""
    if not pasta:
        return None
    raiz = pasta_absoluta(pasta)
    ficheiros = sorted(raiz.glob("*.png")) + sorted((raiz / PASTA_NAO_RUGAS).glob("*.*"))
    assinatura = tuple((str(f.relative_to(raiz)), f.stat().st_size, f.stat().st_mtime_ns) for f in ficheiros
                       if f.is_file() and f.suffix in (".png", ".json"))
    return _referencia(str(raiz), assinatura, float(margem_mm), float(ppm), _Parametros(p))


class _Parametros:
    """Os parâmetros do M5 para as caches: iguais se o resumo (chave) for igual."""

    def __init__(self, p: dict) -> None:
        self.p = dict(p)
        self.chave = hashlib.sha1(repr(sorted(self.p.items())).encode()).hexdigest()[:16]

    def __hash__(self) -> int:
        return hash(self.chave)

    def __eq__(self, outro: object) -> bool:
        return isinstance(outro, _Parametros) and outro.chave == self.chave


@functools.lru_cache(maxsize=4)
def _referencia(pasta: str, assinatura: tuple, margem_mm: float, ppm: float, p: _Parametros) -> linhas.Referencia:
    vistas = []
    entradas = [(caminho, None) for caminho in recortes(pasta)]
    entradas += [(j.with_suffix(".png"), j) for j in nao_rugas(pasta)]
    for caminho, linhas_json in entradas:
        util_caminho = caminho.with_name(f"{caminho.stem}{SUFIXO_UTIL}.png")
        if not caminho.exists() or not util_caminho.exists():
            log.warning("Referência das rugas: %s ignorado (falta o recorte ou o %s)", caminho.name, util_caminho.name)
            continue
        ficheiros = (caminho, util_caminho) + ((linhas_json,) if linhas_json is not None else ())
        estado = tuple((f.stat().st_size, f.stat().st_mtime_ns) for f in ficheiros)
        v = _vista(str(caminho), estado, margem_mm, ppm, p, linhas_json is not None)
        if v is not None:
            vistas.append(v)
    ref = linhas.Referencia(tuple(vistas))
    if vistas:
        log.info("Referência das rugas (%s): %d saco(s) OK, %d imagem(ns) com linhas \"não é ruga\"", pasta, ref.n,
                 ref.nao_rugas)
    return ref


@functools.lru_cache(maxsize=VISTAS_EM_MEMORIA)
def _vista(caminho: str, estado: tuple, margem_mm: float, ppm: float, p: _Parametros,
           nao_ruga: bool = False) -> linhas.Vista | None:
    """A vista de um recorte: do .npz se estiver em dia; senão prepara-a e guarda-a (sem pasta onde
    escrever, fica só em memória). nao_ruga: uma entrada "não é ruga" (as linhas no .json ao lado)."""
    recorte = Path(caminho)
    npz = recorte.parent / PASTA_VISTAS / f"{recorte.stem}.npz"
    marca = f"{VERSAO_VISTA}|{estado}|{margem_mm:.6g}|{ppm:.6g}|{p.chave}|{int(nao_ruga)}"
    v = _ler_vista(npz, marca, recorte.name)
    if v is not None:
        return v
    imagem = ler_imagem(recorte)
    util = ler_imagem(recorte.with_name(f"{recorte.stem}{SUFIXO_UTIL}.png"))
    if imagem is None or util is None or util.shape[:2] != imagem.shape[:2]:
        log.warning("Referência das rugas: %s ignorado (a zona _util não é uma imagem do mesmo tamanho)",
                    recorte.name)
        return None
    k = 2 * int(round(margem_mm * ppm)) + 1
    util = cv2.erode((util[..., 0] > 0).astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if nao_ruga:  # só as linhas marcadas: não é preciso procurar as linhas da imagem
        prep = linhas.preparar(cinza, util, ppm, p.p, com_linhas=False)
        v = linhas.vista_nao_ruga(prep, _segmentos(recorte.with_suffix(".json")), p.p, recorte.name)
    else:
        v = linhas.vista(linhas.preparar(cinza, util, ppm, p.p), p.p, recorte.name)
    v = _so_leitura(v)
    _guardar_vista(npz, marca, v)
    return v


def _so_leitura(v: linhas.Vista) -> linhas.Vista:
    for matriz in (v.pequena, v.visivel_pequena, v.marcas, v.visivel, v.pontos, v.descritores):
        if matriz is not None:
            matriz.flags.writeable = False  # partilhada entre threads
    return v


def _ler_vista(npz: Path, marca: str, nome: str) -> linhas.Vista | None:
    if not npz.is_file():
        return None
    try:
        with open(npz, "rb") as f:  # open() aceita caminhos com acentos; o np.load com o caminho também
            d = np.load(io.BytesIO(f.read()), allow_pickle=False)
        if str(d["marca"]) != marca:
            return None
        descritores = d["descritores"] if d["descritores"].size else None
        return _so_leitura(linhas.Vista(nome, tuple(int(x) for x in d["forma"]), d["pequena"],
                                        d["visivel_pequena"], d["marcas"], d["visivel"], d["pontos"], descritores,
                                        bool(d["nao_ruga"])))
    except (OSError, ValueError, KeyError) as erro:
        log.warning("Referência das rugas: %s não se leu (%s); prepara-se de novo", npz.name, erro)
        return None


def _guardar_vista(npz: Path, marca: str, v: linhas.Vista) -> None:
    """Escreve num ficheiro temporário e troca-o (outra thread pode estar a ler o mesmo)."""
    try:
        npz.parent.mkdir(parents=True, exist_ok=True)
        dados = io.BytesIO()
        np.savez_compressed(dados, marca=np.array(marca), forma=np.array(v.forma), pequena=v.pequena,
                            visivel_pequena=v.visivel_pequena, marcas=v.marcas, visivel=v.visivel, pontos=v.pontos,
                            descritores=v.descritores if v.descritores is not None else np.zeros((0, 128), np.float32),
                            nao_ruga=np.array(v.nao_ruga))
        temporario = npz.with_name(f"{npz.stem}.{os.getpid()}.tmp")
        temporario.write_bytes(dados.getvalue())
        os.replace(temporario, npz)
    except OSError as erro:
        log.warning("Referência das rugas: a vista %s não se guardou (%s): prepara-se de novo ao arrancar",
                    npz.name, erro)
