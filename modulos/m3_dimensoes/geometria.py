"""M3 Dimensões, geometria: uma reta a cada lado do saco → esquadria, paralelismo e cantos.

Os ângulos não dependem da escala (nem do px_por_mm): uma ponta cortada de
viés tem o mesmo ângulo a qualquer distância. São por isso a medida mais
direta de um corte torto, mesmo pequeno, que a forma (falta/sobra de área)
só apanha quando a área passa o mínimo.

Cada reta ajusta-se à parte central do lado (sem os cantos arredondados) e é
reajustada sem os pontos que se afastam dela (um canto dobrado, um rasgão,
uma aba): mede-se o corte, não os acidentes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.contrato import Defeito, Saco, TipoDefeito

_COR_RETAS = (255, 255, 0)  # retas ajustadas aos lados: ciano


@dataclass
class Geometria:
    """As 4 retas do saco, no recorte normalizado (origem no centro do saco, px).

    Lados compridos: v = a·u + b; pontas: u = a·v + b (a = inclinação).
    """

    cima: tuple[float, float]
    baixo: tuple[float, float]
    esquerda: tuple[float, float]
    direita: tuple[float, float]

    @property
    def paralelismo_graus(self) -> float:
        """Ângulo entre os lados compridos (0 = paralelos)."""
        return math.degrees(math.atan(self.cima[0]) - math.atan(self.baixo[0]))

    def esquadria_graus(self, ponta: str) -> float:
        """Desvio da ponta ("esquerda" ou "direita") à perpendicular do eixo do saco (0 = em esquadria)."""
        eixo = (math.atan(self.cima[0]) + math.atan(self.baixo[0])) / 2
        return math.degrees(math.atan(getattr(self, ponta)[0]) + eixo)

    def cantos_graus(self) -> list[float]:
        """Ângulo interior de cada canto: cima-esq, cima-dir, baixo-dir, baixo-esq (90 = reto)."""
        ac, ab, ae, ad = self.cima[0], self.baixo[0], self.esquerda[0], self.direita[0]
        pares = [((1, ac), (ae, 1)), ((-1, -ac), (ad, 1)), ((-1, -ab), (-ad, -1)), ((1, ab), (-ae, -1))]
        return [math.degrees(math.acos(np.dot(p, q) / (np.linalg.norm(p) * np.linalg.norm(q)))) for p, q in pares]


def medir(saco: Saco, ppm: float) -> Geometria | None:
    """As 4 retas, a partir do contorno do saco levado para o recorte; None se um lado não tiver pontos."""
    pontos = cv2.transform(saco.contorno.astype(np.float32), saco.matriz).reshape(-1, 2).astype(np.float64)
    altura, largura = saco.mascara.shape
    u, v = pontos[:, 0] - (largura - 1) / 2, pontos[:, 1] - (altura - 1) / 2
    meio_c, meia_l = saco.comprimento_mm * ppm / 2, saco.largura_mm * ppm / 2
    central_u = np.abs(u) < D.M3_GEOMETRIA_PARTE_CENTRAL * meio_c
    central_v = np.abs(v) < D.M3_GEOMETRIA_PARTE_CENTRAL * meia_l
    desvio_max = D.M3_GEOMETRIA_DESVIO_MM * ppm
    retas = (
        _reta(u[central_u & (v < 0)], v[central_u & (v < 0)], desvio_max),
        _reta(u[central_u & (v > 0)], v[central_u & (v > 0)], desvio_max),
        _reta(v[central_v & (u < 0)], u[central_v & (u < 0)], desvio_max),
        _reta(v[central_v & (u > 0)], u[central_v & (u > 0)], desvio_max),
    )
    return None if any(r is None for r in retas) else Geometria(*retas)


def _reta(x: np.ndarray, y: np.ndarray, desvio_max: float) -> tuple[float, float] | None:
    """y = a·x + b, robusta: ajusta, tira os pontos a mais de desvio_max e volta a ajustar."""
    if len(x) < D.M3_GEOMETRIA_PONTOS_MIN:
        return None
    a, b = np.polyfit(x, y, 1)
    for _ in range(D.M3_GEOMETRIA_ITERACOES):
        perto = np.abs(y - (a * x + b)) < desvio_max
        if perto.sum() < D.M3_GEOMETRIA_PONTOS_MIN:
            break
        a, b = np.polyfit(x[perto], y[perto], 1)
    return float(a), float(b)


def defeito_ponta(geo: Geometria, ponta: str, saco: Saco, ppm: float, limite_graus: float) -> Defeito:
    """O Defeito de uma ponta fora de esquadria, com uma faixa ao longo dessa ponta como contorno (em mm).
    limite_graus: a esquadria máxima aceite (para a severidade)."""
    angulo = geo.esquadria_graus(ponta)
    a, b = getattr(geo, ponta)
    altura, largura = saco.mascara.shape
    meia_l, faixa = saco.largura_mm * ppm / 2, ppm  # faixa de ±1 mm à volta da reta
    vs = np.array([-meia_l, meia_l])
    us = a * vs + b
    centro = np.array([(largura - 1) / 2, (altura - 1) / 2])
    poligono = np.array([[us[0] - faixa, vs[0]], [us[1] - faixa, vs[1]], [us[1] + faixa, vs[1]],
                         [us[0] + faixa, vs[0]]]) + centro
    x_mm = b / ppm
    return Defeito(
        TipoDefeito.DIMENSAO,
        x_mm=x_mm,
        y_mm=0.0,
        comprimento_mm=saco.largura_mm,
        largura_mm=2.0,
        angulo_graus=angulo,
        intensidade=abs(angulo),
        severidade=abs(angulo) / limite_graus,
        contorno=saco.px_para_mm(poligono, ppm),
        descricao=f"ponta {ponta} fora de esquadria (corte torto?): {angulo:+.1f} graus em x={x_mm:.0f} mm",
    )


def desenhar(imagem: np.ndarray, geo: Geometria, saco: Saco, ppm: float) -> None:
    """Desenha as 4 retas (ciano) e o ângulo de cada ponta e o paralelismo, na imagem do recorte."""
    altura, largura = imagem.shape[:2]
    cx, cy = (largura - 1) / 2, (altura - 1) / 2
    meio_c, meia_l = saco.comprimento_mm * ppm / 2 + 2 * ppm, saco.largura_mm * ppm / 2 + 2 * ppm
    espessura = max(1, round(ppm / 5))
    for a, b in (geo.cima, geo.baixo):
        cv2.line(imagem, (round(cx - meio_c), round(cy + a * -meio_c + b)),
                 (round(cx + meio_c), round(cy + a * meio_c + b)), _COR_RETAS, espessura)
    for a, b in (geo.esquerda, geo.direita):
        cv2.line(imagem, (round(cx + a * -meia_l + b), round(cy - meia_l)),
                 (round(cx + a * meia_l + b), round(cy + meia_l)), _COR_RETAS, espessura)
    escala_texto = max(0.4, ppm / 10)
    for ponta, x in (("esquerda", cx - meio_c * 0.75), ("direita", cx + meio_c * 0.55)):
        _texto(imagem, f"{geo.esquadria_graus(ponta):+.1f}", (round(x), round(cy)), escala_texto)
    _texto(imagem, f"paral. {geo.paralelismo_graus:+.1f}", (round(cx - meio_c * 0.2), round(cy - meia_l * 0.5)),
           escala_texto)


def _texto(imagem: np.ndarray, texto: str, posicao: tuple[int, int], escala: float) -> None:
    """Texto ciano com orla escura (lê-se sobre qualquer cor)."""
    espessura = max(1, round(escala * 2))
    cv2.putText(imagem, texto, posicao, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), espessura + 2)
    cv2.putText(imagem, texto, posicao, cv2.FONT_HERSHEY_SIMPLEX, escala, _COR_RETAS, espessura)
