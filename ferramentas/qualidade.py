r"""Os limites do controlo da medição do M2 (localizacao.qualidade_*) a partir de imagens de sacos bons.

    python -m ferramentas.qualidade --config configuracoes\bancada_botao.json dataset\bancada_botao\ok

Corre o M2 (só ele, com o controlo desligado) em cada imagem e mostra, para cada sinal, a mediana e
o pior dos sacos bons, o limite em uso, quantos destes sacos ele manda à revisão (ERRO "medição não
fiável"; devia ser 0) e o limite proposto: para lá do pior saco bom, a uma distância de metade da
que vai da mediana a esse pior (--folga). Mostra também cada lado (o que está pior é o que pede luz).

As imagens: sacos bons, da montagem atual (a mesma luz, ROI, escala e foto do fundo vazio), de vários
sacos físicos em posições diferentes; pelo menos uns 50 (com poucos, o pior visto fica longe do pior
que vai aparecer). Refazer depois de mudar a luz ou a câmara: com luz melhor, os limites apertam.

Não muda o perfil: escreve o bloco a copiar para lá, e em resultados/qualidade/<perfil>.csv os valores
de cada imagem.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\qualidade.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferramentas.validar import _escrever, _tabela  # noqa: E402
from modulos.m2_localizacao import Diagnostico, Localizador  # noqa: E402
from modulos.m2_localizacao.medicao import LADOS  # noqa: E402
from modulos.m2_localizacao.qualidade import problemas  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, carregar_configuracao  # noqa: E402
from nucleo.contrato import SemConfianca, SemSaco  # noqa: E402
from nucleo.imagens import listar_imagens, ler_original  # noqa: E402

FOLGA = 0.5  # o limite proposto fica para lá do pior saco bom: esta fração da distância da mediana a ele


@dataclass(frozen=True)
class Sinal:
    """Um limite do controlo da medição e o valor de cada saco que ele verifica (o do pior lado)."""

    parametro: str  # o campo de localizacao
    chave: str  # o valor por saco, de valores_do_saco()
    maximo: bool  # True: o limite é um máximo (mau é acima); False: um mínimo
    passo: float  # o limite proposto é arredondado a isto, para fora (para longe dos sacos bons)
    descricao: str


SINAIS = (
    Sinal("qualidade_aresta_min_pct", "aresta_min_pct", False, 1.0, "aresta encontrada, pior lado (%)"),
    Sinal("qualidade_rejeitados_max_pct", "rejeitados_max_pct", True, 1.0, "longe da reta, pior lado (%)"),
    Sinal("qualidade_reflexos_max_pct", "reflexos_max_pct", True, 1.0, "reflexos junto à aresta, pior lado (%)"),
    Sinal("qualidade_residuo_max_mm", "residuo_mm", True, 0.01, "resíduo do contorno (mm)"),
    Sinal("qualidade_contraste_min", "contraste", False, 0.01, "contraste ao fundo (só ΔE)"),
    Sinal("qualidade_luz_max_pct", "luz_desvio_pct", True, 1.0, "luz face à foto do fundo vazio (± %)"),
)


def sem_controlo(cfg: Config) -> Config:
    """A configuração com o controlo da medição desligado (os qualidade_* a 0): mede todos os sacos."""
    p = cfg.localizacao
    return replace(cfg, localizacao=replace(p, **{f.name: 0.0 for f in fields(p) if f.name.startswith("qualidade_")}))


def valores_do_saco(medidas: dict[str, float], px_por_mm: float) -> dict[str, float]:
    """Os valores que os limites verificam, das medidas do diagnóstico do M2 (os que faltam ficam de fora)."""
    valores = {s.chave: medidas[s.chave] for s in SINAIS if s.chave in medidas}
    if "contorno_rms_px" in medidas:
        valores["residuo_mm"] = medidas["contorno_rms_px"] / px_por_mm
    if "luz_pct" in medidas:
        valores["luz_desvio_pct"] = abs(medidas["luz_pct"])
    return valores


def propor(valores: np.ndarray, sinal: Sinal, folga: float = FOLGA) -> float:
    """O limite para lá do pior saco bom: pior + folga × (pior − mediana), arredondado para fora."""
    mediana = float(np.median(valores))
    pior = float(valores.max() if sinal.maximo else valores.min())
    limite = pior + folga * (pior - mediana)
    passos = limite / sinal.passo
    limite = (math.ceil(passos - 1e-9) if sinal.maximo else math.floor(passos + 1e-9)) * sinal.passo
    if sinal.parametro.endswith("_pct") and sinal.parametro != "qualidade_luz_max_pct":
        limite = min(limite, 100.0)
    return round(max(limite, 0.0), 6)


def resumo(por_saco: list[dict[str, float]], cfg: Config, folga: float = FOLGA) -> list[dict[str, object]]:
    """Uma linha por sinal: n, mediana, pior, o limite em uso e quantos sacos manda à revisão, e o proposto."""
    linhas = []
    for s in SINAIS:
        valores = np.array([v[s.chave] for v in por_saco if s.chave in v and math.isfinite(v[s.chave])])
        atual = float(getattr(cfg.localizacao, s.parametro))
        if not len(valores):
            linhas.append({"sinal": s.descricao, "n": 0, "mediana": None, "pior": None, "limite": atual,
                           "revisao": None, "proposto": None})
            continue
        fora = valores > atual if s.maximo else valores < atual
        linhas.append({"sinal": s.descricao, "n": len(valores), "mediana": float(np.median(valores)),
                       "pior": float(valores.max() if s.maximo else valores.min()),
                       "limite": atual if atual else "desligado",
                       "revisao": int(np.count_nonzero(fora)) if atual else 0, "proposto": propor(valores, s, folga)})
    return linhas


def _por_lado(medidas: list[dict[str, float]]) -> list[dict[str, object]]:
    """A aresta encontrada, os pontos longe da reta e os reflexos de cada lado: mediana e pior."""
    linhas = []
    for lado in LADOS:
        linha: dict[str, object] = {"lado": lado}
        for chave, pior in (("aresta", min), ("rejeitados", max), ("reflexos", max)):
            valores = [m[f"{chave}_{lado}_pct"] for m in medidas if f"{chave}_{lado}_pct" in m]
            if valores:
                linha[f"{chave}_mediana"], linha[f"{chave}_pior"] = float(np.median(valores)), float(pior(valores))
        linhas.append(linha)
    return linhas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Limites do controlo da medição do M2 a partir de sacos bons.")
    parser.add_argument("pastas", nargs="+", type=Path, help="pastas com imagens de sacos bons (e subpastas)")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="perfil da montagem (JSON)")
    parser.add_argument("--folga", type=float, default=FOLGA,
                        help=f"o limite fica para lá do pior saco bom esta fração da distância da mediana a ele "
                             f"(omissão {FOLGA:g})")
    parser.add_argument("--saida", type=Path, default=None,
                        help="CSV com os valores de cada imagem (omissão: resultados/qualidade/<perfil>.csv)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    try:
        cfg = carregar_configuracao(args.config)
        localizador = Localizador(sem_controlo(cfg))
    except (ValueError, FileNotFoundError) as erro:
        print(erro)
        return 2
    imagens = [p for pasta in args.pastas for p in listar_imagens(pasta)]
    if not imagens:
        print("Nenhuma imagem: indicar as pastas dos sacos bons, p.ex. dataset\\bancada_botao\\ok")
        return 1
    ppm = cfg.geral.px_por_mm
    print(f"{len(imagens)} imagens; px_por_mm {ppm:g}, segmentador {cfg.localizacao.segmentador}")

    por_imagem, medidas, por_saco, sem_medida = [], [], [], []
    for i, caminho in enumerate(imagens, start=1):
        diagnostico = Diagnostico()
        try:
            img = ler_original(caminho)
            if img is None:
                raise ValueError("não é uma imagem")
            saco = localizador.localizar(img, diagnostico=diagnostico)
        except (SemSaco, SemConfianca, ValueError) as erro:
            sem_medida.append((caminho, str(erro)))
            por_imagem.append({"ficheiro": str(caminho), "motivo": str(erro)})
            print(f"  {i}/{len(imagens)} {caminho.name}: não medido ({erro})")
            continue
        valores = valores_do_saco(diagnostico.medidas, ppm)
        revisao = "; ".join(problemas(diagnostico.medidas, cfg.localizacao, ppm))
        medidas.append(diagnostico.medidas)
        por_saco.append(valores)
        por_imagem.append({"ficheiro": str(caminho), "comprimento_mm": saco.comprimento_mm,
                           "largura_mm": saco.largura_mm, **valores, "com_os_limites_atuais": revisao or "ok",
                           **{k: v for k, v in diagnostico.medidas.items()
                              if k.startswith(("aresta_", "rejeitados_", "reflexos_")) and k not in valores}})
        print(f"  {i}/{len(imagens)} {caminho.name}: {'revisão: ' + revisao if revisao else 'ok'}")

    saida = args.saida or RAIZ / "resultados" / "qualidade" / f"{args.config.stem}.csv"
    _escrever(saida, por_imagem)
    if sem_medida:
        print(f"\n{len(sem_medida)} imagens sem medida (não entram; se são de sacos bons, ver porquê):")
        for caminho, motivo in sem_medida:
            print(f"  {caminho.name}: {motivo}")
    if not por_saco:
        print("Nenhum saco medido.")
        return 1

    linhas = resumo(por_saco, cfg, args.folga)
    print(_tabela(f"Sacos bons medidos: {len(por_saco)} (revisao = quantos o limite em uso manda à revisão)", linhas))
    print(_tabela("Por lado (%)", _por_lado(medidas)))
    if len(por_saco) < 50:
        print(f"\nSó {len(por_saco)} sacos: juntar mais (pelo menos uns 50) antes de usar os limites propostos.")
    propostos = {s.parametro: linha["proposto"] for s, linha in zip(SINAIS, linhas) if linha["proposto"] is not None}
    print(f"\nPara usar os propostos, no perfil ({args.config}):")
    print(json.dumps({"localizacao": propostos}, indent=2, ensure_ascii=False))
    print(f"\nValores de cada imagem em {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
