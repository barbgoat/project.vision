"""M2 Localização, etapa 1: segmentadores, as várias formas de separar o saco do fundo.

Todos têm a mesma interface (Segmentador.segmentar) e devolvem um
ResultadoSegmentacao: a região do saco, a confiança, e o mapa onde o contorno
subpíxel procura a aresta. O resto do M2 (contorno subpíxel, pose, medidas,
recorte) e o M3 não sabem qual foi usado.

    delta_e.py   SegmentadorDeltaE: diferença de cor ao fundo (CIELAB), CPU, rápido
    aprendido.py SegmentadorAprendido: cores e texturas aprendidas com as fotos anotadas, CPU
    sam3.py      SegmentadorSam3: SAM 3 da Meta por texto ("plastic bag"), qualquer fundo, GPU
    (aqui)       SegmentadorDuplo: os dois, e ERRO quando discordam (verificação cruzada)

Escolhe-se em localizacao.segmentador ("delta_e", "aprendido", "sam3" ou "duplo");
criar(cfg) devolve o pedido.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.config import RAIZ, Config

log = logging.getLogger("m2.segmentadores")


class SegmentacaoIncerta(Exception):
    """O segmentador não tem a certeza (confiança baixa, nenhuma máscara, segmentadores em
    desacordo). O Mestre transforma-a em ERRO: um saco assim nunca é aprovado."""


@dataclass(frozen=True)
class ResultadoSegmentacao:
    """O que um segmentador encontrou numa imagem.

    mascara e mapa_aresta estão na mesma resolução, proporcional à da imagem
    original (pode ser uma versão reduzida); o M2 leva o contorno para a original.
    """

    mascara: np.ndarray  # bool: a região do saco (uma só, preenchida)
    score: float  # confiança, 0–1
    backend: str  # "delta_e", "sam3"...
    tempo_ms: float
    # Onde o contorno subpíxel procura a aresta (o sítio em que este mapa muda mais depressa ao
    # longo da normal): 2D (ΔE ao fundo, que cai para fora) ou H×W×3 (CIELAB, o gradiente em módulo).
    mapa_aresta: np.ndarray
    medidas: dict[str, float] = field(default_factory=dict)  # valores de diagnóstico (limiar usado...)
    intermedios: dict[str, np.ndarray] = field(default_factory=dict)  # imagens das etapas, se pedidas


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
    """O segmentador pedido em cfg.localizacao.segmentador."""
    from modulos.m2_localizacao.segmentadores.aprendido import SegmentadorAprendido
    from modulos.m2_localizacao.segmentadores.delta_e import SegmentadorDeltaE
    from modulos.m2_localizacao.segmentadores.sam3 import SegmentadorSam3

    tipo = cfg.localizacao.segmentador
    if tipo == "delta_e":
        return SegmentadorDeltaE(cfg)
    if tipo == "aprendido":
        return SegmentadorAprendido(cfg)
    if tipo == "sam3":
        return SegmentadorSam3(cfg)
    return SegmentadorDuplo(SegmentadorDeltaE(cfg), SegmentadorSam3(cfg), cfg.localizacao.duplo_iou_min)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Coincidência de duas máscaras (interseção / união), na resolução da maior; 0 se ambas vazias."""
    if a.shape != b.shape:
        maior, menor = (a, b) if a.size >= b.size else (b, a)
        menor = cv2.resize(menor.astype(np.uint8), maior.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
        a, b = maior, menor
    uniao = np.count_nonzero(a | b)
    return np.count_nonzero(a & b) / uniao if uniao else 0.0


class SegmentadorDuplo(Segmentador):
    """Verificação cruzada: corre dois segmentadores e compara as máscaras.

    Se coincidem (IoU ≥ iou_min), usa o resultado do principal (o SAM 3, por omissão) com a
    confiança baixada para o IoU, se este for menor. Se não, a foto é guardada em `revisao`
    (para ser anotada) e o saco fica ERRO ("duvidoso").
    """

    nome = "duplo"

    def __init__(self, secundario: Segmentador, principal: Segmentador, iou_min: float,
                 revisao: str | Path = D.M2_REVISAO) -> None:
        self.secundario, self.principal, self.iou_min = secundario, principal, iou_min
        self.revisao = Path(revisao) if Path(revisao).is_absolute() else RAIZ / revisao

    def segmentar(self, img: np.ndarray, px_por_mm: float, guardar_intermedios: bool = False) -> ResultadoSegmentacao:
        a = self.secundario.segmentar(img, px_por_mm, guardar_intermedios)
        b = self.principal.segmentar(img, px_por_mm, guardar_intermedios)
        coincidencia = iou(a.mascara, b.mascara)
        if coincidencia < self.iou_min:
            destino = self._guardar_para_revisao(img)
            raise SegmentacaoIncerta(
                f"segmentação duvidosa: {a.backend} e {b.backend} discordam (IoU {coincidencia:.3f} < "
                f"{self.iou_min:g}); foto em {destino}")
        medidas = {**{f"{a.backend}_{k}": v for k, v in a.medidas.items()}, **b.medidas,
                   "iou_duplo": coincidencia, f"score_{a.backend}": a.score}
        return ResultadoSegmentacao(b.mascara, min(b.score, coincidencia), f"{b.backend}+{a.backend}",
                                    a.tempo_ms + b.tempo_ms, b.mapa_aresta, medidas,
                                    {**a.intermedios, **{f"{k}_{b.backend}": v for k, v in b.intermedios.items()}})

    def _guardar_para_revisao(self, img: np.ndarray) -> Path:
        from nucleo.imagens import guardar_imagem

        destino = self.revisao / f"{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        try:
            guardar_imagem(destino, img)
        except OSError as erro:  # a revisão não pode parar a inspeção
            log.warning("não foi possível guardar %s: %s", destino, erro)
        return destino
