"""M2 Localização, etapa 4: endireitar o saco e marcar a zona a inspecionar.

A matriz afim roda o saco para o eixo maior ficar na horizontal e põe-no no
centro do recorte; a zona útil tira a faixa junto ao contorno e as pontas.
"""

from __future__ import annotations

import math

import cv2
import numpy as np



def matriz_normalizacao(
    centro: np.ndarray, angulo: float, comprimento_px: float, largura_px: float, folga_px: float
) -> tuple[np.ndarray, tuple[int, int]]:
    """Matriz afim 2×3 (imagem original → recorte) e tamanho do recorte (largura, altura).

    Roda de −ângulo em torno do centro do saco (o eixo maior fica na
    horizontal) e põe esse centro no centro do recorte, ((L−1)/2, (A−1)/2).
    """
    largura_rec = int(math.ceil(comprimento_px + 2 * folga_px))
    altura_rec = int(math.ceil(largura_px + 2 * folga_px))
    cos, sen = math.cos(angulo), math.sin(angulo)
    rotacao = np.array([[cos, sen], [-sen, cos]])
    destino = np.array([(largura_rec - 1) / 2, (altura_rec - 1) / 2])
    translacao = destino - rotacao @ centro
    return np.hstack([rotacao, translacao[:, None]]), (largura_rec, altura_rec)


def zona_util(mascara: np.ndarray, comprimento_px: float, margem_px: float, extremidades_px: float) -> np.ndarray:
    """Máscara sem a faixa junto ao contorno e sem as pontas do eixo maior."""
    distancia = cv2.distanceTransform(mascara, cv2.DIST_L2, 5)
    util = distancia > margem_px
    centro_x = (mascara.shape[1] - 1) / 2
    colunas = np.arange(mascara.shape[1])
    fora = np.abs(colunas - centro_x) > comprimento_px / 2 - extremidades_px
    util[:, fora] = False
    return util.astype(np.uint8) * 255
