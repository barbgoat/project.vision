r"""Bancada das rugas (M5): avaliar um detetor por saco físico, sem misturar o mesmo saco.

    python -m ferramentas.bancada_rugas cenas 20261117_090000 --rotulo rugas --fotos-por-saco 3 --prefixo r
    python -m ferramentas.bancada_rugas cenas 20261117_090000 --rotulo rugas --fotos-por-saco 3 --prefixo r --escrever
    python -m ferramentas.bancada_rugas avaliar --detetor ferramentas.rugas_linhas --saida resultados\rugas_aval
    python -m ferramentas.bancada_rugas exportar --saida resultados\rugas_folder --treino ok01

O manifesto (dataset\bancada_botao\rugas_manifesto.csv, ";", UTF-8) tem uma linha por cena:
saco;grupo;sessao;passagens;rotulo;nota
- cena: o saco numa posição. As passagens repetidas da mesma foto (o saco não se mexeu) juntam-se
  numa cena ("1-5"); conta só a primeira.
- saco: o saco físico (várias cenas = várias posições do mesmo saco).
- grupo: o que nunca se separa entre referência/treino e teste. Por omissão é o saco; quando não se
  sabe se dois sacos são o mesmo (p.ex. sessões antigas), o grupo é a sessão.
- rotulo: ok ou rugas (uma só classe: vinco único ou zona com vários vincos).

cenas: propõe as linhas de uma sessão (junta as passagens com a mesma foto pela diferença entre
imagens, não por md5, e reparte as cenas por sacos, N fotos seguidas por saco). Mostra-as; só as
acrescenta ao manifesto com --escrever.

avaliar: cada cena pelo M2 (recorte normalizado) e pelo detetor. A referência que o detetor recebe
(p.ex. a impressão, tirada dos outros sacos alinhados) é feita só com os sacos dos OUTROS grupos.
Escreve em --saida cenas.csv, sacos.csv, configuracao.txt e, por cena, uma folha com as imagens
intermédias do detetor (imagens\<rotulo>\...). Reporta:
- por foto: rugas apanhadas e OK rejeitados (o IC de Wilson é otimista: as fotos do mesmo saco
  não são independentes);
- por saco, conservador: um saco com rugas só conta como apanhado se TODAS as fotos o forem; um
  saco OK conta como rejeitado se ALGUMA foto o for;
- o tempo do M2 e do detetor (média, percentil 95, máximo).

exportar: os recortes do M2 numa estrutura Folder (train/good, test/good, test/ruga) com a máscara
da área a inspecionar ao lado (masks\...), para um modelo de anomalias (p.ex. PatchCore). Os sacos
OK de --treino vão para train/good; os outros OK para test/good.

O detetor é um módulo com criar() -> objeto com:
    nome: str; parametros: dict
    preparar(recorte: Recorte) -> object            (por cena, uma vez)
    referencia(outros: list[object]) -> object      (os preparados dos outros grupos)
    detetar(preparado, referencia) -> Deteccao
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib
import logging
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\bancada_rugas.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferramentas.avaliar import taxa  # noqa: E402
from ferramentas.validar import _escrever, _tabela  # noqa: E402
from modulos.m2_localizacao.escravo import Localizador  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, carregar_configuracao  # noqa: E402
from nucleo.contrato import SemConfianca, SemSaco  # noqa: E402
from nucleo.imagens import guardar_imagem, ler_original  # noqa: E402

log = logging.getLogger("bancada_rugas")

MANIFESTO = RAIZ / "dataset" / "bancada_botao" / "rugas_manifesto.csv"
COLUNAS = ("saco", "grupo", "sessao", "passagens", "rotulo", "nota")
ROTULOS = ("ok", "rugas")
MESMA_FOTO = 3.0  # diferença média (níveis, imagem reduzida 8×) abaixo da qual duas passagens são a mesma foto
MARGEM_MM = 3.0  # tirada à mascara_util do M2 (bordas, soldadura)
QUEIMADO = 250  # acima disto o sensor saturou: não há informação


# --- Manifesto ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cena:
    """Um saco físico numa posição: uma ou mais passagens com a mesma foto."""

    saco: str
    grupo: str
    sessao: str
    passagens: tuple[int, ...]
    rotulo: str
    nota: str = ""

    @property
    def passagem(self) -> int:
        return self.passagens[0]

    @property
    def nome(self) -> str:
        return f"{self.saco}_{self.sessao}_{self.passagem:03d}"

    def linha(self) -> dict[str, str]:
        return {"saco": self.saco, "grupo": self.grupo, "sessao": self.sessao,
                "passagens": texto_passagens(self.passagens), "rotulo": self.rotulo, "nota": self.nota}


def ler_passagens(texto: str) -> tuple[int, ...]:
    """ "1-5" → (1, 2, 3, 4, 5); "8" → (8,); "1,3,7-8" → (1, 3, 7, 8)."""
    out: list[int] = []
    for parte in texto.replace(" ", "").split(","):
        if not parte:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", parte)
        if not m:
            raise ValueError(f"passagens inválidas: {texto!r}")
        a, b = int(m[1]), int(m[2] or m[1])
        if b < a:
            raise ValueError(f"passagens inválidas: {texto!r}")
        out.extend(range(a, b + 1))
    if not out:
        raise ValueError("passagens vazias")
    return tuple(out)


def texto_passagens(passagens: tuple[int, ...]) -> str:
    """O inverso de ler_passagens, com intervalos contíguos."""
    partes, inicio = [], passagens[0]
    for a, b in zip(passagens, passagens[1:] + (None,)):
        if b != a + 1:
            partes.append(str(inicio) if inicio == a else f"{inicio}-{a}")
            inicio = b
    return ",".join(partes)


def ler_manifesto(caminho: Path) -> list[Cena]:
    """As cenas do manifesto, verificadas: rótulo conhecido e um só rótulo/grupo por saco."""
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f, delimiter=";"))
    cenas: list[Cena] = []
    for i, l in enumerate(linhas, start=2):
        rotulo = (l.get("rotulo") or "").strip().lower()
        if rotulo not in ROTULOS:
            raise ValueError(f"{caminho.name}, linha {i}: rótulo {rotulo!r} (esperado: {', '.join(ROTULOS)})")
        saco = (l.get("saco") or "").strip()
        sessao = (l.get("sessao") or "").strip()
        if not saco or not sessao:
            raise ValueError(f"{caminho.name}, linha {i}: falta o saco ou a sessão")
        cenas.append(Cena(saco, (l.get("grupo") or "").strip() or saco, sessao,
                          ler_passagens(l.get("passagens") or ""), rotulo, (l.get("nota") or "").strip()))
    por_saco: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for c in cenas:
        por_saco[c.saco].add((c.rotulo, c.grupo))
    for saco, v in por_saco.items():
        if len(v) > 1:
            raise ValueError(f"{caminho.name}: o saco {saco} tem rótulos/grupos diferentes: {sorted(v)}")
    vistos: dict[tuple[str, int], str] = {}
    for c in cenas:
        for p in c.passagens:
            if (c.sessao, p) in vistos:
                raise ValueError(f"{caminho.name}: {c.sessao} passagem {p} em {vistos[(c.sessao, p)]} e {c.saco}")
            vistos[(c.sessao, p)] = c.saco
    return cenas


def escrever_manifesto(caminho: Path, cenas: list[Cena]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        w.writeheader()
        w.writerows(c.linha() for c in cenas)


# --- Sessões: originais e cenas ----------------------------------------------------------------

def originais(resultados: Path, sessao: str) -> dict[int, Path]:
    """Passagem → imagem original da sessão (originais\\<veredito>\\<passagem>_..._original.png)."""
    out: dict[int, Path] = {}
    for f in (resultados / sessao / "originais").rglob("*_original.*"):
        m = re.match(r"0*(\d+)_", f.name)
        if m:
            out[int(m[1])] = f
    return dict(sorted(out.items()))


def original(resultados: Path, cena: Cena) -> Path:
    f = originais(resultados, cena.sessao).get(cena.passagem)
    if f is None:
        raise FileNotFoundError(f"{cena.sessao}: não há original da passagem {cena.passagem}")
    return f


def _reduzida(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.resize(g, (max(1, g.shape[1] // 8), max(1, g.shape[0] // 8)), interpolation=cv2.INTER_AREA
                      ).astype(np.float32)


def agrupar_passagens(reduzidas: list[tuple[int, np.ndarray]], limiar: float = MESMA_FOTO) -> list[list[int]]:
    """Junta passagens SEGUIDAS com a mesma foto (diferença média < limiar): o saco não se mexeu."""
    grupos: list[list[int]] = []
    anterior = None
    for p, r in reduzidas:
        if anterior is not None and r.shape == anterior.shape and float(np.mean(np.abs(r - anterior))) < limiar:
            grupos[-1].append(p)
        else:
            grupos.append([p])
        anterior = r
    return grupos


def propor_cenas(resultados: Path, sessao: str, rotulo: str, fotos_por_saco: int, prefixo: str,
                 primeiro: int = 1, grupo: str = "") -> tuple[list[Cena], list[str]]:
    """As cenas de uma sessão, repartidas por sacos (fotos_por_saco cenas seguidas por saco).

    Devolve (cenas, avisos). O grupo é o saco, ou `grupo` para todos (quando os sacos podem repetir-se).
    """
    if rotulo not in ROTULOS:
        raise ValueError(f"rótulo {rotulo!r} (esperado: {', '.join(ROTULOS)})")
    fs = originais(resultados, sessao)
    if not fs:
        raise FileNotFoundError(f"{resultados / sessao / 'originais'}: sem originais")
    reduzidas = []
    for p, f in fs.items():
        img = ler_original(f)
        if img is None:
            raise OSError(f"não consegui ler {f}")
        reduzidas.append((p, _reduzida(img)))
    grupos = agrupar_passagens(reduzidas)
    avisos = []
    if len(grupos) % fotos_por_saco:
        avisos.append(f"{len(grupos)} cenas não se repartem em sacos de {fotos_por_saco}: confirmar à mão")
    cenas = []
    for i, ps in enumerate(grupos):
        saco = f"{prefixo}{primeiro + i // fotos_por_saco:02d}"
        cenas.append(Cena(saco, grupo or saco, sessao, tuple(ps), rotulo,
                          f"{len(ps)} passagens iguais" if len(ps) > 1 else ""))
    return cenas, avisos


# --- Recorte e detetor -------------------------------------------------------------------------

@dataclass
class Recorte:
    """O saco normalizado pelo M2, como o M5 o recebe."""

    cinza: np.ndarray  # float32, recorte normalizado (eixo maior na horizontal)
    util: np.ndarray  # bool: a inspecionar (sem bordas, soldadura e margem)
    ppm: float
    imagem: np.ndarray | None = None  # BGR, para desenhar

    @property
    def queimado(self) -> np.ndarray:
        return self.util & (self.cinza >= QUEIMADO)


@dataclass
class Deteccao:
    nok: bool
    pontuacao: float = 0.0  # quanto maior, mais "ruga" (para escolher o limiar)
    marcas: list[np.ndarray] = field(default_factory=list)  # polilinhas (N×2, px do recorte)
    imagens: dict[str, np.ndarray] = field(default_factory=dict)  # intermédias, pela ordem
    texto: str = ""


class Detetor(Protocol):
    nome: str
    parametros: dict[str, Any]

    def preparar(self, recorte: Recorte) -> Any: ...

    def referencia(self, outros: list[Any]) -> Any: ...

    def detetar(self, preparado: Any, referencia: Any) -> Deteccao: ...


def carregar_detetor(nome: str) -> Detetor:
    return importlib.import_module(nome).criar()


def config_m2(caminho: Path, sem_fundo: bool) -> Config:
    """A configuração do perfil; com sem_fundo, a do protótipo (sem fundo de referência e sem os
    controlos de qualidade do contraste e da luz)."""
    cfg = carregar_configuracao(caminho)
    if sem_fundo:
        cfg = dataclasses.replace(cfg, localizacao=dataclasses.replace(
            cfg.localizacao, fundo_referencia="", qualidade_contraste_min=0.0, qualidade_luz_max_pct=1000.0))
    return cfg


def recortar(localizador: Localizador, img: np.ndarray, ppm: float, margem_mm: float = MARGEM_MM) -> Recorte:
    saco = localizador.localizar(img)
    k = max(1, int(round(2 * margem_mm * ppm)) | 1)
    util = cv2.erode(saco.mascara_util, np.ones((k, k), np.uint8)) > 0
    cinza = cv2.cvtColor(saco.imagem, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return Recorte(cinza, util, ppm, saco.imagem)


# --- Avaliação ---------------------------------------------------------------------------------

@dataclass
class Avaliada:
    cena: Cena
    estado: str  # NOK, OK ou M2_FALHOU
    pontuacao: float = 0.0
    t_m2_ms: float = 0.0
    t_preparar_ms: float = 0.0
    t_detetar_ms: float = 0.0
    queimado_pct: float = 0.0
    texto: str = ""

    @property
    def certa(self) -> bool | None:
        if self.estado not in ("OK", "NOK"):
            return None
        return (self.estado == "NOK") == (self.cena.rotulo == "rugas")

    def linha(self) -> dict[str, object]:
        c = self.cena
        return {"saco": c.saco, "grupo": c.grupo, "sessao": c.sessao, "passagem": c.passagem,
                "rotulo": c.rotulo, "sistema": self.estado, "certa": {True: "sim", False: "NAO", None: "-"}[self.certa],
                "pontuacao": round(self.pontuacao, 4), "queimado_pct": round(self.queimado_pct, 1),
                "t_m2_ms": round(self.t_m2_ms, 1), "t_preparar_ms": round(self.t_preparar_ms, 1),
                "t_detetar_ms": round(self.t_detetar_ms, 1), "texto": self.texto}


def _cor(img: np.ndarray) -> np.ndarray:
    """Uma imagem intermédia qualquer → BGR uint8 (as float esticam-se entre o mínimo e o percentil 99.5)."""
    if img.dtype == bool:
        img = img.astype(np.uint8) * 255
    elif img.dtype != np.uint8:
        f = img.astype(np.float32)
        lo, hi = float(np.min(f)), float(np.percentile(f, 99.5))
        img = np.clip((f - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def folha(recorte: Recorte, d: Deteccao, cena: Cena, estado: str, colunas: int = 3, escala: float = 0.6) -> np.ndarray:
    """As imagens intermédias do detetor e, no fim, o saco com as marcas e o queimado (magenta)."""
    final = (recorte.imagem.copy() if recorte.imagem is not None else _cor(recorte.cinza))
    final[~recorte.util] //= 3
    final[recorte.queimado] = (255, 0, 255)
    for m in d.marcas:
        cv2.polylines(final, [np.asarray(m, np.int32).reshape(-1, 1, 2)], False, (0, 0, 255), 3)
    paineis = [*d.imagens.items(), (f"{estado} (real: {cena.rotulo}) {d.texto}"[:80], final)]
    h, w = recorte.cinza.shape
    ims = []
    for titulo, im in paineis:
        im = _cor(im)
        if im.shape[:2] != (h, w):
            im = cv2.resize(im, (w, h), interpolation=cv2.INTER_NEAREST)
        im = im.copy()
        cv2.putText(im, titulo, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
        cv2.putText(im, titulo, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        ims.append(im)
    while len(ims) % colunas:
        ims.append(np.zeros_like(ims[0]))
    grelha = np.vstack([np.hstack(ims[i:i + colunas]) for i in range(0, len(ims), colunas)])
    return cv2.resize(grelha, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)


def avaliar(cenas: list[Cena], detetor: Detetor, cfg: Config, resultados: Path,
            saida: Path | None = None) -> list[Avaliada]:
    """Cada cena pelo M2 e pelo detetor, com a referência feita só dos outros grupos."""
    ppm = cfg.geral.px_por_mm
    loc = Localizador(cfg)
    recortes: dict[Cena, Recorte] = {}
    preparados: dict[Cena, Any] = {}
    avaliadas: dict[Cena, Avaliada] = {}
    for c in cenas:
        img = ler_original(original(resultados, c))
        t0 = time.perf_counter()
        try:
            r = recortar(loc, img, ppm)
        except (SemSaco, SemConfianca) as e:
            avaliadas[c] = Avaliada(c, "M2_FALHOU", t_m2_ms=(time.perf_counter() - t0) * 1000, texto=str(e))
            log.warning("%s: o M2 falhou: %s", c.nome, e)
            continue
        t_m2 = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        preparados[c] = detetor.preparar(r)
        recortes[c] = r
        avaliadas[c] = Avaliada(c, "", t_m2_ms=t_m2, t_preparar_ms=(time.perf_counter() - t0) * 1000,
                                queimado_pct=100 * float(r.queimado.sum()) / max(1, int(r.util.sum())))
    referencias: dict[str, Any] = {}
    for c in cenas:
        if c not in preparados:
            continue
        if c.grupo not in referencias:
            referencias[c.grupo] = detetor.referencia([p for o, p in preparados.items() if o.grupo != c.grupo])
        t0 = time.perf_counter()
        d = detetor.detetar(preparados[c], referencias[c.grupo])
        a = avaliadas[c]
        a.t_detetar_ms = (time.perf_counter() - t0) * 1000
        a.estado, a.pontuacao, a.texto = ("NOK" if d.nok else "OK"), float(d.pontuacao), d.texto
        if saida is not None:
            guardar_imagem(saida / "imagens" / c.rotulo / f"{c.nome}.jpg", folha(recortes[c], d, c, a.estado),
                           [cv2.IMWRITE_JPEG_QUALITY, 85])
    return [avaliadas[c] for c in cenas]


def _tempos(valores: list[float]) -> str:
    if not valores:
        return "-"
    v = np.asarray(valores)
    return f"média {v.mean():.0f} ms, p95 {np.percentile(v, 95):.0f} ms, máx {v.max():.0f} ms"


def por_saco(avaliadas: list[Avaliada]) -> list[dict[str, object]]:
    sacos: dict[str, list[Avaliada]] = defaultdict(list)
    for a in avaliadas:
        sacos[a.cena.saco].append(a)
    linhas = []
    for saco, av in sacos.items():
        validas = [a for a in av if a.estado in ("OK", "NOK")]
        nok = sum(a.estado == "NOK" for a in validas)
        rotulo = av[0].cena.rotulo
        if not validas:
            veredito = "sem fotos válidas"
        elif rotulo == "rugas":
            veredito = "apanhado" if nok == len(validas) else ("em parte" if nok else "ESCAPOU")
        else:
            veredito = "REJEITADO" if nok else "aceite"
        linhas.append({"saco": saco, "grupo": av[0].cena.grupo, "rotulo": rotulo, "fotos": len(av),
                       "validas": len(validas), "nok": nok, "veredito": veredito,
                       "pontuacoes": " ".join(f"{a.pontuacao:.3g}" for a in validas)})
    return linhas


def resumo(avaliadas: list[Avaliada]) -> list[str]:
    """As taxas por foto e por saco (conservador), as falhas do M2 e os tempos."""
    validas = [a for a in avaliadas if a.estado in ("OK", "NOK")]
    rugas = [a for a in validas if a.cena.rotulo == "rugas"]
    oks = [a for a in validas if a.cena.rotulo == "ok"]
    sacos = por_saco(avaliadas)
    s_rugas = [s for s in sacos if s["rotulo"] == "rugas" and s["validas"]]
    s_ok = [s for s in sacos if s["rotulo"] == "ok" and s["validas"]]
    falhas = [a for a in avaliadas if a.estado == "M2_FALHOU"]
    return [
        f"Cenas: {len(avaliadas)} ({len({a.cena.saco for a in avaliadas})} sacos); o M2 falhou em {len(falhas)}"
        + (": " + ", ".join(a.cena.nome for a in falhas) if falhas else ""),
        f"Por foto (otimista: fotos do mesmo saco não são independentes):",
        f"  rugas apanhadas: {taxa(sum(a.estado == 'NOK' for a in rugas), len(rugas))}",
        f"  OK rejeitados:   {taxa(sum(a.estado == 'NOK' for a in oks), len(oks))}",
        f"Por saco (conservador: apanhado = todas as fotos; rejeitado = alguma foto):",
        f"  rugas apanhadas: {taxa(sum(s['veredito'] == 'apanhado' for s in s_rugas), len(s_rugas))}",
        f"  OK rejeitados:   {taxa(sum(s['veredito'] == 'REJEITADO' for s in s_ok), len(s_ok))}",
        f"Tempo do M2:      {_tempos([a.t_m2_ms for a in avaliadas if a.estado != 'M2_FALHOU'])}",
        f"Tempo do detetor: {_tempos([a.t_preparar_ms + a.t_detetar_ms for a in validas])} (preparar + detetar)",
    ]


def _hash(caminho: Path) -> str:
    return hashlib.sha1(caminho.read_bytes()).hexdigest()[:10]


# --- Exportar (Folder) -------------------------------------------------------------------------

def exportar(cenas: list[Cena], cfg: Config, resultados: Path, saida: Path, treino: set[str]) -> dict[str, int]:
    """Os recortes do M2 em train/good, test/good e test/ruga; a área a inspecionar em masks/."""
    loc, ppm = Localizador(cfg), cfg.geral.px_por_mm
    contagem: dict[str, int] = defaultdict(int)
    for c in cenas:
        pasta = "test/ruga" if c.rotulo == "rugas" else ("train/good" if c.saco in treino else "test/good")
        try:
            r = recortar(loc, ler_original(original(resultados, c)), ppm)
        except (SemSaco, SemConfianca) as e:
            log.warning("%s: o M2 falhou, fica de fora: %s", c.nome, e)
            contagem["m2_falhou"] += 1
            continue
        guardar_imagem(saida / pasta / f"{c.nome}.png", r.cinza.astype(np.uint8))
        guardar_imagem(saida / "masks" / pasta / f"{c.nome}.png", r.util.astype(np.uint8) * 255)
        contagem[pasta] += 1
    return dict(contagem)


# --- Linha de comandos -------------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bancada das rugas (M5): avaliar por saco físico.")
    p.add_argument("--manifesto", type=Path, default=MANIFESTO)
    p.add_argument("--resultados", type=Path, default=RAIZ / "resultados")
    p.add_argument("--config", type=Path, default=RAIZ / "configuracoes" / "bancada_botao.json"
                   if (RAIZ / "configuracoes" / "bancada_botao.json").exists() else CAMINHO_POR_OMISSAO)
    p.add_argument("--sem-fundo", action="store_true",
                   help="M2 sem fundo de referência nem controlos de contraste/luz (a configuração do protótipo)")
    sub = p.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("cenas", help="propor as linhas do manifesto de uma sessão")
    c.add_argument("sessao")
    c.add_argument("--rotulo", required=True, choices=ROTULOS)
    c.add_argument("--fotos-por-saco", type=int, default=3)
    c.add_argument("--prefixo", default="")
    c.add_argument("--primeiro", type=int, default=1, help="número do primeiro saco")
    c.add_argument("--grupo", default="", help="um grupo para todos (quando os sacos podem ser os mesmos)")
    c.add_argument("--escrever", action="store_true", help="acrescentar ao manifesto")
    a = sub.add_parser("avaliar", help="avaliar um detetor nas cenas do manifesto")
    a.add_argument("--detetor", required=True, help="módulo com criar(), p.ex. ferramentas.rugas_linhas")
    a.add_argument("--saida", type=Path, required=True)
    e = sub.add_parser("exportar", help="recortes numa estrutura Folder (train/good, test/good, test/ruga)")
    e.add_argument("--saida", type=Path, required=True)
    e.add_argument("--treino", default="", help="sacos OK para train/good, separados por vírgulas")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = construir_parser().parse_args(argv)
    if args.comando == "cenas":
        prefixo = args.prefixo or ("r" if args.rotulo == "rugas" else "ok")
        novas, avisos = propor_cenas(args.resultados, args.sessao, args.rotulo, args.fotos_por_saco, prefixo,
                                     args.primeiro, args.grupo)
        print(_tabela(f"{args.sessao}: {len(novas)} cenas", [c.linha() for c in novas]))
        for a in avisos:
            print("AVISO:", a)
        if args.escrever:
            antigas = ler_manifesto(args.manifesto) if args.manifesto.exists() else []
            repetidas = {c.saco for c in antigas} & {c.saco for c in novas}
            if repetidas:
                print(f"Não escrevi: os sacos {sorted(repetidas)} já estão no manifesto (usar --prefixo/--primeiro)")
                return 1
            escrever_manifesto(args.manifesto, antigas + novas)
            ler_manifesto(args.manifesto)  # verifica o conjunto
            print(f"Acrescentadas a {args.manifesto}")
        return 0

    cenas = ler_manifesto(args.manifesto)
    cfg = config_m2(args.config, args.sem_fundo)
    if args.comando == "exportar":
        treino = {s.strip() for s in args.treino.split(",") if s.strip()}
        print(exportar(cenas, cfg, args.resultados, args.saida, treino))
        return 0

    detetor = carregar_detetor(args.detetor)
    avaliadas = avaliar(cenas, detetor, cfg, args.resultados, args.saida)
    _escrever(args.saida / "cenas.csv", [a.linha() for a in avaliadas])
    _escrever(args.saida / "sacos.csv", por_saco(avaliadas))
    loc = cfg.localizacao
    configuracao = [
        f"data: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"manifesto: {args.manifesto} (sha1 {_hash(args.manifesto)})",
        f"config M2: {args.config} (sha1 {_hash(args.config)}); fundo_referencia={loc.fundo_referencia!r}, "
        f"qualidade_contraste_min={loc.qualidade_contraste_min}, qualidade_luz_max_pct={loc.qualidade_luz_max_pct}"
        + (f"; fundo sha1 {_hash(RAIZ / loc.fundo_referencia)}" if loc.fundo_referencia
           and (RAIZ / loc.fundo_referencia).is_file() else ""),
        f"px_por_mm: {cfg.geral.px_por_mm}; margem: {MARGEM_MM} mm; queimado: >= {QUEIMADO}",
        f"detetor: {detetor.nome} {detetor.parametros}",
    ]
    linhas = resumo(avaliadas)
    (args.saida / "configuracao.txt").write_text("\n".join(configuracao + [""] + linhas) + "\n", encoding="utf-8")
    print("\n".join(configuracao))
    print(_tabela("Por saco", por_saco(avaliadas)))
    print()
    print("\n".join(linhas))
    print(f"\nImagens de cada cena em {args.saida / 'imagens'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
