r"""Validar os segmentadores do M2 num conjunto de teste com as medidas reais (paquímetro).

    python -m ferramentas.validar --config configuracoes\linha.json
    python -m ferramentas.validar --config configuracoes\linha.json --backends delta_e aprendido

O conjunto (--pasta, omissão: dataset\teste):
    imagens/            as fotos, tiradas com a montagem do perfil (px_por_mm medido)
    mascaras/           (opcional) PNG com o saco anotado, mesmo nome da foto: dá o IoU
    ground_truth.csv    ficheiro; saco; tipo_saco; fundo; comprimento_mm; largura_mm
                        (saco = o saco físico: as fotos do mesmo saco dão a repetibilidade)

Para cada backend corre o M2 em cada foto e reporta, no terminal e em CSV (--saida, omissão
resultados\validacao.csv, mais _resumo.csv e _repetibilidade.csv ao lado):
    erro médio, máximo e desvio-padrão (mm) do comprimento e da largura;
    IoU da máscara (quando há máscara anotada);
    taxa de ERRO ou sem saco; tempo médio por foto;
agrupado por backend, por backend e tipo de saco, e por backend e fundo. A repetibilidade
é o desvio-padrão das medidas das várias fotos do mesmo saco.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import get_args

import cv2
import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\validar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mestre import Mestre  # noqa: E402
from modulos.m2_localizacao.segmentadores import iou, sam3  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, TipoSegmentador, carregar_configuracao  # noqa: E402
from nucleo.contrato import Estado  # noqa: E402
from nucleo.imagens import ler_imagem, ler_original  # noqa: E402

COLUNAS_VERDADE = ("ficheiro", "saco", "tipo_saco", "fundo", "comprimento_mm", "largura_mm")


@dataclass
class Medicao:
    """Uma foto analisada por um backend."""

    backend: str
    ficheiro: str
    saco: str
    tipo_saco: str
    fundo: str
    estado: str  # OK (mediu em mm), ERRO, SEM_SACO
    comprimento_mm: float | None
    largura_mm: float | None
    erro_comprimento_mm: float | None  # medido − real
    erro_largura_mm: float | None
    iou: float | None
    tempo_ms: float
    mensagem: str = ""


def ler_verdade(pasta: Path) -> list[dict[str, str]]:
    """As linhas de ground_truth.csv (separador ;)."""
    caminho = pasta / "ground_truth.csv"
    with caminho.open(encoding="utf-8-sig", newline="") as ficheiro:
        linhas = list(csv.DictReader(ficheiro, delimiter=";"))
    faltam = [c for c in COLUNAS_VERDADE if linhas and c not in linhas[0]]
    if faltam:
        raise ValueError(f"{caminho}: faltam as colunas {', '.join(faltam)}")
    return linhas


def medir(pasta: Path, cfg: Config, backend: str) -> list[Medicao]:
    """Corre o M2 com o `backend` em cada foto do conjunto."""
    cfg = replace(cfg, localizacao=replace(cfg.localizacao, segmentador=backend))
    mestre = Mestre(cfg, apenas=[])
    medicoes = []
    for verdade in ler_verdade(pasta):
        img = ler_original(pasta / "imagens" / verdade["ficheiro"])
        if img is None:
            raise FileNotFoundError(pasta / "imagens" / verdade["ficheiro"])
        inicio = perf_counter()
        resultado = mestre.inspecionar(img)
        tempo = (perf_counter() - inicio) * 1000
        loc = resultado.relatorio("localizacao")
        mediu = loc is not None and loc.estado is Estado.OK and "comprimento_mm" in loc.medidas
        comprimento = loc.medidas["comprimento_mm"] if mediu else None
        largura = loc.medidas["largura_mm"] if mediu else None
        medicoes.append(Medicao(
            backend, verdade["ficheiro"], verdade["saco"], verdade["tipo_saco"], verdade["fundo"],
            "OK" if mediu else str(loc.estado if loc else resultado.estado),
            comprimento, largura,
            None if comprimento is None else comprimento - float(verdade["comprimento_mm"]),
            None if largura is None else largura - float(verdade["largura_mm"]),
            _iou(pasta, verdade["ficheiro"], resultado) if mediu else None, tempo,
            "" if mediu else resultado.mensagem))
    return medicoes


def _iou(pasta: Path, ficheiro: str, resultado) -> float | None:
    """IoU da região do saco com a máscara anotada, se houver uma do tamanho da imagem analisada."""
    caminho = pasta / "mascaras" / f"{Path(ficheiro).stem}.png"
    anotada = ler_imagem(caminho) if caminho.exists() else None
    if anotada is None or resultado.saco is None or anotada.shape[:2] != resultado.imagem.shape[:2]:
        return None  # sem máscara, ou de outro tamanho
    regiao = np.zeros(anotada.shape[:2], np.uint8)
    cv2.fillPoly(regiao, [resultado.saco.contorno], 1)
    return iou(regiao > 0, anotada[..., 0] > 127)


def resumir(medicoes: list[Medicao], *chaves: str) -> list[dict[str, object]]:
    """Estatísticas por grupo (backend e as `chaves`, p.ex. "tipo_saco")."""
    grupos: dict[tuple, list[Medicao]] = defaultdict(list)
    for m in medicoes:
        grupos[(m.backend, *(getattr(m, c) for c in chaves))].append(m)
    linhas = []
    for grupo, ms in sorted(grupos.items()):
        linha: dict[str, object] = {"backend": grupo[0], **dict(zip(chaves, grupo[1:])), "fotos": len(ms)}
        for medida in ("comprimento", "largura"):
            erros = [getattr(m, f"erro_{medida}_mm") for m in ms if getattr(m, f"erro_{medida}_mm") is not None]
            linha[f"{medida}_erro_medio_mm"] = statistics.fmean(map(abs, erros)) if erros else None
            linha[f"{medida}_erro_max_mm"] = max(map(abs, erros)) if erros else None
            linha[f"{medida}_desvio_padrao_mm"] = statistics.pstdev(erros) if len(erros) > 1 else None
        ious = [m.iou for m in ms if m.iou is not None]
        linha["iou_medio"] = statistics.fmean(ious) if ious else None
        linha["taxa_erro_ou_sem_saco"] = sum(m.estado != "OK" for m in ms) / len(ms)
        linha["tempo_medio_ms"] = statistics.fmean(m.tempo_ms for m in ms)
        linhas.append(linha)
    return linhas


def repetibilidade(medicoes: list[Medicao]) -> list[dict[str, object]]:
    """Por backend e saco físico com ≥ 2 fotos medidas: o desvio-padrão das medidas (mm)."""
    grupos: dict[tuple[str, str], list[Medicao]] = defaultdict(list)
    for m in medicoes:
        if m.comprimento_mm is not None:
            grupos[(m.backend, m.saco)].append(m)
    return [{"backend": b, "saco": s, "fotos": len(ms),
             "comprimento_dp_mm": statistics.pstdev(m.comprimento_mm for m in ms),
             "largura_dp_mm": statistics.pstdev(m.largura_mm for m in ms)}
            for (b, s), ms in sorted(grupos.items()) if len(ms) >= 2]


def _escrever(caminho: Path, linhas: list[dict[str, object]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as ficheiro:
        if not linhas:
            return
        colunas = list(dict.fromkeys(c for linha in linhas for c in linha))  # o resumo junta grupos diferentes
        escritor = csv.DictWriter(ficheiro, fieldnames=colunas, delimiter=";", restval="")
        escritor.writeheader()
        escritor.writerows({k: _texto(v) for k, v in linha.items()} for linha in linhas)


def _texto(valor: object) -> object:
    return f"{valor:.3f}" if isinstance(valor, float) else "" if valor is None else valor


def _tabela(titulo: str, linhas: list[dict[str, object]]) -> str:
    """Uma tabela de texto para o terminal."""
    if not linhas:
        return f"\n{titulo}: (nada)"
    colunas = list(linhas[0])
    celulas = [[str(_texto(l[c])) for c in colunas] for l in linhas]
    larguras = [max(len(c), *(len(r[i]) for r in celulas)) for i, c in enumerate(colunas)]
    linha = lambda valores: "  ".join(v.rjust(w) for v, w in zip(valores, larguras))  # noqa: E731
    return "\n".join([f"\n{titulo}", linha(colunas), *(linha(r) for r in celulas)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validar os segmentadores do M2 com medidas reais.")
    parser.add_argument("--pasta", type=Path, default=RAIZ / "dataset" / "teste", help="o conjunto de teste")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="perfil da montagem (JSON)")
    parser.add_argument("--backends", nargs="+", default=["delta_e"], choices=list(get_args(TipoSegmentador)))
    parser.add_argument("--saida", type=Path, default=RAIZ / "resultados" / "validacao.csv", help="CSV por foto")
    args = parser.parse_args(argv)

    cfg = carregar_configuracao(args.config)
    medicoes: list[Medicao] = []
    for backend in args.backends:
        if backend in ("sam3", "duplo") and not sam3.disponivel():
            print(f"{backend}: o SAM 3 não está instalado (ver o README); salto este backend.")
            continue
        medicoes += medir(args.pasta, cfg, backend)
    if not medicoes:
        print("Nada medido.")
        return 1

    por_foto = [asdict(m) for m in medicoes]
    resumos = resumir(medicoes) + resumir(medicoes, "tipo_saco") + resumir(medicoes, "fundo")
    repet = repetibilidade(medicoes)
    _escrever(args.saida, por_foto)
    _escrever(args.saida.with_name(f"{args.saida.stem}_resumo.csv"), resumos)
    _escrever(args.saida.with_name(f"{args.saida.stem}_repetibilidade.csv"), repet)
    print(_tabela("Por backend", resumir(medicoes)))
    print(_tabela("Por backend e tipo de saco", resumir(medicoes, "tipo_saco")))
    print(_tabela("Por backend e fundo", resumir(medicoes, "fundo")))
    print(_tabela("Repetibilidade (fotos do mesmo saco)", repet))
    print(f"\nCSV em {args.saida} (e _resumo, _repetibilidade)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
