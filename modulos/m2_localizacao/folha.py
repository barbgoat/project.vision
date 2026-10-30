"""M2 Localização, etapa 0 (opcional): endireitar a foto pela folha de referência.

Nas fotos tiradas à mão, a câmara nunca está bem perpendicular: o saco
aparece como um trapézio (ângulos errados de vários graus) e a escala muda
com a distância. Com o saco pousado numa folha de tamanho conhecido (A4),
os 4 cantos da folha dão a homografia que leva a foto para uma vista de
cima, com GERAL_PX_POR_MM exato. Na linha, com a câmara fixa, não é preciso.

    1. A folha numa versão reduzida: clara e lisa (o papel quase não tem
       textura; um fundo de inox escovado tem muita). O casco convexo ignora
       o saco por cima e o texto impresso; os 4 cantos aproximados.
    2. Cada aresta na resolução total: o máximo do gradiente ao longo da
       normal, com precisão subpíxel, e uma reta robusta a esses pontos.
    3. Cantos = cruzamento das retas; homografia; recorte sem uma margem
       junto às arestas da folha (para não ficar fundo na imagem).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.config import Config
from nucleo.contrato import SemSaco


def retificar(img: np.ndarray, cfg: Config) -> np.ndarray:
    """A folha vista de cima, a cfg.geral.px_por_mm, sem a margem junto às arestas.

    Recebe:
        img: a foto original, BGR uint8.
        cfg: usa geral.px_por_mm, localizacao.folha_* e localizacao.referencia_margem_mm.
    Devolve:
        A imagem endireitada, ao alto ou deitada como a folha estava na foto.
        Levanta SemSaco se não encontrar a folha inteira.
    """
    p = cfg.localizacao
    cantos = _ordenar(_afinar_cantos(img, _cantos_aproximados(img)))
    lado_curto, lado_comprido = sorted((p.folha_largura_mm, p.folha_altura_mm))
    primeiro, segundo = np.linalg.norm(cantos[1] - cantos[0]), np.linalg.norm(cantos[2] - cantos[1])
    largura, altura = (lado_curto, lado_comprido) if primeiro < segundo else (lado_comprido, lado_curto)

    ppm, margem = cfg.geral.px_por_mm, p.referencia_margem_mm
    destino = (np.array([[0, 0], [largura, 0], [largura, altura], [0, altura]], np.float32) - margem) * ppm
    matriz = cv2.getPerspectiveTransform(cantos, destino)
    tamanho = (round((largura - 2 * margem) * ppm), round((altura - 2 * margem) * ppm))
    return cv2.warpPerspective(img, matriz, tamanho, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _cantos_aproximados(img: np.ndarray) -> np.ndarray:
    """Os 4 cantos da folha (px da imagem original), achados numa versão reduzida."""
    escala = min(1.0, D.M2_FOLHA_DETECAO_PX / max(img.shape[:2]))
    pequena = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    cinza = cv2.cvtColor(pequena, cv2.COLOR_BGR2GRAY).astype(np.float32)
    janela = (D.M2_FOLHA_TEXTURA_JANELA_PX,) * 2
    media = cv2.blur(cinza, janela)
    textura = np.sqrt(np.maximum(cv2.blur(cinza * cinza, janela) - media * media, 0))
    claro, _ = cv2.threshold(cv2.GaussianBlur(cinza.astype(np.uint8), (D.M2_FOLHA_DESFOQUE_PX,) * 2, 0), 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    papel = ((media > claro) & (textura < D.M2_FOLHA_TEXTURA_MAX)).astype(np.uint8) * 255
    papel = cv2.morphologyEx(papel, cv2.MORPH_OPEN, np.ones((D.M2_FOLHA_ABERTURA_PX,) * 2, np.uint8))  # pontes p/ reflexos
    contornos, _ = cv2.findContours(papel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        raise SemSaco("folha de referência não encontrada (nada claro e liso na imagem)")
    casco = cv2.convexHull(max(contornos, key=cv2.contourArea))
    if cv2.contourArea(casco) < D.M2_FOLHA_AREA_MIN * papel.size:
        raise SemSaco("folha de referência não encontrada (a maior zona clara e lisa é pequena demais)")
    aproximado = cv2.approxPolyDP(casco, D.M2_FOLHA_APROXIMACAO * cv2.arcLength(casco, True), True).reshape(-1, 2)
    if len(aproximado) != 4:
        raise SemSaco(f"a folha de referência não tem 4 cantos visíveis (encontrados {len(aproximado)})")
    if (casco[:, 0, 0].min() <= 0 or casco[:, 0, 1].min() <= 0
            or casco[:, 0, 0].max() >= papel.shape[1] - 1 or casco[:, 0, 1].max() >= papel.shape[0] - 1):
        raise SemSaco("a folha de referência toca na borda da imagem (tem de aparecer inteira)")
    return (aproximado.astype(np.float64) + 0.5) / escala - 0.5


def _afinar_cantos(img: np.ndarray, aproximados: np.ndarray) -> np.ndarray:
    """Cantos precisos: retas ajustadas a cada aresta na resolução total, cruzadas duas a duas."""
    cinza = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0),
                             D.M2_FOLHA_SUAVIZACAO_PX)
    gx, gy = cv2.Sobel(cinza, cv2.CV_32F, 1, 0), cv2.Sobel(cinza, cv2.CV_32F, 0, 1)
    procura = max(10, round(D.M2_FOLHA_PROCURA * max(img.shape[:2]) / D.M2_FOLHA_DETECAO_PX))
    passos = np.arange(-procura, procura + 1)
    retas = []
    for i in range(4):
        a, b = aproximados[i], aproximados[(i + 1) % 4]
        direcao = (b - a) / np.linalg.norm(b - a)
        normal = np.array([-direcao[1], direcao[0]])
        # Pontos de amostragem ao longo da parte central da aresta × posições ao longo da normal
        centrais = a + np.linspace(0.1, 0.9, D.M2_FOLHA_PONTOS_ARESTA)[:, None] * (b - a)
        xs = (centrais[:, 0:1] + passos * normal[0]).astype(np.float32)
        ys = (centrais[:, 1:2] + passos * normal[1]).astype(np.float32)
        perfil = np.abs(cv2.remap(gx, xs, ys, cv2.INTER_LINEAR) * normal[0]
                        + cv2.remap(gy, xs, ys, cv2.INTER_LINEAR) * normal[1])
        j = np.argmax(perfil, axis=1)
        dentro = (j > 0) & (j < len(passos) - 1)  # máximo no limite da janela: a aresta não está ali
        if dentro.sum() < D.M2_FOLHA_PONTOS_ARESTA // 4:
            raise SemSaco("não foi possível seguir uma aresta da folha de referência")
        j, centrais, perfil = j[dentro], centrais[dentro], perfil[dentro]
        linhas = np.arange(len(j))
        y0, y1, y2 = perfil[linhas, j - 1], perfil[linhas, j], perfil[linhas, j + 1]
        curvatura = y0 - 2 * y1 + y2
        desvio = np.where(curvatura < 0, 0.5 * (y0 - y2) / np.where(curvatura < 0, curvatura, -1), 0)
        pontos = centrais + (passos[j] + desvio)[:, None] * normal
        retas.append(cv2.fitLine(pontos.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel())

    cantos = []
    for i in range(4):
        (vx1, vy1, x1, y1), (vx2, vy2, x2, y2) = retas[i - 1], retas[i]
        sistema = np.array([[vx1, -vx2], [vy1, -vy2]], np.float64)
        if abs(np.linalg.det(sistema)) < math.sin(math.radians(D.M2_FOLHA_ANGULO_MIN_GRAUS)):
            raise SemSaco("a folha de referência não tem 4 cantos visíveis (arestas quase paralelas)")
        t = np.linalg.solve(sistema, [x2 - x1, y2 - y1])[0]
        cantos.append((x1 + t * vx1, y1 + t * vy1))
    return np.array(cantos, np.float32)


def _ordenar(cantos: np.ndarray) -> np.ndarray:
    """Cantos no sentido dos ponteiros do relógio, a começar no de cima à esquerda."""
    centro = cantos.mean(axis=0)
    ordem = np.argsort(np.arctan2(cantos[:, 1] - centro[1], cantos[:, 0] - centro[0]))
    cantos = cantos[ordem]
    return np.roll(cantos, -int(np.argmin(cantos.sum(axis=1))), axis=0)
