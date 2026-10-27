"""M2 Localização, etapa 2: da diferença ao fundo à região do saco.

Limiar (Otsu, com um mínimo), arestas quando a cor não chega (sacos
transparentes), limpeza morfológica, maior região, e o contorno do saco
levado para a resolução original.
"""

from __future__ import annotations

import cv2
import numpy as np

from nucleo import defines as D


_DELTA_E_PARA_U8 = 255 / 100  # escala do mapa de diferenças para o Otsu (ΔE 100 → 255)

def otsu(diferenca: np.ndarray) -> float:
    """Limiar de Otsu sobre o mapa de diferenças, devolvido em ΔE."""
    u8 = np.clip(diferenca * _DELTA_E_PARA_U8, 0, 255).astype(np.uint8)
    limiar, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(limiar) / _DELTA_E_PARA_U8


def arestas(lab: np.ndarray, faixa: np.ndarray, fator: float) -> np.ndarray:
    """Arestas bem mais fortes do que o "grão" normal do fundo (percentil 99 na faixa da borda)."""
    gx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3)
    gradiente = np.sqrt((gx * gx + gy * gy).sum(axis=2)) / 8  # /8: o Sobel 3×3 amplifica 8×
    normal = max(float(np.percentile(gradiente[faixa], 99)), D.M2_GRADIENTE_FUNDO_MIN)
    return gradiente > fator * normal


def limpar(mascara: np.ndarray, fecho_px: float, abertura_px: float) -> np.ndarray:
    """Fecho (tapa falhas, liga as arestas), preenchimento dos buracos e abertura (tira pontos e fios).

    O preenchimento vem antes da abertura: o contorno de um saco transparente é
    um anel fino, que a abertura apagaria se o interior ainda estivesse vazio.
    """
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, _disco(fecho_px))
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    preenchida = np.zeros_like(mascara)
    cv2.drawContours(preenchida, contornos, -1, 255, thickness=cv2.FILLED)
    return cv2.morphologyEx(preenchida, cv2.MORPH_OPEN, _disco(abertura_px))


def _disco(diametro_px: float) -> np.ndarray:
    """Elemento estruturante circular com o diâmetro dado (ímpar, no mínimo 3 px)."""
    k = max(3, 2 * round(diametro_px / 2) + 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def maior_regiao(mascara: np.ndarray) -> np.ndarray | None:
    """A maior região (há um saco por imagem), preenchida; None se não houver nenhuma."""
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    maior = max(contornos, key=cv2.contourArea)
    if cv2.contourArea(maior) < 1:
        return None
    regiao = np.zeros_like(mascara)
    cv2.drawContours(regiao, [maior], -1, 255, thickness=cv2.FILLED)
    return regiao


def toca_na_borda(regiao: np.ndarray) -> bool:
    return bool(regiao[0].any() or regiao[-1].any() or regiao[:, 0].any() or regiao[:, -1].any())


def contorno_original(regiao: np.ndarray, forma_original: tuple[int, int]) -> np.ndarray:
    """Leva a região para a resolução original e devolve o seu contorno exterior (N×1×2, int32).

    Só amplia a zona à volta do saco (mais rápido do que a imagem toda), com a
    mesma correspondência de píxeis que o cv2.resize da imagem inteira.
    """
    altura, largura = forma_original
    if regiao.shape != (altura, largura):
        fx, fy = largura / regiao.shape[1], altura / regiao.shape[0]
        x, y, w, h = cv2.boundingRect(regiao)
        x0, y0 = max(x - 2, 0), max(y - 2, 0)
        x1, y1 = min(x + w + 2, regiao.shape[1]), min(y + h + 2, regiao.shape[0])
        X0, Y0 = round(x0 * fx), round(y0 * fy)
        X1, Y1 = min(round(x1 * fx), largura), min(round(y1 * fy), altura)
        # píxel (X, Y) da original ↔ ((X + 0,5)/fx − 0,5, (Y + 0,5)/fy − 0,5) da reduzida
        matriz = np.array([[fx, 0, fx * (x0 + 0.5) - 0.5 - X0], [0, fy, fy * (y0 + 0.5) - 0.5 - Y0]])
        ampliada = cv2.warpAffine(regiao[y0:y1, x0:x1], matriz, (X1 - X0, Y1 - Y0), flags=cv2.INTER_LINEAR)
        mascara = np.where(ampliada > 127, 255, 0).astype(np.uint8)
        deslocamento = (X0, Y0)
    else:
        mascara, deslocamento = regiao, (0, 0)
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=deslocamento)
    return max(contornos, key=cv2.contourArea).astype(np.int32)
