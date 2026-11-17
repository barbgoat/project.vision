"""M2 Localização, etapa 1: o segmentador, que separa o saco do fundo.

A interface (Segmentador.segmentar) devolve um ResultadoSegmentacao: a região do
saco, a confiança, e o mapa onde o contorno subpíxel procura a aresta. O resto do
M2 (contorno subpíxel, pose, medidas, recorte) e o M3 não dependem de como foi feito.

    delta_e.py   SegmentadorDeltaE: diferença de cor ao fundo (CIELAB), CPU, rápido

Os testes usam a interface para dar ao M2 uma máscara feita à mão.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from nucleo.config import Config
from nucleo.contrato import SemConfianca


class SegmentacaoIncerta(SemConfianca):
    """O segmentador não tem a certeza (confiança baixa, contorno incompleto). O Mestre
    transforma-a em ERRO: um saco assim nunca é aprovado."""


@dataclass(frozen=True)
class ResultadoSegmentacao:
    """O que um segmentador encontrou numa imagem.

    mascara e mapa_aresta estão na mesma resolução, proporcional à da imagem
    original (pode ser uma versão reduzida); o M2 leva o contorno para a original.
    """

    mascara: np.ndarray  # bool: a região do saco (uma só, preenchida)
    score: float  # confiança, 0–1
    backend: str  # "delta_e"
    tempo_ms: float
    # Onde o contorno subpíxel procura a aresta (o sítio em que este mapa muda mais depressa ao
    # longo da normal): 2D (ΔE ao fundo, que cai para fora) ou H×W×3 (CIELAB, o gradiente em módulo).
    mapa_aresta: np.ndarray
    medidas: dict[str, float] = field(default_factory=dict)  # valores de diagnóstico (limiar usado...)
    intermedios: dict[str, np.ndarray] = field(default_factory=dict)  # imagens das etapas, se pedidas
    # Opcional: o mesmo sinal do mapa_aresta, mas lido da imagem à resolução total. Recebe (xs, ys) em
    # px originais (N×K) e devolve N×K. Com o mapa reduzido, o contorno subpíxel procura a aresta aqui
    # (a precisão deixa de depender da redução); sem ele, ou sem redução, procura no mapa.
    amostrar: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None


class Segmentador(ABC):
    """Separa o saco do fundo numa imagem."""

    nome: str

    @abstractmethod
    def segmentar(self, img: np.ndarray, px_por_mm: float, guardar_intermedios: bool = False) -> ResultadoSegmentacao:
        """A região do saco.

        Recebe:
            img: imagem BGR uint8, só de leitura.
            px_por_mm: a escala da imagem (para os tamanhos em mm: fecho, faixa da borda...).
            guardar_intermedios: preencher ResultadoSegmentacao.intermedios.
        Devolve:
            O ResultadoSegmentacao. Levanta SemSaco se não houver nada que pareça um saco,
            ou SegmentacaoIncerta se houver mas sem confiança suficiente.
        """


def criar(cfg: Config) -> Segmentador:
    """O segmentador do M2 (o ΔE)."""
    from modulos.m2_localizacao.segmentadores.delta_e import SegmentadorDeltaE

    return SegmentadorDeltaE(cfg)
