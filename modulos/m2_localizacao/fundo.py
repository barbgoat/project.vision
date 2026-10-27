"""M2 Localização, etapa 1: o modelo do fundo e a diferença de cor ao fundo.

O fundo é uma foto do fundo vazio (LOCALIZACAO_FUNDO_REFERENCIA) ou, sem ela, uma
superfície suave ajustada à faixa junto à borda da imagem, onde só há fundo.
A diferença de cor (ΔE, em CIELAB) de cada píxel ao fundo é o que separa o saco.
"""

from __future__ import annotations

import cv2
import numpy as np

from nucleo import defines as D



def lab(img: np.ndarray, sigma_px: float) -> np.ndarray:
    """BGR uint8 → CIELAB float32 (L* 0–100, a*, b* com sinal), ligeiramente suavizada."""
    lab = cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)
    return cv2.GaussianBlur(lab, (0, 0), max(sigma_px, 0.3))


def faixa_da_borda(forma: tuple[int, int], largura: int) -> np.ndarray:
    """Máscara booleana da faixa junto à borda da imagem (onde só há fundo)."""
    faixa = np.zeros(forma, bool)
    faixa[:largura], faixa[-largura:] = True, True
    faixa[:, :largura], faixa[:, -largura:] = True, True
    return faixa


def modelar_fundo(lab: np.ndarray, faixa: np.ndarray) -> np.ndarray:
    """Ajusta uma superfície suave (polinómio de 2.º grau em x, y) à cor da faixa da borda.

    Um polinómio acompanha as variações lentas da luz (luz rasante, vinhetagem).
    O ajuste é robusto: em cada volta descarta os píxeis que se afastam muito
    (sujidade, um canto do saco), para o modelo representar só o fundo.
    """
    altura, largura = lab.shape[:2]
    ys, xs = np.nonzero(faixa)
    passo = max(1, len(xs) // D.M2_FUNDO_PONTOS_MAX)
    xs, ys = xs[::passo], ys[::passo]
    valores = lab[ys, xs].astype(np.float64)
    base = _base_polinomial(xs / (largura - 1) * 2 - 1, ys / (altura - 1) * 2 - 1)

    usar = np.ones(len(xs), bool)
    coeficientes = np.zeros((6, 3))
    for _ in range(D.M2_FUNDO_ITERACOES):
        if usar.sum() < 12:
            break
        coeficientes, *_ = np.linalg.lstsq(base[usar], valores[usar], rcond=None)
        residuo = np.linalg.norm(valores - base @ coeficientes, axis=1)
        mediana = np.median(residuo[usar])
        mad = np.median(np.abs(residuo[usar] - mediana))
        # 1,4826·MAD ≈ desvio-padrão, mas sem se deixar levar pelos pontos estranhos
        usar = residuo <= mediana + D.M2_FUNDO_DESVIOS * 1.4826 * mad + D.M2_FUNDO_TOLERANCIA_DELTA_E

    # Avaliar a superfície em toda a imagem: os termos só de x e só de y
    # calculam-se numa linha/coluna; só o termo x·y precisa da grelha inteira.
    c = coeficientes.astype(np.float32)
    x = np.linspace(-1, 1, largura, dtype=np.float32)[None, :, None]
    y = np.linspace(-1, 1, altura, dtype=np.float32)[:, None, None]
    so_x = c[0] + c[1] * x + c[3] * x * x  # 1 × largura × 3
    so_y = c[2] * y + c[5] * y * y  # altura × 1 × 3
    return so_x + so_y + c[4] * (y * x)


def _base_polinomial(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Colunas 1, x, y, x², xy, y² (a mesma ordem usada em modelar_fundo)."""
    return np.stack([np.ones_like(x, dtype=np.float64), x, y, x * x, x * y, y * y], axis=1)


def diferenca(lab: np.ndarray, fundo: np.ndarray, peso_luminosidade: float) -> np.ndarray:
    """ΔE de cada píxel face ao fundo, com a luminosidade pesada por peso_luminosidade."""
    d = lab - fundo
    return np.sqrt((peso_luminosidade * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)
