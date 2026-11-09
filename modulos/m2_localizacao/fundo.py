"""M2 Localização, etapa 1: o modelo do fundo e a diferença de cor ao fundo.

O fundo é uma foto do fundo vazio (LOCALIZACAO_FUNDO_REFERENCIA) ou, sem ela, uma
superfície suave ajustada à faixa junto à borda da imagem, onde só há fundo.
A diferença de cor (ΔE, em CIELAB) de cada píxel ao fundo é o que separa o saco.
"""

from __future__ import annotations

import functools

import cv2
import numpy as np

from nucleo import defines as D



def lab(img: np.ndarray, sigma_px: float) -> np.ndarray:
    """BGR uint8 → CIELAB float32 (L* 0–100, a*, b* com sinal), ligeiramente suavizada."""
    # cv2.multiply: o mesmo que img.astype(float32) / 255, bit a bit, numa só passagem (sem a cópia intermédia)
    lab = cv2.cvtColor(cv2.multiply(img, 1 / 255, dtype=cv2.CV_32F), cv2.COLOR_BGR2Lab)
    return cv2.GaussianBlur(lab, (0, 0), max(sigma_px, D.M2_SUAVIZACAO_MIN_PX))


@functools.lru_cache(maxsize=8)
def faixa_da_borda(forma: tuple[int, int], largura: int) -> np.ndarray:
    """Máscara booleana da faixa junto à borda da imagem (onde só há fundo). Só de leitura: é feita
    uma vez por tamanho de imagem e partilhada."""
    faixa = np.zeros(forma, bool)
    faixa[:largura], faixa[-largura:] = True, True
    faixa[:, :largura], faixa[:, -largura:] = True, True
    faixa.flags.writeable = False
    return faixa


@functools.lru_cache(maxsize=8)
def indices_da_faixa(forma: tuple[int, int], largura: int) -> np.ndarray:
    """Os índices (na imagem achatada) dos píxeis da faixa da borda: ler img.reshape(-1, ...)[indices]
    é bem mais rápido do que img[faixa] com a máscara booleana."""
    indices = np.flatnonzero(faixa_da_borda(forma, largura))
    indices.flags.writeable = False
    return indices


def desvio_mediano(d: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """A mediana de cada canal de d (altura × largura × canais) nos píxeis dados. Igual a
    np.median(d[faixa], axis=0), mas com cada canal contíguo na memória (3× mais rápido)."""
    canais = d.reshape(-1, d.shape[2])[indices].T.copy()
    return np.median(canais, axis=1).astype(d.dtype)


def lab_nos_pontos(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """CIELAB float32 (forma de xs × 3) da imagem BGR uint8 (ou cinzenta) em pontos subpíxel, por
    interpolação bilinear (cv2.remap; fora da imagem, a borda). Só passa a float o retângulo que
    contém os pontos: serve para ler a imagem à resolução total ao longo das normais do contorno sem
    a converter toda."""
    altura, largura = img.shape[:2]
    xs, ys = np.asarray(xs), np.asarray(ys)
    x0, y0 = max(int(np.floor(xs.min())) - 2, 0), max(int(np.floor(ys.min())) - 2, 0)
    x1, y1 = min(int(np.ceil(xs.max())) + 3, largura), min(int(np.ceil(ys.max())) + 3, altura)
    if x1 <= x0 or y1 <= y0:  # todos fora da imagem: a borda mais próxima
        x0, y0 = min(x0, largura - 1), min(y0, altura - 1)
        x1, y1 = x0 + 1, y0 + 1
    recorte = cv2.multiply(img[y0:y1, x0:x1], 1 / 255, dtype=cv2.CV_32F)
    bgr = cv2.remap(recorte, (xs - x0).astype(np.float32), (ys - y0).astype(np.float32), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
    if bgr.ndim == xs.ndim:  # cinzenta (câmara mono sem conversão para BGR): os três canais iguais
        bgr = cv2.merge([bgr, bgr, bgr])
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)


def distancia(d: np.ndarray, peso_luminosidade: float) -> np.ndarray:
    """ΔE a partir da diferença d = lab − fundo, com a luminosidade pesada. Altera d (é temporária)."""
    if peso_luminosidade != 1:
        d[..., 0] *= peso_luminosidade
    quadrado = np.einsum("ijk,ijk->ij", d, d)  # sem as 5 imagens intermédias de (w·dL)² + da² + db²
    return np.sqrt(quadrado, out=quadrado)


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
    return distancia(lab - fundo, peso_luminosidade)
