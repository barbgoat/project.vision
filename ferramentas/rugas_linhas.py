"""Detetor de rugas por linhas, para a bancada: python -m ferramentas.bancada_rugas avaliar --detetor ferramentas.rugas_linhas

O algoritmo é o do M5 (modulos/m5_relevo/linhas.py); aqui só se adapta à interface da bancada.
"""

from __future__ import annotations

import numpy as np

from ferramentas.bancada_rugas import Deteccao, Recorte
from modulos.m5_relevo import linhas
from modulos.m5_relevo.linhas import PARAMETROS, Preparado, Referencia


class Linhas:
    nome = "rugas_linhas"

    def __init__(self, **parametros):
        self.parametros = {**PARAMETROS, **parametros}

    def preparar(self, r: Recorte) -> Preparado:
        return linhas.preparar(r.cinza, r.util, r.ppm, self.parametros)

    def referencia(self, outros: list[Preparado]) -> Referencia:
        return linhas.referencia(outros, self.parametros)

    def detetar(self, prep: Preparado, ref: Referencia) -> Deteccao:
        d = linhas.detetar(prep, ref, self.parametros, imagens=True)
        tapado = 100 * float((d.imagens["impressao"] & prep.util).sum()) / max(1, int(prep.util.sum()))
        texto = (f"{len(d.achados)} retas, prova {d.prova_mm:.0f} mm; " if d.achados else "") + \
            f"alinh {d.alinhamento:.2f}; impressao {tapado:.0f}%"
        return Deteccao(
            bool(d.achados), d.prova_mm, [np.array([a.p1, a.p2]) for a in d.achados],
            {"1 normalizado": prep.pequena, "2 resposta (2 polaridades)": d.imagens["resposta"],
             "3 pontos de linha (simetria, tinta)": d.imagens["linhas"],
             "4 impressao (referencia, %d sacos)" % ref.n: d.imagens["impressao"],
             "5 candidatos: amarelo soltos, azul densos": d.imagens["candidatos"]},
            texto)


def criar() -> Linhas:
    return Linhas()
