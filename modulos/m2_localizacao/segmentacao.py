"""M2 Localização, etapa 2: da diferença ao fundo à região do saco.

Limiar (Otsu, com um mínimo), arestas quando a cor não chega (sacos
transparentes), limpeza morfológica, maior região, e o contorno do saco
levado para a resolução original.
"""

from __future__ import annotations

import functools

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

    O fecho só pode tapar falhas, não acrescentar fundo: um saco é convexo, por isso o que
    fica fora do invólucro convexo das regiões da máscara (sem as manchas pequenas) sai. Sem
    isto, um fecho largo cola ao saco as manchas do fundo junto à aresta (bossas de ~3 mm).
    """
    fechada = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, _disco(fecho_px))
    contornos, _ = cv2.findContours(fechada, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    preenchida = np.zeros_like(fechada)
    cv2.drawContours(preenchida, contornos, -1, 255, thickness=cv2.FILLED)
    preenchida &= _involucro(mascara)
    return cv2.morphologyEx(preenchida, cv2.MORPH_OPEN, _disco(abertura_px))


def _involucro(mascara: np.ndarray) -> np.ndarray:
    """O invólucro convexo (preenchido) das regiões da máscara com pelo menos M2_INVOLUCRO_FRACAO_MIN
    da área da maior: as manchas pequenas (ruído do fundo) não o alargam, os pedaços de um saco
    transparente (o anel partido) sim."""
    n, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    areas = estatisticas[1:, cv2.CC_STAT_AREA]
    involucro = np.zeros_like(mascara)
    if n < 2:
        return involucro
    grandes = np.zeros(n, np.uint8)  # tabela rótulo → 1 se a região é grande (2× mais rápido que np.isin)
    grandes[1:][areas >= D.M2_INVOLUCRO_FRACAO_MIN * areas.max()] = 1
    contornos, _ = cv2.findContours(grandes[rotulos], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.fillPoly(involucro, [cv2.convexHull(np.vstack(contornos))], 255)
    return involucro


def _disco(diametro_px: float) -> np.ndarray:
    """Elemento estruturante circular com o diâmetro dado (ímpar, no mínimo 3 px); feito uma vez por tamanho."""
    return _disco_impar(max(3, 2 * round(diametro_px / 2) + 1))


@functools.lru_cache(maxsize=16)
def _disco_impar(k: int) -> np.ndarray:
    disco = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    disco.flags.writeable = False
    return disco


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


def solidez(regiao: np.ndarray) -> float:
    """Área da região / área do seu invólucro convexo: perto de 1 num saco (mesmo com defeitos),
    menos quando o contorno não fechou e falta uma "baía" do saco."""
    contornos, _ = cv2.findContours(regiao, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    maior = max(contornos, key=cv2.contourArea)
    return float(cv2.contourArea(maior) / max(cv2.contourArea(cv2.convexHull(maior)), 1.0))


def toca_na_borda(regiao: np.ndarray) -> bool:
    return bool(regiao[0].any() or regiao[-1].any() or regiao[:, 0].any() or regiao[:, -1].any())


def afinar_contorno(contorno: np.ndarray, mapa: np.ndarray,
                    forma_original: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Cada ponto do contorno levado para a aresta verdadeira, com precisão subpíxel.

    Devolve (arestas, normais, validos, encontrada): N×2 float em px originais, a normal (para
    fora) em cada ponto, se o máximo do gradiente desse ponto ficou dentro da janela (bool, N; os
    outros não medem) e se aí havia mesmo uma aresta (bool, N: dentro da janela e com uma transição
    de pelo menos M2_AFINAR_GRADIENTE_MIN). Num fundo liso, sem aresta nenhuma, o máximo cai num
    sítio qualquer da janela: o ponto é "válido" mas não foi encontrada aresta (fica onde estava).

    O limiar diz que píxeis são saco, mas a aresta fica onde a diferença ao
    fundo passa ao seu limiar: um limiar baixo "engorda" o saco, um alto
    emagrece-o (frações de píxel de cada lado, o que conta nas medidas). A
    aresta verdadeira é onde a diferença muda mais depressa: o máximo do
    gradiente ao longo da normal, afinado por uma parábola. Onde a transição é
    fraca, o ponto fica na aresta da máscara (meio píxel para fora dos píxeis de borda).
    A janela centra-se nos píxeis de borda, não na aresta da máscara: as máscaras tendem a
    sair gordas (limiar baixo, fecho), e assim a aresta fica mais longe da ponta de dentro.

    O contorno vem em degraus (os píxeis do mapa, ampliados para a original): as normais
    tiradas dos degraus apontariam para os lados deles, não para fora do saco. Por isso as
    coordenadas são suavizadas ao longo do contorno antes (σ de M2_AFINAR_SUAVIZACAO px do mapa).

    A janela é estreita (± M2_AFINAR_PROCURA px do mapa): mais larga, entra na textura das
    soldaduras e o máximo do gradiente passa a cair aí. Onde a máscara erra mais do que
    isso, as passagens seguintes (M2_AFINAR_PASSAGENS: suavizar, normais de novo, procurar)
    aproximam o contorno da aresta aos poucos. Um máximo na ponta da janela quer dizer que
    a aresta está fora dela: o ponto fica onde estava e não é válido (não serve para medir).

    mapa: o mapa do segmentador (ResultadoSegmentacao.mapa_aresta). 2D: a diferença ao
    fundo (ΔE), que cai para fora do saco. H×W×3: a imagem em CIELAB; conta o módulo do
    gradiente da cor (o saco pode ser mais claro ou mais escuro do que o fundo).
    """
    altura, largura = forma_original
    fx, fy = largura / mapa.shape[1], altura / mapa.shape[0]
    mapa = mapa.astype(np.float32)
    pontos = contorno.reshape(-1, 2).astype(np.float64)
    for passagem in range(D.M2_AFINAR_PASSAGENS):
        pontos = _suavizar(pontos, D.M2_AFINAR_SUAVIZACAO * max(fx, fy))
        normal = _normais(pontos)
        # Sem aresta, o ponto fica na aresta da máscara: na 1.ª passagem, meio píxel para fora dos
        # píxeis de borda; nas seguintes, onde a anterior o deixou.
        deslocamento, validos, forte = _procurar_aresta(pontos, normal, mapa, fx, fy, 0.5 if passagem == 0 else 0.0)
        pontos = pontos + deslocamento[:, None] * normal
    return pontos, normal, validos, validos & forte


def _normais(pontos: np.ndarray) -> np.ndarray:
    """Normal para fora: perpendicular à tangente média dos vizinhos; o sentido vem da orientação do contorno."""
    k = D.M2_AFINAR_VIZINHOS
    tangente = np.roll(pontos, -k, axis=0) - np.roll(pontos, k, axis=0)
    tangente /= np.maximum(np.linalg.norm(tangente, axis=1, keepdims=True), 1e-9)
    normal = np.c_[tangente[:, 1], -tangente[:, 0]]
    area_com_sinal = np.sum(pontos[:, 0] * np.roll(pontos[:, 1], -1) - np.roll(pontos[:, 0], -1) * pontos[:, 1])
    return -normal if area_com_sinal < 0 else normal


def _procurar_aresta(pontos: np.ndarray, normal: np.ndarray, mapa: np.ndarray, fx: float, fy: float,
                     sem_aresta: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(deslocamento, dentro, forte): quanto andar ao longo da normal (px originais) até ao máximo do
    gradiente do mapa, se esse máximo ficou dentro da janela (não numa ponta) e se a transição aí é
    forte (≥ M2_AFINAR_GRADIENTE_MIN). Fora dela ou com a transição fraca, o deslocamento é sem_aresta."""
    passo = max(fx, fy) / 2  # meio píxel da imagem reduzida, em px originais
    passos = np.arange(-D.M2_AFINAR_PROCURA * 2, D.M2_AFINAR_PROCURA * 2 + 1) * passo
    xs = ((pontos[:, 0:1] + passos * normal[:, 0:1] + 0.5) / fx - 0.5).astype(np.float32)
    ys = ((pontos[:, 1:2] + passos * normal[:, 1:2] + 0.5) / fy - 0.5).astype(np.float32)
    perfil = cv2.remap(mapa, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    derivada = np.gradient(perfil, axis=1) / passo * max(fx, fy)  # ΔE (ou L*a*b*) por px do mapa
    # ΔE: quanto a diferença cai para fora; cor: o módulo do gradiente nos 3 canais
    queda = -derivada if derivada.ndim == 2 else np.linalg.norm(derivada, axis=2)
    j = np.argmax(queda, axis=1)
    linhas = np.arange(len(j))
    jj = np.clip(j, 1, len(passos) - 2)
    y0, y1, y2 = queda[linhas, jj - 1], queda[linhas, jj], queda[linhas, jj + 1]
    curvatura = y0 - 2 * y1 + y2
    desvio = np.where(curvatura < 0, 0.5 * (y0 - y2) / np.where(curvatura < 0, curvatura, -1), 0.0)
    dentro = (j > 0) & (j < len(passos) - 1)
    forte = y1 >= D.M2_AFINAR_GRADIENTE_MIN
    deslocamento = np.where(dentro & forte, passos[jj] + desvio * passo, sem_aresta)
    return deslocamento, dentro, forte


def _suavizar(pontos: np.ndarray, sigma: float) -> np.ndarray:
    """Média gaussiana das coordenadas ao longo do contorno fechado (σ em pontos: seguidos, ≈ 1 px)."""
    raio = int(np.ceil(3 * sigma))
    if raio < 1:
        return pontos
    desvios = np.arange(-raio, raio + 1)
    pesos = np.exp(-0.5 * (desvios / sigma) ** 2)
    vizinhos = pontos[(np.arange(len(pontos))[:, None] + desvios) % len(pontos)]  # N×(2·raio+1)×2
    return np.tensordot(vizinhos, pesos / pesos.sum(), axes=([1], [0]))


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
