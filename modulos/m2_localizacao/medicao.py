"""M2 Localização, etapa 3: orientação e medidas do saco, como um paquímetro.

O ângulo vem dos momentos da forma, afinado pelos lados compridos; o
comprimento e a largura são as distâncias entre as posições medianas de
lados opostos, longe dos cantos.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.contrato import SemSaco



def pose(arestas: np.ndarray, px_por_mm: float) -> tuple[np.ndarray, float, float, float]:
    """Centro, ângulo do eixo maior (rad, em (−π/2, π/2]), comprimento e largura em px.

    O ângulo vem dos momentos centrais, θ = ½·atan2(2·μ11, μ20 − μ02), que não
    depende das convenções de ângulo do cv2.minAreaRect, e é depois afinado
    pelos lados compridos (reta ajustada à parte central de cada um): um canto
    dobrado ou uma aba desequilibram os momentos e inclinavam o saco ~1°.

    Recebe os pontos do contorno já na aresta (subpíxel, N×2), de
    segmentacao.afinar_contorno, e a escala (para as tolerâncias em mm).

    As medidas são como as de um paquímetro encostado aos lados: a posição de
    cada lado é a mediana dos pontos do contorno nesse lado, longe dos cantos.
    Assim, a "escada" de píxeis de um saco rodado, um canto saliente ou um
    píxel a mais não mexem nas medidas (a forma irregular é assunto do M3).
    O centro é o meio entre os lados (não o centro de massa), para as posições
    em mm irem de −comprimento/2 a +comprimento/2.
    """
    m = cv2.moments(arestas.reshape(-1, 1, 2).astype(np.float32))
    if m["m00"] <= 0:
        raise SemSaco("o contorno encontrado não tem área")
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    pontos = arestas.reshape(-1, 2).astype(np.float64) - (cx, cy)

    angulo = 0.5 * math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"])
    u, v = _projetar(pontos, angulo)
    if np.ptp(v) > np.ptp(u):  # forma quase quadrada: garantir que o eixo "maior" é o maior
        angulo += math.pi / 2
    for _ in range(D.M2_ANGULO_ITERACOES):
        correcao_angulo = _inclinacao_lados(*_projetar(pontos, angulo), D.M2_ANGULO_DESVIO_MM * px_por_mm)
        if correcao_angulo is None:
            break
        angulo += correcao_angulo
    if angulo > math.pi / 2:
        angulo -= math.pi
    elif angulo <= -math.pi / 2:
        angulo += math.pi
    u, v = _projetar(pontos, angulo)

    esquerda, direita, cima, baixo = _lados(u, v)
    cos, sen = math.cos(angulo), math.sin(angulo)
    comprimento = direita - esquerda
    largura = baixo - cima
    meio_u, meio_v = (esquerda + direita) / 2, (cima + baixo) / 2
    centro = np.array([cx + meio_u * cos - meio_v * sen, cy + meio_u * sen + meio_v * cos])
    return centro, angulo, comprimento, largura


LADOS = ("esquerda", "direita", "cima", "baixo")


def qualidade(arestas: np.ndarray, centro: np.ndarray, angulo: float, desvio_px: float) -> dict[str, float]:
    """Quão bem o contorno segue quatro lados retos, para avaliar o contorno (não o saco).

    A parte central de cada lado (sem os cantos, como em _lados) contra a sua reta robusta:
    pontos a mais de desvio_px da reta são rejeitados (um vinco, uma sombra, um pedaço de
    fundo... ou um defeito do saco). Devolve contorno_rms_px, o resíduo RMS dos pontos válidos
    dos quatro lados, rejeitados_<lado>_pct e rejeitados_max_pct, o do pior lado.
    """
    u, v = _projetar(arestas.reshape(-1, 2).astype(np.float64) - centro, angulo)
    meio_u, meio_v = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    metade = D.M2_PARTE_CENTRAL_LADOS / 2
    central_u = np.abs(u - meio_u) < metade * np.ptp(u)
    central_v = np.abs(v - meio_v) < metade * np.ptp(v)
    lados = {  # (posição ao longo do lado, distância ao eixo)
        "esquerda": (v[central_v & (u < meio_u)], u[central_v & (u < meio_u)]),
        "direita": (v[central_v & (u > meio_u)], u[central_v & (u > meio_u)]),
        "cima": (u[central_u & (v < meio_v)], v[central_u & (v < meio_v)]),
        "baixo": (u[central_u & (v > meio_v)], v[central_u & (v > meio_v)]),
    }
    medidas: dict[str, float] = {}
    quadrados, validos_total = 0.0, 0
    for nome, (ao_longo, distancia) in lados.items():
        if len(ao_longo) < 10:
            medidas[f"rejeitados_{nome}_pct"] = 100.0
            continue
        # Começa pela reta paralela ao eixo, na mediana (o ângulo já vem afinado pelos lados), e
        # afina-a só com os pontos perto dela: um vinco colado ao lado não a puxa.
        a, b = 0.0, float(np.median(distancia))
        for _ in range(3):
            residuo = distancia - (a * ao_longo + b)
            validos = np.abs(residuo) <= desvio_px
            if validos.sum() < 10:
                break
            a, b = np.polyfit(ao_longo[validos], distancia[validos], 1)
        residuo = distancia - (a * ao_longo + b)
        validos = np.abs(residuo) <= desvio_px
        medidas[f"rejeitados_{nome}_pct"] = float(100 * (1 - validos.mean()))
        quadrados += float(np.sum(residuo[validos] ** 2))
        validos_total += int(validos.sum())
    medidas["contorno_rms_px"] = math.sqrt(quadrados / validos_total) if validos_total else float("nan")
    medidas["rejeitados_max_pct"] = max(medidas[f"rejeitados_{nome}_pct"] for nome in LADOS)
    return medidas


def _projetar(pontos: np.ndarray, angulo: float) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas dos pontos ao longo do eixo maior (u) e do eixo menor (v)."""
    cos, sen = math.cos(angulo), math.sin(angulo)
    return pontos[:, 0] * cos + pontos[:, 1] * sen, -pontos[:, 0] * sen + pontos[:, 1] * cos


def _inclinacao_lados(u: np.ndarray, v: np.ndarray, desvio_px: float) -> float | None:
    """Quanto rodar (rad) para os lados compridos ficarem na horizontal.

    Ajusta uma reta v = a·u + b à parte central de cada lado comprido, deita
    fora os pontos que se afastam mais do que desvio_px (M2_ANGULO_DESVIO_MM; uma aba,
    um rasgão) e volta a ajustar. Devolve a média das inclinações, pesada
    pelo nº de pontos, ou None se não houver pontos suficientes.
    """
    meio_u, meio_v = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    central = np.abs(u - meio_u) < D.M2_PARTE_CENTRAL_LADOS / 2 * np.ptp(u)
    inclinacoes, pesos = [], []
    for lado in (central & (v < meio_v), central & (v > meio_v)):
        uu, vv = u[lado], v[lado]
        if len(uu) < 10:
            continue
        a, b = np.polyfit(uu, vv, 1)
        perto = np.abs(vv - (a * uu + b)) < desvio_px
        if perto.sum() >= 10:
            a, _ = np.polyfit(uu[perto], vv[perto], 1)
        inclinacoes.append(a)
        pesos.append(int(perto.sum()))
    if not inclinacoes:
        return None
    return math.atan(float(np.average(inclinacoes, weights=pesos)))


def _lados(u: np.ndarray, v: np.ndarray) -> tuple[float, float, float, float]:
    """Posição (mediana) dos lados esquerdo, direito, de cima e de baixo, longe dos cantos."""
    meio_u, meio_v = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    metade = D.M2_PARTE_CENTRAL_LADOS / 2
    central_u = np.abs(u - meio_u) < metade * np.ptp(u)  # parte central dos lados compridos
    central_v = np.abs(v - meio_v) < metade * np.ptp(v)  # parte central dos lados curtos

    def mediana(valores: np.ndarray, extremo: float) -> float:
        return float(np.median(valores)) if len(valores) >= 3 else extremo

    return (
        mediana(u[central_v & (u < meio_u)], u.min()),
        mediana(u[central_v & (u > meio_u)], u.max()),
        mediana(v[central_u & (v < meio_v)], v.min()),
        mediana(v[central_u & (v > meio_v)], v.max()),
    )
