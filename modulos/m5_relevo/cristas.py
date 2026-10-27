"""M5 Relevo, etapa 2: realçar as cristas e extrair as suas linhas centrais."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nucleo.config import Config


@dataclass
class MapaCristas:
    """Saída de mapa_cristas(): mapas com o tamanho do recorte normalizado."""

    resposta: np.ndarray  # float32: força da crista (máximo sobre as escalas); 0 fora da zona útil
    orientacao: np.ndarray  # float32: direção ao longo da crista, em rad, no intervalo [0, π)
    largura_mm: np.ndarray  # float32: largura estimada a partir da escala de resposta máxima
    mascara: np.ndarray  # uint8 0/255: região das cristas (com a largura toda), após histerese
    centro: np.ndarray  # uint8 0/255: linhas centrais das cristas, com 1 px de espessura


def mapa_cristas(img: np.ndarray, mascara: np.ndarray, cfg: Config) -> MapaCristas:
    """Realça as cristas em várias escalas com a matriz Hessiana e extrai as linhas centrais.

    Recebe:
        img: imagem pré-processada, float32 (saída de preprocessar).
        mascara: zona a inspecionar, uint8 0/255 (Saco.mascara_util); fora dela
            a resposta é 0.
        cfg: configuração; usa geral.px_por_mm e relevo.crista_*.

    Devolve:
        MapaCristas com a resposta, a orientação, a largura estimada, a região
        das cristas e as suas linhas centrais.

    O que vai fazer:
        Para cada σ de crista_sigmas_mm (convertido para px, no mínimo ~1 px):
        1. Suavizar com uma gaussiana de σ e calcular Ixx, Iyy e Ixy
           (cv2.Sobel de 2.ª ordem sobre a imagem suavizada).
        2. Valores próprios λ1, λ2 da Hessiana 2×2 em cada píxel, com
           |λ1| ≤ |λ2| (fórmula fechada, vetorizada em numpy).
        3. Resposta normalizada pela escala, R = σ²·|λ2|, só onde há uma linha
           e não uma mancha (|λ2| bem maior do que |λ1|, p.ex. com o fator de
           Frangi exp(−(λ1/λ2)²/2β²)), e com o sinal pedido em
           crista_polaridade: clara → λ2 < 0, escura → λ2 > 0, ambas → qualquer.
        Combinar as escalas pelo máximo de R, guardando em cada píxel:
        - a largura, a partir do σ vencedor (≈ 2σ para um perfil retangular,
          com a normalização σ²; confirmar com defeitos reais);
        - a orientação ao longo da crista (perpendicular ao vetor próprio de λ2).
        Região (mascara): limiarização com histerese. Os píxeis acima de
        crista_limiar_alto são sementes; os píxeis acima de crista_limiar_baixo
        contam se estiverem ligados a uma semente (componentes ligadas da
        máscara baixa que contêm pelo menos uma semente).
        Linhas centrais (centro), à maneira do detetor de linhas de Steger:
        supressão de não-máximos na direção perpendicular à crista. Um píxel
        da região só fica se R for maior do que nos dois vizinhos ao longo da
        normal, como no Canny. Dá linhas com 1 px de espessura sem precisar de
        esqueletização (o opencv-python não traz o cv2.ximgproc.thinning).
    """
    raise NotImplementedError
