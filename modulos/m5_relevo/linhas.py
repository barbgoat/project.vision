"""M5 Relevo: rugas como linhas finas e compridas no recorte normalizado do saco.

1. Linhas finas pela Hessiana, várias escalas e as duas polaridades (vale escuro e crista clara).
2. Teste de simetria: numa linha os dois lados são parecidos e o centro difere; na borda de um
   reflexo (degrau) os lados diferem. Teste de tinta: um fio muito mais escuro do que os lados é impressão.
3. A impressão sai da referência: a tinta que se repete nos sacos OK do produto, alinhados a este
   (cada ruga cai num sítio diferente; a impressão cai sempre no mesmo), com folga para o alinhamento.
4. Procura de retas por ângulo, só com os pontos com a direção da reta: troços compridos com prova
   espalhada, que se destacam das retas paralelas ao lado e atravessam sem a contar como prova o que não
   deixa ver uma ruga (queimado, tinta do próprio saco, impressão da referência, zonas muito escuras).
Ruga: uma reta com prova >= prova_min_mm e comprimento >= ruga_min_mm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

QUEIMADO = 250  # acima disto o sensor saturou: não há informação
ALINHAMENTO_MIN = 0.5  # correlação mínima com a referência para usar a impressão dela

PARAMETROS = {
    "sigmas_px": (1.0, 1.5, 2.5),
    "limiar": 0.10,  # resposta da Hessiana relativa ao brilho local (relevo.limiar)
    "tinta_rel": 0.45,  # fio escuro mais escuro do que isto × lados: tinta
    "simetria_max": 1.5,  # |lado1 - lado2| <= isto × profundidade
    "densidade_max": 0.35,  # fração de pontos de linha numa janela de 8 mm: texto/textura
    "tinta_limiar": 0.25,  # black-hat (traço escuro fino) relativo ao brilho local: tinta
    "tinta_largura_mm": 1.5,  # traços até esta largura
    "tinta_folga_mm": 1.0,  # à volta da tinta do próprio saco não se procuram rugas (atravessa-se)
    "impressao_fracao": 0.5,  # tinta em >= isto das referências (onde se veem): impressão
    "impressao_folga_mm": 1.0,
    "faixa_mm": 0.6,  # tolerância lateral à volta da reta
    "falha_mm": 8.0,  # falha visível máxima dentro de uma ruga
    "ruga_min_mm": 40.0,
    "prova_min_mm": 20.0,  # traço visível mínimo ao longo da reta
    "cobertura_min": 0.4,  # da parte visível da reta
    "vizinhas_mm": 2.5,  # retas paralelas a esta distância: a ruga tem de ter pelo menos prova_min a mais do que elas
    "orientacao_graus": 15.0,  # cada reta só conta os pontos de linha com a direção dela (± isto)
    "alinhado_graus": 0.0,  # retas a menos disto dos eixos do saco ficam de fora (0: procuram-se todas)
    "passo_graus": 1.0,
    "busca_px_por_mm": 3.5,  # a procura de retas faz-se numa grelha reduzida até perto disto (fator inteiro)
    "escuro": 50,  # abaixo disto (níveis) uma ruga de ~10 % muda menos de 5 níveis: não se vê, como no queimado
}


@dataclass
class Preparado:
    cinza: np.ndarray
    util: np.ndarray
    visivel: np.ndarray
    queimado: np.ndarray
    resposta: np.ndarray  # a maior resposta das linhas (as duas polaridades)
    linhas: np.ndarray  # bool: pontos de linha que passam a simetria e a tinta
    tinta: np.ndarray  # bool: traços escuros finos (a impressão, e também algumas rugas)
    pequena: np.ndarray  # para alinhar: brilho normalizado, 1/4
    ppm: float
    orientacao: np.ndarray | None = None  # graus 0–180: a direção da linha em cada ponto de linha


@dataclass
class Referencia:
    pequena: np.ndarray | None  # a 1.ª referência (o referencial)
    impressao: np.ndarray | None  # bool, no referencial
    n: int


def _pequena(cinza: np.ndarray) -> np.ndarray:
    n = cinza / (cv2.GaussianBlur(cinza, (0, 0), 25) + 10)  # tira a exposição e os reflexos largos
    return cv2.resize(n, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA).astype(np.float32)


def _linhas(cinza: np.ndarray, visivel: np.ndarray, p: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(resposta, pontos de linha, orientação) das duas polaridades, com os testes de simetria e de tinta.

    A orientação é a direção da linha em cada ponto, em graus de 0 a 180 (0 = ao longo do eixo x)."""
    h, w = cinza.shape
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    resp_total = np.zeros((h, w), np.float32)
    pontos = np.zeros((h, w), bool)
    resp_pontos = np.zeros((h, w), np.float32)  # a resposta da polaridade que deu o ponto
    orientacao = np.zeros((h, w), np.float32)
    for polaridade in (1, -1):  # 1: fio escuro (vale); -1: fio claro (crista)
        img = cinza if polaridade == 1 else 255 - cinza
        resp = np.zeros((h, w), np.float32)
        ori = np.zeros((h, w), np.float32)
        tinta = np.zeros((h, w), bool)
        degrau = np.zeros((h, w), bool)
        for s in p["sigmas_px"]:
            b = cv2.GaussianBlur(img, (0, 0), s)
            xx = cv2.Sobel(b, cv2.CV_32F, 2, 0); yy = cv2.Sobel(b, cv2.CV_32F, 0, 2); xy = cv2.Sobel(b, cv2.CV_32F, 1, 1)
            d = np.sqrt((xx - yy) ** 2 + 4 * xy ** 2)
            l2, l1 = (xx + yy + d) / 2, (xx + yy - d) / 2  # l2: curvatura forte (positiva num vale)
            r = np.where(l2 > 0, s * s * l2 * np.exp(-(l1 / (l2 + 1e-6)) ** 2 / 0.5), 0) / (b + 20)
            # Normal à linha: o vetor próprio de l2. As duas formas valem; uma degenera (0, 0) quando a
            # linha está alinhada com um eixo (p.ex. (xy, l2 − xx) numa linha vertical): usa-se a outra.
            nx, ny = xy, l2 - xx
            outra = (l2 - yy) ** 2 + xy ** 2 > nx * nx + ny * ny
            nx, ny = np.where(outra, l2 - yy, nx), np.where(outra, xy, ny)
            nn = np.sqrt(nx * nx + ny * ny) + 1e-6
            nx, ny = nx / nn, ny / nn
            k = max(2.5, 2.5 * s)
            lado1 = cv2.remap(b, gx + k * nx, gy + k * ny, cv2.INTER_LINEAR)
            lado2 = cv2.remap(b, gx - k * nx, gy - k * ny, cv2.INTER_LINEAR)
            lados = np.minimum(lado1, lado2)
            prof = lados - b
            melhor = r > resp
            resp = np.where(melhor, r, resp)
            ori = np.where(melhor, np.degrees(np.arctan2(nx, -ny)) % 180, ori)  # a linha é perpendicular à normal
            if polaridade == 1:
                tinta = np.where(melhor, b < (1 - p["tinta_rel"]) * lados, tinta)
            degrau = np.where(melhor, (prof < 2) | (np.abs(lado1 - lado2) > np.maximum(prof, 2) * p["simetria_max"]),
                              degrau)
        novos = (resp > p["limiar"]) & ~tinta & ~degrau & visivel
        mais_forte = novos & (resp > resp_pontos)
        orientacao = np.where(mais_forte, ori, orientacao)
        resp_pontos = np.where(mais_forte, resp, resp_pontos)
        pontos |= novos
        resp_total = np.maximum(resp_total, resp)
    return resp_total, pontos, orientacao


def _tinta(cinza: np.ndarray, visivel: np.ndarray, ppm: float, p: dict) -> np.ndarray:
    """Traços escuros finos: black-hat (fecho - imagem) relativo ao brilho local. Os reflexos largos não dão."""
    k = int(p["tinta_largura_mm"] * ppm) | 1
    bh = cv2.morphologyEx(cinza, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return (bh / (cv2.GaussianBlur(cinza, (0, 0), 10) + 20) > p["tinta_limiar"]) & visivel


def _rapido(ref: np.ndarray, alvo: np.ndarray) -> tuple[float, bool, np.ndarray]:
    """(correlação, rodado 180°, alvo rodado e com o tamanho da ref): sem o ECC, só a correlação direta."""
    melhor = None
    for r180 in (False, True):
        a = cv2.rotate(alvo, cv2.ROTATE_180) if r180 else alvo
        a = cv2.resize(a, ref.shape[::-1])
        ncc = float(cv2.matchTemplate(a, ref, cv2.TM_CCOEFF_NORMED)[0, 0])
        if melhor is None or ncc > melhor[0]:
            melhor = (ncc, r180, a)
    return melhor


def correlacao(ref: np.ndarray, alvo: np.ndarray) -> float:
    """O quanto duas imagens pequenas (Preparado.pequena) se parecem, na melhor das duas rotações (rápido)."""
    return _rapido(ref, alvo)[0]


def _alinhar(ref: np.ndarray, alvo: np.ndarray) -> tuple[bool, np.ndarray, float]:
    """(rodado 180°, matriz afim ECC na escala 1/4 que leva o alvo à ref, correlação)."""
    ncc, r180, a = _rapido(ref, alvo)
    m = np.eye(2, 3, dtype=np.float32)
    try:
        ncc, m = cv2.findTransformECC(ref, a, m, cv2.MOTION_AFFINE,
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-4), None, 5)
    except cv2.error:
        pass
    return r180, m, float(ncc)


def _levar(mascara: np.ndarray, r180: bool, m: np.ndarray, forma: tuple[int, int], inversa: bool) -> np.ndarray:
    """Uma máscara (da escala inteira) pelo alinhamento de _alinhar, para a forma dada."""
    m = m.copy()
    m[:, 2] *= 4
    flags = cv2.INTER_NEAREST | (cv2.WARP_INVERSE_MAP if inversa else 0)
    u8 = mascara.astype(np.uint8)
    if inversa:  # alvo -> referencial: rodar primeiro
        u8 = cv2.rotate(u8, cv2.ROTATE_180) if r180 else u8
        u8 = cv2.resize(u8, (forma[1], forma[0]), interpolation=cv2.INTER_NEAREST)
        return cv2.warpAffine(u8, m, (forma[1], forma[0]), flags=flags) > 0
    u8 = cv2.warpAffine(u8, m, (u8.shape[1], u8.shape[0]), flags=flags)  # referencial -> alvo
    u8 = cv2.resize(u8, (forma[1], forma[0]), interpolation=cv2.INTER_NEAREST)
    return (cv2.rotate(u8, cv2.ROTATE_180) if r180 else u8) > 0


def _ate_segmentos(pontos: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distância (k × n) de cada um dos n pontos (n × 2) a cada segmento a[k]–b[k] (k × 2)."""
    ab = (b - a)[:, None, :]
    t = np.clip(((pontos[None] - a[:, None, :]) * ab).sum(2) / np.maximum((ab * ab).sum(2), 1e-9), 0, 1)
    return np.linalg.norm(a[:, None, :] + t[..., None] * ab - pontos[None], axis=2)


def _a_vista(p1: np.ndarray, p2: np.ndarray, escondido: np.ndarray, n: int = 33) -> tuple[np.ndarray, np.ndarray]:
    """(n pontos ao longo do segmento, quais estão à vista)."""
    amostras = p1[:2] + np.linspace(0, 1, n)[:, None] * (p2[:2] - p1[:2])
    xs = np.clip(np.round(amostras[:, 0]).astype(int), 0, escondido.shape[1] - 1)
    ys = np.clip(np.round(amostras[:, 1]).astype(int), 0, escondido.shape[0] - 1)
    return amostras, ~escondido[ys, xs]


MAX_RUGAS = 20  # rugas distintas a reportar (o saco já é NOK com uma)
MAX_ACHADOS = 2000  # troços considerados para separar rugas distintas (os de mais prova)


ORIENTACOES = 18  # classes de direção dos pontos (de 10 em 10 graus)


def retas(pontos: np.ndarray, queimado: np.ndarray, ppm: float, p: dict,
          orientacao: np.ndarray | None = None) -> list[tuple[float, float, float, np.ndarray, np.ndarray]]:
    """Retas (comprimento mm, prova mm, ângulo, p1, p2), da mais provada para a menos: roda até a reta
    ficar vertical e, em cada coluna, procura troços com falhas visíveis <= falha_mm (o queimado
    atravessa-se) e prova suficiente, que se destaquem das retas paralelas ao lado. Uma por ruga (os
    troços quase iguais juntam-se).

    orientacao (graus 0–180, a direção da linha em cada ponto): cada reta só conta os pontos com a
    direção dela (± orientacao_graus); os contornos de letras e desenhos que ela corta não contam.

    A procura faz-se numa grelha reduzida por um fator inteiro (busca_px_por_mm): uma célula tem um
    ponto se tiver algum, e só está queimada se estiver toda. Os extremos voltam à escala inteira."""
    escondido = queimado  # à escala inteira, para separar as rugas no fim
    fator = max(1, int(ppm / p["busca_px_por_mm"]))
    hh, ww = pontos.shape[0] // fator, pontos.shape[1] // fator

    def reduzir(mascara: np.ndarray) -> np.ndarray:
        u8 = mascara.astype(np.uint8) * 255
        return u8 if fator == 1 else cv2.resize(u8[:hh * fator, :ww * fator], (ww, hh), interpolation=cv2.INTER_AREA)

    if orientacao is None:
        por_direcao = None
        todos = reduzir(pontos)
    else:  # os pontos de cada classe de direção, já na grelha reduzida
        classe = (orientacao * ORIENTACOES / 180).astype(np.int32) % ORIENTACOES
        por_direcao = [reduzir(pontos & (classe == c)) > 0 for c in range(ORIENTACOES)]
        todos = None
    queimado = reduzir(queimado) == 255
    ppm_inteira, ppm = ppm, ppm / fator
    h, w = queimado.shape
    D = int(np.hypot(h, w)) + 4
    faixa = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * int(round(p["faixa_mm"] * ppm)) + 1, 1))
    falha = cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(p["falha_mm"] * ppm)))
    q8 = queimado.astype(np.uint8) * 255
    ha_queimado = bool(queimado.any())
    ruga_min, prova_min = p["ruga_min_mm"] * ppm, p["prova_min_mm"] * ppm
    centros = (np.arange(ORIENTACOES) + 0.5) * 180 / ORIENTACOES
    partes: list[tuple[np.ndarray, ...]] = []
    for ang in np.arange(-90, 90, p["passo_graus"]):
        if min(abs(ang), abs(abs(ang) - 90)) < p["alinhado_graus"]:
            continue
        m = cv2.getRotationMatrix2D((w / 2, h / 2), float(ang), 1.0)
        m[:, 2] += (D - w) / 2, (D - h) / 2
        if por_direcao is None:
            h8 = todos
        else:  # a direção da reta (a vertical depois de rodar) na imagem original
            vx, vy = cv2.invertAffineTransform(m)[:, 1]
            direcao = np.degrees(np.arctan2(vy, vx)) % 180
            diferenca = np.abs(centros - direcao)
            perto = np.minimum(diferenca, 180 - diferenca) <= p["orientacao_graus"] + 90 / ORIENTACOES
            h8 = np.logical_or.reduce([por_direcao[c] for c in np.nonzero(perto)[0]]).astype(np.uint8) * 255
        r_todas = cv2.dilate(cv2.warpAffine(h8, m, (D, D), flags=cv2.INTER_NEAREST), faixa) > 0
        cand = np.nonzero(r_todas.sum(0) >= prova_min)[0]
        if not len(cand):
            continue
        q_todas = cv2.warpAffine(q8, m, (D, D), flags=cv2.INTER_NEAREST) > 0 if ha_queimado else None
        r = r_todas[:, cand]
        q = q_todas[:, cand] if ha_queimado else np.zeros_like(r)
        ligado = cv2.morphologyEx(((r | q) * 255).astype(np.uint8), cv2.MORPH_CLOSE, falha) > 0
        # Os troços de todas as colunas de uma vez: inícios e fins (exclusivos), coluna a coluna
        borda = np.zeros((1, len(cand)), np.int8)
        d = np.diff(np.vstack([borda, ligado.astype(np.int8), borda]), axis=0).T
        col, y0 = np.nonzero(d == 1)
        _, y1 = np.nonzero(d == -1)
        longos = (y1 - y0) >= ruga_min
        if not longos.any():
            continue
        col, y0, y1 = col[longos], y0[longos], y1[longos]
        zero = np.zeros((1, len(cand)), np.int32)
        provado = np.vstack([zero, np.cumsum(r & ~q, axis=0, dtype=np.int32)])
        visivel = np.vstack([zero, np.cumsum(~q, axis=0, dtype=np.int32)])
        prova = provado[y1, col] - provado[y0, col]
        vistos = visivel[y1, col] - visivel[y0, col]
        bons = (prova >= prova_min) & (prova >= p["cobertura_min"] * np.maximum(vistos, 1))
        if not bons.any():
            continue
        col, y0, y1, prova = col[bons], y0[bons], y1[bons], prova[bons]
        # Uma ruga destaca-se da vizinhança: as retas paralelas a ±vizinhas_mm, nas mesmas linhas, têm de
        # ter pelo menos prova_min a menos. Numa textura (reflexos, texto, folha metalizada) qualquer reta
        # junta pontos ao acaso, e as do lado juntam outros tantos.
        provado_todas = np.vstack([np.zeros((1, D), np.int32), np.cumsum(
            r_todas & ~q_todas if ha_queimado else r_todas, axis=0, dtype=np.int32)])
        k = max(1, int(round(p["vizinhas_mm"] * ppm)))
        vizinhas = np.clip(cand[col][:, None] + np.array([-k, k]), 0, D - 1)
        fundo = (provado_todas[y1[:, None], vizinhas] - provado_todas[y0[:, None], vizinhas]).max(axis=1)
        destaca = prova - fundo >= prova_min
        if not destaca.any():
            continue
        col, y0, y1, prova = col[destaca], y0[destaca], y1[destaca], prova[destaca]
        inv = cv2.invertAffineTransform(m)
        x = cand[col].astype(np.float64)
        p1 = (inv @ np.vstack([x, y0, np.ones_like(x)])).T
        p2 = (inv @ np.vstack([x, y1, np.ones_like(x)])).T
        partes.append(((y1 - y0) / ppm, prova / ppm, np.full(len(x), float(ang)), p1, p2))
    if not partes:
        return []
    comprimento, prova, angulo, p1, p2 = (np.concatenate(c) for c in zip(*partes))
    p1, p2 = p1 * fator + (fator - 1) / 2, p2 * fator + (fator - 1) / 2  # centro da célula, na escala inteira
    ppm = ppm_inteira
    meios = (p1 + p2) / 2
    guardados: list[int] = []
    # Uma por ruga: um troço com ângulo parecido ao de uma já guardada é a mesma se o centro estiver perto
    # do dela, ou se pelo menos metade da parte à vista estiver em cima dela (um pedaço da mesma reta,
    # achado noutro ângulo ou coluna, ou que só tem prova onde ela passa e segue pelo escondido)
    for i in np.argsort(-prova, kind="stable")[:MAX_ACHADOS]:
        if guardados:
            g = np.array(guardados)
            diferenca = np.abs(angulo[g] - angulo[i])
            parecido = np.minimum(diferenca, 180 - diferenca) <= 10  # -89 e +89 graus são quase a mesma reta
            perto = np.hypot(*(meios[g] - meios[i]).T) <= 8 * ppm
            amostras, vista = _a_vista(p1[i], p2[i], escondido)
            em_cima = (_ate_segmentos(amostras, p1[g, :2], p2[g, :2]) <= 4 * ppm) & vista
            sobre_outra = em_cima.sum(1) >= 0.5 * max(int(vista.sum()), 1)
            if np.any((perto | sobre_outra) & parecido):
                continue
        guardados.append(int(i))
        if len(guardados) >= MAX_RUGAS:
            break
    return [(float(comprimento[i]), float(prova[i]), float(angulo[i]), p1[i], p2[i]) for i in guardados]


def preparar(cinza: np.ndarray, util: np.ndarray, ppm: float, p: dict, com_linhas: bool = True) -> Preparado:
    """Por imagem, uma vez: as linhas (com a direção de cada ponto), a tinta e a versão pequena para
    alinhar. Sem as linhas (com_linhas=False) só se pode alinhar e comparar a tinta (mais rápido)."""
    queimado = util & (cinza >= QUEIMADO)
    visivel = util & ~queimado
    if com_linhas:
        resposta, linhas, orientacao = _linhas(cinza, visivel, p)
    else:
        resposta, linhas, orientacao = np.zeros((1, 1), np.float32), np.zeros((1, 1), bool), None
    tinta = _tinta(cinza, visivel, ppm, p)
    return Preparado(cinza, util, visivel, queimado, resposta, linhas, tinta, _pequena(cinza), ppm, orientacao)


def referencia(outros: list[Preparado], p: dict) -> Referencia:
    """A impressão: a tinta e as linhas que se repetem nos sacos OK (p.ex. traços claros impressos), no
    referencial do 1.º. Os que não alinham com ele (outro produto, outra face) ficam de fora. Cada ruga
    ou reflexo cai num sítio diferente em cada saco; a impressão cai sempre no mesmo."""
    if not outros:
        return Referencia(None, None, 0)
    base = outros[0]
    forma = base.tinta.shape
    k = 2 * int(round(p["impressao_folga_mm"] * base.ppm)) + 1
    folga = np.ones((k, k), np.uint8)
    votos = np.zeros(forma, np.float32)
    vistos = np.zeros(forma, np.float32)
    n = 0
    for o in outros:
        r180, m, ncc = _alinhar(base.pequena, o.pequena)
        if o is not base and ncc < ALINHAMENTO_MIN:
            continue
        marcas = o.tinta | o.linhas if o.linhas.shape == o.tinta.shape else o.tinta
        votos += _levar(cv2.dilate(marcas.astype(np.uint8), folga) > 0, r180, m, forma, inversa=True)
        vistos += _levar(o.visivel, r180, m, forma, inversa=True)
        n += 1
    impressao = (vistos > 0) & (votos >= p["impressao_fracao"] * np.maximum(vistos, 1))
    return Referencia(base.pequena, impressao, n)


@dataclass
class Achado:
    comprimento_mm: float
    prova_mm: float
    angulo_graus: float  # no recorte (0 = ao longo do eixo maior)
    p1: np.ndarray  # extremos, px do recorte (x, y)
    p2: np.ndarray


@dataclass
class Deteccao:
    achados: list[Achado]
    alinhamento: float  # correlação com a referência (nan sem referência)
    impressao_usada: bool
    imagens: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def prova_mm(self) -> float:
        return max((a.prova_mm for a in self.achados), default=0.0)


def detetar(prep: Preparado, ref: Referencia | None, p: dict, imagens: bool = False) -> Deteccao:
    ppm = prep.ppm
    impressao, ncc = np.zeros_like(prep.linhas), float("nan")
    if ref is not None and ref.impressao is not None:
        r180, m, ncc = _alinhar(ref.pequena, prep.pequena)
        if ncc >= ALINHAMENTO_MIN:
            impressao = _levar(ref.impressao, r180, m, prep.cinza.shape, inversa=False)
    usada = bool(impressao.any())
    # A tinta deste saco (texto, desenhos), com folga: um vinco (10–15 % de contraste) não é tinta, e as
    # linhas de texto, vistas de longe, parecem retas
    k = 2 * int(round(p["tinta_folga_mm"] * ppm)) + 1
    tinta = cv2.dilate(prep.tinta.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    cand = prep.linhas & ~impressao & ~tinta
    jan = int(8 * ppm) | 1
    denso = cv2.boxFilter(cand.astype(np.float32), -1, (jan, jan)) > p["densidade_max"]
    soltos = cand & ~denso
    # A impressão, a tinta e o muito escuro (p.ex. um quadrado preto) são como o queimado: uma ruga não se
    # vê aí, por isso atravessa-se sem contar como prova
    escondido = prep.queimado | (prep.util & (impressao | tinta | (prep.cinza < p["escuro"])))
    achados = [Achado(c, pr, ang, p1, p2) for c, pr, ang, p1, p2 in retas(soltos, escondido, ppm, p, prep.orientacao)]
    ver = {}
    if imagens:
        cand_img = np.zeros((*cand.shape, 3), np.uint8)
        cand_img[prep.linhas] = (90, 90, 90)
        cand_img[impressao & prep.util] = (60, 60, 140)
        cand_img[soltos] = (0, 255, 255)
        cand_img[cand & denso] = (255, 120, 0)
        ver = {"resposta": prep.resposta, "linhas": prep.linhas, "impressao": impressao,
               "candidatos": cand_img}
    return Deteccao(achados, ncc, usada, ver)
