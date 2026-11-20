"""M5 Relevo: rugas como linhas finas e compridas no recorte normalizado do saco.

1. Linhas finas pela Hessiana, várias escalas e as duas polaridades (vale escuro e crista clara). As
   dobras largas e suaves (num saco mate) pela mesma Hessiana numa imagem reduzida, só o centro de cada uma.
2. Teste de simetria: numa linha os dois lados são parecidos e o centro difere; na borda de um
   reflexo (degrau) os lados diferem. Teste de tinta: um fio muito mais escuro do que os lados é impressão.
3. A impressão sai da referência: os sacos OK ensinados (vistas), cada um alinhado com este pelos pontos
   de interesse (SIFT: qualquer posição e rotação, e só uma parte em comum, como na imagem toda de um saco
   maior do que o campo) ou, sem eles, pela correlação. As marcas que se repetem nas vistas alinhadas são
   impressão (cada ruga cai num sítio diferente; a impressão cai sempre no mesmo), com folga. As linhas
   que o administrador marcou como "não é ruga" vêm da mesma maneira (a imagem onde foram marcadas alinha
   com este saco), sem votos: uma reta achada com metade ou mais dentro da faixa de uma delas não conta.
4. Procura de retas por ângulo, só com os pontos com a direção da reta: troços compridos com prova
   espalhada, que se destacam das retas paralelas ao lado e atravessam sem a contar como prova o que não
   deixa ver uma ruga (queimado, tinta do próprio saco, impressão da referência, zonas muito escuras).
5. Comparar com o resto do saco: a prova da ruga face à das retas paralelas, do mesmo comprimento, em todo
   o saco (mediana e dispersão robusta). Uma textura (reflexos, impressão miúda) dá prova a muitas paralelas.
   Comparar só com a zona à volta e pôr de lado as zonas de textura também existem, mas estão desligadas
   (saco_local_mm e textura_coerencia_max a 0): nas fotos de 19/11 custavam rugas reais e quase não ajudavam.
6. Famílias: pela mecânica do saco, uma ruga costuma ter paralelas acima ou abaixo. As rugas já detetadas não
   contam no "resto do saco" das paralelas a elas; à volta de cada ruga forte procuram-se paralelas (±3°) com
   limites mais baixos (relevo.*_familia), a não ser que essas paralelas sejam uma textura (muitas, ou com
   espaçamento regular). As rugas paralelas agrupam-se em famílias (Familia).
Ruga: uma reta com prova >= prova_min_mm e comprimento >= ruga_min_mm (numa família: os limites da família).
"""

from __future__ import annotations

import math
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
    "tinta_fio_mm": 25.0,  # um traço escuro fino mais comprido do que isto não é texto: é vinco (ou impressão, que sai pela referência)
    "tinta_fio_largura_mm": 0.8,  # largura média máxima desse traço (área / comprimento); o texto fica acima de ~0,9
    "impressao_fracao": 0.5,  # tinta em >= isto das referências (onde se veem): impressão
    "impressao_folga_mm": 1.0,
    "faixa_mm": 0.6,  # tolerância lateral à volta da reta
    "falha_mm": 8.0,  # falha visível máxima dentro de uma ruga
    "ruga_min_mm": 40.0,
    "prova_min_mm": 20.0,  # traço visível mínimo ao longo da reta
    "cobertura_min": 0.4,  # da parte visível da reta
    "vizinhas_mm": 2.5,  # retas paralelas a esta distância: a ruga tem de ter pelo menos destaque_mm a mais do que elas
    "borda_mm": 3.0,  # uma reta paralela a um eixo do saco e toda a menos disto da borda da zona inspecionada
    "borda_eixo_graus": 3.0,  # (± isto) é a selagem ou a aresta de um painel impresso, não uma ruga
    "destaque_mm": 14.0,  # traço visível a mais do que as paralelas (as estrias da folha metalizada seguem o vinco)
    "orientacao_graus": 15.0,  # cada reta só conta os pontos de linha com a direção dela (± isto)
    "alinhado_graus": 0.0,  # retas a menos disto dos eixos do saco ficam de fora (0: procuram-se todas)
    "passo_graus": 1.0,
    "busca_px_por_mm": 3.5,  # a procura de retas faz-se numa grelha reduzida até perto disto (fator inteiro)
    "escuro": 50,  # abaixo disto (níveis) uma ruga de ~10 % muda menos de 5 níveis: não se vê, como no queimado
    # Dobras largas e suaves (3–6 mm de largura, 5–15 % de contraste, p.ex. num saco mate): a mesma Hessiana
    # numa imagem reduzida, só com o centro de cada dobra. Juntam-se aos pontos de linha finos.
    "dobra_px_por_mm": 1.75,  # a imagem reduzida (fator inteiro) até perto disto
    "dobra_sigmas_mm": (0.9, 1.4, 2.0),
    "dobra_limiar": 0.04,  # resposta mínima de uma dobra (as dobras reais dão ~0,05–0,15)
    "dobra_max": 0.30,  # acima disto é o contraste da impressão (letras e painéis desfocados), não uma dobra
    "dobra_folga_mm": 1.5,  # à volta da tinta e desse contraste forte não se procuram dobras
    # Comparar com o resto do saco: a prova da ruga face à das retas paralelas, com o mesmo comprimento,
    # em todo o saco. Numa textura (reflexos, impressão miúda) muitas paralelas têm prova parecida.
    # (prova − mediana das paralelas) / dispersão delas (MAD × 1,48). Nas fotos de 19/11 os vincos reais no
    # PreVital (folha metalizada) dão 1,6–20, mediana 4,3; com 2 perde-se 1 foto de rugas em 37 e nenhum OK
    "saco_desvio_min": 2.0,
    "saco_dispersao_min_mm": 2.0,  # a dispersão nunca conta menos do que isto (num saco liso é ~0)
    "saco_afastamento_mm": 4.0,  # as paralelas a menos disto da ruga são ela própria (ou a sua sombra)
    "saco_paralelas_min": 8,  # com menos paralelas à vista não se compara (a ruga fica)
    # Comparar com a zona à volta: o mesmo, só com as paralelas a menos de saco_local_mm. O saco todo engana
    # quando metade é liso (qualquer reta numa zona impressa se destaca); à volta, a impressão também tem prova.
    # 0 = desligada: nas fotos de 19/11 perdia 2 fotos de rugas reais em 73 sem tirar falsas no Dolmio (para a
    # ligar: 15 mm)
    "saco_local_mm": 0.0,
    "saco_local_desvio_min": 2.0,
    # Textura: zonas com muitas linhas em direções misturadas (fotografia, comida, letras claras). Uma ruga não
    # se distingue aí: não se procura, atravessa-se sem contar como prova (como a tinta). Desligada
    # (textura_coerencia_max 0): nas fotos de 19/11 perdia 7 fotos de rugas reais em 73 (vincos do PreVital no
    # meio de reflexos) e as falsas do Dolmio são impressão nítida, não textura (para a ligar: 0,5). A
    # impressão de um saco muito impresso desconta-se com a referência dos sacos OK.
    "textura_janela_mm": 6.0,
    "textura_densidade": 0.08,  # fração de pontos de linha na janela
    "textura_coerencia_max": 0.0,  # 1 = todos na mesma direção (uma ruga, um feixe de dobras); 0 = misturadas
    # "Não é ruga" (Verificar rugas): as linhas que o administrador disse que não são rugas. Cada imagem onde
    # foram marcadas é uma vista (como um saco OK) que só leva essas linhas: em cada saco onde ela alinha, há
    # uma faixa à volta de cada uma (sem votos: basta uma vista), e uma reta achada sobre uma faixa não conta
    "nao_ruga_folga_mm": 2.0,  # metade da largura da faixa (a linha marcada tem ~0,3 mm; o alinhamento erra ~0,5 mm)
    "nao_ruga_prolongar_mm": 10.0,  # a faixa continua isto para lá de cada ponta (noutro saco a linha vê-se mais comprida)
    "nao_ruga_fracao": 0.5,  # a reta não conta com pelo menos isto do comprimento dentro de uma faixa
    # Famílias de rugas paralelas: pela mecânica do saco, uma ruga costuma ter paralelas acima ou abaixo. À volta
    # de cada ruga forte (desvio >= familia_semente_desvio) procuram-se retas no mesmo ângulo (±familia_angulo_graus)
    # em todo o saco, com os mesmos pontos de linha (simetria, tinta, impressão da referência) e limites mais baixos
    "prova_min_mm_familia": 12.0,  # relevo.prova_min_mm_familia
    "ruga_min_mm_familia": 25.0,  # relevo.ruga_min_mm_familia
    "desvio_min_familia": 1.5,  # relevo.desvio_min_familia
    "familia_semente_desvio": 3.0,  # só uma ruga que se destaca isto do saco guia a procura
    "familia_angulo_graus": 3.0,
    "familia_destaque_mm": 8.0,  # o destaque face às paralelas a ±vizinhas_mm (destaque_mm 14 > prova 12: nada passava)
    # Família ou textura: uma família tem poucas linhas (até familia_linhas_max), a >= familia_espacamento_min_mm
    # e com espaçamentos irregulares. Muitas linhas, ou >= 4 com o coeficiente de variação dos espaçamentos abaixo de
    # familia_regularidade_max (periódicas: reflexos, estrias da folha metalizada), é textura: não se junta nenhuma
    "familia_espacamento_min_mm": 5.0,  # mais perto do que isto é a mesma ruga (ou a sombra dela)
    "familia_linhas_max": 6,
    "familia_regularidade_max": 0.2,
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
    fios: np.ndarray | None = None  # bool: traços escuros compridos e finos, tirados da tinta (vinco ou linha impressa)
    dobras: np.ndarray | None = None  # bool: centros das dobras largas (já incluídos em linhas)
    pontos: tuple[np.ndarray, np.ndarray | None] | None = None  # os de interesse (pontos_de), só quando é preciso


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
            k = float(max(2.5, 2.5 * s))  # float do Python: um np.float64 faria os mapas float64 (o remap recusa)
            lado1 = cv2.remap(b, (gx + k * nx).astype(np.float32), (gy + k * ny).astype(np.float32), cv2.INTER_LINEAR)
            lado2 = cv2.remap(b, (gx - k * nx).astype(np.float32), (gy - k * ny).astype(np.float32), cv2.INTER_LINEAR)
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


def _dobras(cinza: np.ndarray, visivel: np.ndarray, tinta: np.ndarray, ppm: float,
            p: dict) -> tuple[np.ndarray, np.ndarray]:
    """(pontos, orientação) das dobras largas, à escala inteira: a Hessiana de _linhas numa imagem reduzida
    (dobra_px_por_mm) com sigmas de dobra, só o centro de cada dobra (o máximo ao longo da normal). Longe
    da tinta e do contraste forte da impressão (resposta > dobra_max): letras e painéis desfocados também
    parecem faixas largas."""
    h, w = cinza.shape
    fator = max(1, int(round(ppm / p["dobra_px_por_mm"])))
    hh, ww = h // fator, w // fator
    vazio = np.zeros((h, w), bool), np.zeros((h, w), np.float32)
    if min(hh, ww) < 8:
        return vazio

    def reduzir(img: np.ndarray) -> np.ndarray:
        return cv2.resize(img[:hh * fator, :ww * fator], (ww, hh), interpolation=cv2.INTER_AREA)

    ppm_r = float(ppm) / fator
    peq = reduzir(cinza.astype(np.float32))
    vis = reduzir(visivel.astype(np.float32)) >= 0.999  # a célula toda à vista
    q = {**p, "sigmas_px": tuple(s * ppm_r for s in p["dobra_sigmas_mm"]), "limiar": p["dobra_limiar"]}
    resp, pontos, ori = _linhas(peq, vis, q)
    k = 2 * int(round(p["dobra_folga_mm"] * ppm_r)) + 1
    perto = cv2.dilate(((resp > p["dobra_max"]) | (reduzir(tinta.astype(np.float32)) > 0)).astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    pontos &= ~perto
    # Só o centro: a resposta não é menor do que a dos vizinhos ao longo da normal
    nx, ny = -np.sin(np.radians(ori)), np.cos(np.radians(ori))
    gy, gx = np.mgrid[0:hh, 0:ww].astype(np.float32)
    for s in (1, -1):
        pontos &= resp >= cv2.remap(resp, (gx + s * nx).astype(np.float32), (gy + s * ny).astype(np.float32),
                                    cv2.INTER_LINEAR)
    cheio = np.zeros((h, w), np.uint8)
    cheio[:hh * fator, :ww * fator] = cv2.resize(pontos.astype(np.uint8), (ww * fator, hh * fator),
                                                interpolation=cv2.INTER_NEAREST)
    orientacao = np.zeros((h, w), np.float32)
    orientacao[:hh * fator, :ww * fator] = cv2.resize(ori, (ww * fator, hh * fator), interpolation=cv2.INTER_NEAREST)
    return (cheio > 0) & visivel, orientacao


def _fios(tinta: np.ndarray, ppm: float, p: dict) -> np.ndarray:
    """Os traços de tinta compridos e finos (>= tinta_fio_mm, largura média <= tinta_fio_largura_mm): um
    vinco forte na folha metalizada é escuro e fino como a tinta, mas o texto nunca faz um traço tão comprido.
    As linhas impressas compridas também saem daqui: descontam-se com a referência dos sacos OK."""
    n, rotulos, caixas, _ = cv2.connectedComponentsWithStats(tinta.astype(np.uint8), connectivity=8)
    comprido = p["tinta_fio_mm"] * ppm
    fio = np.zeros(n, bool)
    for i in np.nonzero(np.hypot(caixas[1:, 2], caixas[1:, 3]) >= comprido)[0] + 1:
        x, y, w, h, area = caixas[i]
        ys, xs = np.nonzero(rotulos[y:y + h, x:x + w] == i)
        _, lados, _ = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
        fio[i] = max(lados) >= comprido and area / max(lados) <= p["tinta_fio_largura_mm"] * ppm
    return fio[rotulos] if fio.any() else np.zeros_like(tinta)


# --- Referência: os sacos OK ensinados (vistas), cada um alinhado com o saco inspecionado -----------

PONTOS_PX_POR_MM = 3.5  # os pontos de interesse (SIFT) procuram-se numa imagem reduzida até perto disto
PONTOS_MAX = 2000  # por imagem
PONTOS_RAZAO = 0.8  # teste da razão (Lowe): a melhor correspondência bem melhor do que a 2.ª
PONTOS_MIN = 15  # correspondências coerentes mínimas (o mesmo produto dá centenas; outro, menos de 10)
PONTOS_ERRO_MM = 0.6  # distância máxima de uma correspondência coerente à afim
ESCALA_TOL = 0.1  # saco e vistas estão no mesmo espaço métrico: a afim não estica nem encolhe mais do que isto
COMUM_MIN_MM2 = 400.0  # parte comum mínima para confirmar o alinhamento pela correlação
VISTAS_MAX = 15  # as vistas que contam em cada saco (as de mais correspondências)
TAMANHO_TOL_MM = 8.0  # sem pontos de interesse, só se alinham pela correlação recortes com este tamanho (± isto)


@dataclass(frozen=True, eq=False)
class Vista:
    """Um saco OK ensinado, pronto a alinhar: onde tem marcas (a impressão dele) e onde se vê. Ou, com
    nao_ruga, uma imagem onde o administrador marcou linhas que não são rugas: as marcas são só essas."""

    nome: str
    forma: tuple[int, int]  # (altura, largura) px
    pequena: np.ndarray  # brilho normalizado, 1/4 (confirmar o alinhamento)
    visivel_pequena: np.ndarray  # bool, 1/4
    marcas: np.ndarray  # np.packbits: tinta, fios e pontos de linha, com impressao_folga_mm (nao_ruga: as faixas)
    visivel: np.ndarray  # np.packbits
    pontos: np.ndarray  # n × 2 float32: px dos pontos de interesse (escala inteira)
    descritores: np.ndarray | None  # n × 128 float32 (SIFT)
    nao_ruga: bool = False  # as marcas são linhas que não são rugas: descontam-se sem votos

    def mascara(self, qual: np.ndarray) -> np.ndarray:
        """marcas ou visivel, desempacotada: uint8 0/1 com a forma da vista."""
        h, w = self.forma
        return np.unpackbits(qual, count=h * w).reshape(h, w)


@dataclass(frozen=True)
class Referencia:
    vistas: tuple[Vista, ...] = ()  # os sacos OK e as imagens com linhas que não são rugas

    @property
    def sacos_ok(self) -> tuple[Vista, ...]:
        return tuple(v for v in self.vistas if not v.nao_ruga)

    @property
    def n(self) -> int:
        """Os sacos OK."""
        return len(self.sacos_ok)

    @property
    def nao_rugas(self) -> int:
        """As imagens com linhas marcadas como "não é ruga"."""
        return len(self.vistas) - self.n


@dataclass(frozen=True)
class Alinhamento:
    vista: Vista
    matriz: np.ndarray  # 2 × 3: px da vista -> px do saco (escala inteira)
    ncc: float  # correlação do brilho normalizado na parte comum
    pontos: int  # correspondências coerentes (0: alinhada pela correlação, sem pontos de interesse)


@dataclass
class Impressao:
    mascara: np.ndarray  # bool: a impressão no saco
    coberto: np.ndarray  # bool: o que alguma vista alinhada vê
    alinhadas: list[Alinhamento]  # sacos OK
    melhor_ncc: float  # a melhor correlação dos sacos OK, mesmo dos que não serviram (nan sem nenhum)
    nao_ruga: np.ndarray | None = None  # bool: as faixas das linhas marcadas como "não é ruga"
    nao_rugas: list[Alinhamento] = field(default_factory=list)  # as imagens delas que alinharam


def _pontos(cinza: np.ndarray, visivel: np.ndarray, ppm: float) -> tuple[np.ndarray, np.ndarray | None]:
    """(posições px n × 2, descritores) SIFT, numa imagem reduzida até perto de PONTOS_PX_POR_MM; as posições
    voltam à escala inteira (centro da célula)."""
    f = max(1, int(round(ppm / PONTOS_PX_POR_MM)))
    g = cv2.resize(cinza, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA) if f > 1 else cinza
    g8 = np.clip(g, 0, 255).astype(np.uint8)
    mascara = cv2.resize(visivel.astype(np.uint8), (g8.shape[1], g8.shape[0]), interpolation=cv2.INTER_NEAREST)
    kp, des = cv2.SIFT_create(nfeatures=PONTOS_MAX).detectAndCompute(g8, mascara)  # um por chamada: threads
    if des is None or not kp:
        return np.zeros((0, 2), np.float32), None
    return np.array([k.pt for k in kp], np.float32) * f + (f - 1) / 2, des


def pontos_de(prep: Preparado) -> tuple[np.ndarray, np.ndarray | None]:
    """Os pontos de interesse do saco: calculados uma vez, ficam no Preparado."""
    if prep.pontos is None:
        prep.pontos = _pontos(prep.cinza, prep.visivel, prep.ppm)
    return prep.pontos


def _mascara_pequena(mascara: np.ndarray, forma: tuple[int, int]) -> np.ndarray:
    return cv2.resize(mascara.astype(np.float32), (forma[1], forma[0]), interpolation=cv2.INTER_AREA) > 0.5


def vista(prep: Preparado, p: dict, nome: str = "") -> Vista:
    """Um saco OK pronto a alinhar: as marcas dele (tinta, fios e pontos de linha), com impressao_folga_mm."""
    k = 2 * int(round(p["impressao_folga_mm"] * prep.ppm)) + 1
    marcas = prep.tinta | prep.linhas if prep.linhas.shape == prep.tinta.shape else prep.tinta.copy()
    if prep.fios is not None:  # uma linha impressa comprida que se repete é impressão, não vinco
        marcas = marcas | prep.fios
    marcas = cv2.dilate(marcas.astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    pontos, descritores = pontos_de(prep)
    return Vista(nome, tuple(prep.cinza.shape[:2]), prep.pequena, _mascara_pequena(prep.visivel, prep.pequena.shape),
                 np.packbits(marcas), np.packbits(prep.visivel), pontos, descritores)


def faixas_nao_ruga(forma: tuple[int, int], segmentos: list[tuple[np.ndarray, np.ndarray]], ppm: float,
                    p: dict) -> np.ndarray:
    """bool: uma faixa à volta de cada segmento (p1, p2 em px), com nao_ruga_folga_mm para cada lado e
    nao_ruga_prolongar_mm para lá de cada ponta."""
    faixas = np.zeros(forma, np.uint8)
    largura = max(1, 2 * int(round(p["nao_ruga_folga_mm"] * ppm)) + 1)
    for p1, p2 in segmentos:
        a, b = np.asarray(p1, float)[:2], np.asarray(p2, float)[:2]
        d = b - a
        n = float(np.hypot(*d))
        if n > 0:
            prolongar = d / n * p["nao_ruga_prolongar_mm"] * ppm
            a, b = a - prolongar, b + prolongar
        cv2.line(faixas, tuple(int(round(v)) for v in a), tuple(int(round(v)) for v in b), 1, largura)
    return faixas > 0


def vista_nao_ruga(prep: Preparado, segmentos: list[tuple[np.ndarray, np.ndarray]], p: dict,
                   nome: str = "") -> Vista:
    """Uma imagem onde o administrador marcou linhas que não são rugas (segmentos p1, p2 em px dela),
    pronta a alinhar como um saco OK: as marcas são só as faixas dessas linhas, não a impressão dela (pode
    ter rugas verdadeiras). Chega um Preparado sem as linhas (preparar com_linhas=False). A vista não
    desconta pontos: em cada saco onde alinha, as retas achadas sobre as faixas não contam (detetar)."""
    marcas = faixas_nao_ruga(prep.cinza.shape[:2], segmentos, prep.ppm, p)
    pontos, descritores = pontos_de(prep)
    return Vista(nome, tuple(prep.cinza.shape[:2]), prep.pequena, _mascara_pequena(prep.visivel, prep.pequena.shape),
                 np.packbits(marcas), np.packbits(prep.visivel), pontos, descritores, nao_ruga=True)


def _3x3(m: np.ndarray) -> np.ndarray:
    return np.vstack([np.asarray(m, float)[:2], [0.0, 0.0, 1.0]])


def _da_escala(f: float) -> np.ndarray:
    """px de uma imagem reduzida f vezes -> px da escala inteira (o centro da célula)."""
    return np.array([[f, 0.0, (f - 1) / 2], [0.0, f, (f - 1) / 2], [0.0, 0.0, 1.0]])


def _por_pontos(v: Vista, alvo: tuple[np.ndarray, np.ndarray | None], ppm: float) -> tuple[np.ndarray | None, int]:
    """(afim vista -> saco, correspondências coerentes) pelos pontos de interesse: RANSAC numa semelhança
    (rodar e deslocar) e depois a afim completa por mínimos quadrados nas coerentes (um saco flexível estica
    um pouco). None se não houver coerentes que cheguem, ou se a afim esticar, encolher ou espelhar."""
    xs, ds = alvo
    if v.descritores is None or ds is None or min(len(v.pontos), len(xs)) < PONTOS_MIN:
        return None, 0
    pares = cv2.BFMatcher(cv2.NORM_L2).knnMatch(v.descritores, ds, k=2)
    bons = [m[0] for m in pares if len(m) == 2 and m[0].distance < PONTOS_RAZAO * m[1].distance]
    if len(bons) < PONTOS_MIN:
        return None, 0
    origem = v.pontos[[m.queryIdx for m in bons]].astype(np.float64)
    destino = xs[[m.trainIdx for m in bons]].astype(np.float64)
    erro = PONTOS_ERRO_MM * ppm
    m, coerentes = cv2.estimateAffinePartial2D(origem, destino, method=cv2.RANSAC, ransacReprojThreshold=erro,
                                               maxIters=5000, confidence=0.999)
    if m is None:
        return None, 0
    coerentes = coerentes.ravel().astype(bool)
    for _ in range(3):
        if coerentes.sum() < PONTOS_MIN:
            return None, int(coerentes.sum())
        x = np.hstack([origem[coerentes], np.ones((int(coerentes.sum()), 1))])
        m = np.linalg.lstsq(x, destino[coerentes], rcond=None)[0].T
        coerentes = np.linalg.norm(origem @ m[:, :2].T + m[:, 2] - destino, axis=1) <= erro
    n = int(coerentes.sum())
    valores = np.linalg.svd(m[:, :2], compute_uv=False)
    if n < PONTOS_MIN or np.linalg.det(m[:, :2]) <= 0 or np.abs(valores - 1).max() > ESCALA_TOL:
        return None, n
    return m, n


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


def _por_correlacao(v: Vista, prep: Preparado) -> np.ndarray | None:
    """Sem pontos de interesse (um saco quase liso): recortes do mesmo tamanho alinham-se pela correlação,
    direto ou rodados 180°, e o ECC na escala 1/4. A afim vista -> saco à escala inteira (None se os
    tamanhos diferirem)."""
    if any(abs(x - y) > TAMANHO_TOL_MM * prep.ppm for x, y in zip(v.forma, prep.cinza.shape[:2])):
        return None
    r180, m, _ = _alinhar(v.pequena, prep.pequena)
    (hv, wv), (ha, wa) = v.pequena.shape, prep.pequena.shape
    sx, sy = wa / wv, ha / hv  # o alvo foi reduzido ao tamanho da vista (cv2.resize, INTER_LINEAR)
    redimensionar = np.array([[sx, 0.0, 0.5 * sx - 0.5], [0.0, sy, 0.5 * sy - 0.5], [0.0, 0.0, 1.0]])
    rodar = np.array([[-1.0, 0.0, wa - 1], [0.0, -1.0, ha - 1], [0.0, 0.0, 1.0]]) if r180 else np.eye(3)
    q = _da_escala(4)
    return (q @ rodar @ redimensionar @ _3x3(m) @ np.linalg.inv(q))[:2]


def _correlacao_comum(v: Vista, prep: Preparado, matriz: np.ndarray, visivel_pequena: np.ndarray) -> float:
    """A correlação do brilho normalizado (1/4) da vista levada para o saco, na parte que ambos veem; -1 se
    essa parte for menor do que COMUM_MIN_MM2."""
    q = _da_escala(4)
    mq = (np.linalg.inv(q) @ _3x3(matriz) @ q)[:2]
    h, w = prep.pequena.shape
    levada = cv2.warpAffine(v.pequena, mq, (w, h), flags=cv2.INTER_LINEAR)
    comum = (cv2.warpAffine(v.visivel_pequena.astype(np.uint8), mq, (w, h), flags=cv2.INTER_NEAREST) > 0) & \
        visivel_pequena
    if comum.sum() < COMUM_MIN_MM2 * (prep.ppm / 4) ** 2:
        return -1.0
    a, b = levada[comum].astype(np.float64), prep.pequena[comum].astype(np.float64)
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / max(math.sqrt(float((a * a).sum() * (b * b).sum())), 1e-12))


def alinhar(v: Vista, prep: Preparado, visivel_pequena: np.ndarray | None = None) -> tuple[Alinhamento | None, float]:
    """A vista no saco: pelos pontos de interesse (qualquer posição e rotação, só uma parte em comum) ou, sem
    eles, pela correlação (recortes do mesmo tamanho). Só serve se o brilho na parte comum se parecer
    (correlação >= ALINHAMENTO_MIN): outro produto, ou a outra face, não serve.

    Devolve (alinhamento ou None, a melhor correlação achada)."""
    if visivel_pequena is None:
        visivel_pequena = _mascara_pequena(prep.visivel, prep.pequena.shape)
    matriz, n = _por_pontos(v, pontos_de(prep), prep.ppm)
    ncc = _correlacao_comum(v, prep, matriz, visivel_pequena) if matriz is not None else -1.0
    if ncc < ALINHAMENTO_MIN and (outra := _por_correlacao(v, prep)) is not None:
        ncc_outra = _correlacao_comum(v, prep, outra, visivel_pequena)
        if ncc_outra > ncc:
            matriz, n, ncc = outra, 0, ncc_outra
    if matriz is None or ncc < ALINHAMENTO_MIN:
        return None, ncc
    return Alinhamento(v, matriz, ncc, n), ncc


def imprimir(ref: Referencia | None, prep: Preparado, p: dict) -> Impressao:
    """A impressão no saco: cada vista alinhada com ele (as VISTAS_MAX de mais correspondências) leva as
    marcas e o que vê para o referencial do saco; é impressão o que está marcado em pelo menos
    impressao_fracao das vistas que veem esse sítio. Votar no referencial do saco deixa usar vistas que só
    têm uma parte em comum com ele (a imagem toda de um saco maior do que o campo, noutra posição).

    As imagens com linhas marcadas como "não é ruga" alinham-se da mesma maneira, mas não votam nem contam
    na cobertura: as faixas de cada uma que alinha vão para nao_ruga (basta uma; detetar tira as retas
    que fiquem sobre elas)."""
    h, w = prep.cinza.shape[:2]
    vazio = np.zeros((h, w), bool)
    if ref is None or not ref.vistas:
        return Impressao(vazio, vazio, [], float("nan"), vazio)
    visivel_pequena = _mascara_pequena(prep.visivel, prep.pequena.shape)
    alinhadas, nao_rugas, melhor = [], [], -1.0
    for v in ref.vistas:
        a, ncc = alinhar(v, prep, visivel_pequena)
        if not v.nao_ruga:
            melhor = max(melhor, ncc)
        if a is not None:
            (nao_rugas if v.nao_ruga else alinhadas).append(a)
    alinhadas = sorted(alinhadas, key=lambda a: (a.pontos, a.ncc), reverse=True)[:VISTAS_MAX]
    votos = np.zeros((h, w), np.uint16)
    vistos = np.zeros((h, w), np.uint16)
    for a in alinhadas:
        for soma, qual in ((votos, a.vista.marcas), (vistos, a.vista.visivel)):
            soma += cv2.warpAffine(a.vista.mascara(qual), a.matriz, (w, h), flags=cv2.INTER_NEAREST)
    mascara = (vistos > 0) & (votos >= p["impressao_fracao"] * np.maximum(vistos, 1))
    nao_ruga = np.zeros((h, w), bool)
    for a in nao_rugas:
        nao_ruga |= cv2.warpAffine(a.vista.mascara(a.vista.marcas), a.matriz, (w, h), flags=cv2.INTER_NEAREST) > 0
    if not ref.n:
        melhor = float("nan")  # só há linhas "não é ruga": nenhum saco OK com que comparar
    return Impressao(mascara, vistos > 0, alinhadas, melhor, nao_ruga, nao_rugas)


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


def retas(pontos: np.ndarray, queimado: np.ndarray, ppm: float, p: dict, orientacao: np.ndarray | None = None,
          angulos: np.ndarray | None = None) -> list[tuple[float, float, float, np.ndarray, np.ndarray]]:
    """Retas (comprimento mm, prova mm, ângulo, p1, p2), da mais provada para a menos: roda até a reta
    ficar vertical e, em cada coluna, procura troços com falhas visíveis <= falha_mm (o queimado
    atravessa-se) e prova suficiente, que se destaquem das retas paralelas ao lado. Uma por ruga (os
    troços quase iguais juntam-se).

    orientacao (graus 0–180, a direção da linha em cada ponto): cada reta só conta os pontos com a
    direção dela (± orientacao_graus); os contornos de letras e desenhos que ela corta não contam.

    A procura faz-se numa grelha reduzida por um fator inteiro (busca_px_por_mm): uma célula tem um
    ponto se tiver algum, e só está queimada se estiver toda. Os extremos voltam à escala inteira.

    angulos: só estes (graus, como o ângulo devolvido); None = todos, de passo_graus em passo_graus.

    Dois troços paralelos são a mesma ruga se o centro de um estiver a menos de saco_afastamento_mm da reta
    do outro (perto ao longo dela) ou se metade dele estiver em cima dela: rugas paralelas mais afastadas ficam
    as duas (uma família)."""
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
    juntas: dict[tuple[int, ...], np.ndarray] = {}  # os pontos de cada conjunto de classes (repete-se de ângulo para ângulo)
    for ang in (np.arange(-90, 90, p["passo_graus"]) if angulos is None else angulos):
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
            classes = tuple(int(c) for c in np.nonzero(perto)[0])
            if classes not in juntas:
                juntas[classes] = np.logical_or.reduce([por_direcao[c] for c in classes]).astype(np.uint8) * 255
            h8 = juntas[classes]
        r8 = cv2.dilate(cv2.warpAffine(h8, m, (D, D), flags=cv2.INTER_NEAREST), faixa)
        cand = np.nonzero(cv2.reduce(r8, 0, cv2.REDUCE_SUM, dtype=cv2.CV_32S).ravel() >= prova_min * 255)[0]
        if not len(cand):
            continue
        r_todas = r8 > 0
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
        # As somas acumuladas só nas colunas que têm troços compridos (numa imagem muito impressa são quase
        # todas: somar a grelha rodada inteira em cada ângulo levava vários segundos)
        usadas, col = np.unique(col, return_inverse=True)
        r_u, q_u = r[:, usadas], q[:, usadas]
        zero = np.zeros((1, len(usadas)), np.int32)
        provado = np.vstack([zero, np.cumsum(r_u & ~q_u, axis=0, dtype=np.int32)])
        visivel = np.vstack([zero, np.cumsum(~q_u, axis=0, dtype=np.int32)])
        prova = provado[y1, col] - provado[y0, col]
        vistos = visivel[y1, col] - visivel[y0, col]
        bons = (prova >= prova_min) & (prova >= p["cobertura_min"] * np.maximum(vistos, 1))
        if not bons.any():
            continue
        col, y0, y1, prova = col[bons], y0[bons], y1[bons], prova[bons]
        coluna = cand[usadas[col]]  # na grelha rodada
        # Uma ruga destaca-se da vizinhança: as retas paralelas a ±vizinhas_mm, nas mesmas linhas, têm de
        # ter pelo menos destaque_mm a menos. Numa textura (reflexos, texto, folha metalizada) qualquer reta
        # junta pontos ao acaso, e as do lado juntam outros tantos. (Só as colunas dessas paralelas.)
        k = max(1, int(round(p["vizinhas_mm"] * ppm)))
        vizinhas = np.clip(coluna[:, None] + np.array([-k, k]), 0, D - 1)
        colunas_v, qual = np.unique(vizinhas.ravel(), return_inverse=True)
        qual = qual.reshape(vizinhas.shape)
        marcadas = r_todas[:, colunas_v] & ~q_todas[:, colunas_v] if ha_queimado else r_todas[:, colunas_v]
        provado_v = np.vstack([np.zeros((1, len(colunas_v)), np.int32), np.cumsum(marcadas, axis=0, dtype=np.int32)])
        fundo = (provado_v[y1[:, None], qual] - provado_v[y0[:, None], qual]).max(axis=1)
        destaca = prova - fundo >= p["destaque_mm"] * ppm
        if not destaca.any():
            continue
        y0, y1, prova, coluna = y0[destaca], y1[destaca], prova[destaca], coluna[destaca]
        inv = cv2.invertAffineTransform(m)
        x = coluna.astype(np.float64)
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
            # Perto: o centro perto do dela e a menos de saco_afastamento_mm da reta dela (uma paralela a 5 mm
            # é outra ruga da mesma família, não a mesma)
            u = p2[g, :2] - p1[g, :2]
            u = u / np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            v = meios[i, :2] - p1[g, :2]
            lateral = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])
            perto = (np.hypot(*(meios[g] - meios[i]).T) <= 8 * ppm) & (lateral <= p["saco_afastamento_mm"] * ppm)
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
    tinta = _tinta(cinza, visivel, ppm, p)
    fios = _fios(tinta, ppm, p)
    dobras = None
    if com_linhas:
        resposta, linhas, orientacao = _linhas(cinza, visivel, p)
        dobras, ori_dobras = _dobras(cinza, visivel, tinta & ~fios, ppm, p)
        orientacao = np.where(dobras & ~linhas, ori_dobras, orientacao)
        linhas = linhas | dobras
    else:
        resposta, linhas, orientacao = np.zeros((1, 1), np.float32), np.zeros((1, 1), bool), None
    return Preparado(cinza, util, visivel, queimado, resposta, linhas, tinta & ~fios, _pequena(cinza), ppm,
                     orientacao, fios, dobras)


def referencia(outros: list[Preparado], p: dict, nomes: list[str] | None = None) -> Referencia:
    """Os sacos OK, cada um pronto a alinhar com o saco inspecionado (vista). A impressão sai, saco a saco,
    das marcas que se repetem nas vistas que alinham com ele (imprimir): cada ruga ou reflexo cai num sítio
    diferente em cada saco; a impressão cai sempre no mesmo."""
    nomes = nomes or [""] * len(outros)
    return Referencia(tuple(vista(o, p, nome) for o, nome in zip(outros, nomes)))


@dataclass
class Achado:
    comprimento_mm: float
    prova_mm: float
    angulo_graus: float  # no recorte (0 = ao longo do eixo maior)
    p1: np.ndarray  # extremos, px do recorte (x, y)
    p2: np.ndarray
    desvio: float = math.inf  # quanto se destaca das paralelas no resto do saco (inf: não comparada)
    desvio_local: float = math.inf  # o mesmo, só com as paralelas à volta (saco_local_mm)
    familia: int = 0  # o número da família de rugas paralelas (0: sozinha)
    da_familia: bool = False  # achada pela procura guiada (limites da família), não pela procura normal


@dataclass
class Familia:
    """Rugas paralelas (±familia_angulo_graus): pela mecânica do saco, uma ruga costuma ter outras ao lado."""

    numero: int
    rugas: list[Achado]
    angulo_graus: float  # a direção média no recorte, entre -90 e 90 (como o ângulo do Defeito)
    espacamentos_mm: list[float]  # entre rugas vizinhas, de um lado ao outro da família

    def texto(self) -> str:
        e = self.espacamentos_mm
        espaco = f"{min(e):.0f} mm" if round(min(e)) == round(max(e)) else f"{min(e):.0f}–{max(e):.0f} mm"
        return f"família de {len(self.rugas)} rugas a {self.angulo_graus:+.0f}° (espaçamento {espaco})"


@dataclass
class Deteccao:
    achados: list[Achado]
    alinhamento: float  # correlação média das vistas usadas (sem nenhuma: a melhor achada; nan sem referência)
    impressao_usada: bool  # alguma vista da referência alinhou com o saco
    imagens: dict[str, np.ndarray] = field(default_factory=dict)
    textura_pct: float = 0.0  # da zona inspecionada, a parte de textura (não se procuram rugas aí)
    vistas: int = 0  # sacos OK da referência usados
    cobertura_pct: float = 0.0  # da zona inspecionada, a parte que eles veem (a impressão só sai daí)
    nao_rugas: int = 0  # imagens com linhas "não é ruga" que alinharam com este saco
    nao_sao: int = 0  # retas achadas que não contaram por estarem sobre uma linha "não é ruga"
    familias: list[Familia] = field(default_factory=list)  # as de 2 ou mais rugas
    familias_textura: int = 0  # procuras de família que deram uma textura (nada se juntou)

    @property
    def prova_mm(self) -> float:
        return max((a.prova_mm for a in self.achados), default=0.0)


# --- Famílias de rugas paralelas ------------------------------------------------------------------

def _direcao(a: Achado) -> float:
    """A direção da reta, em graus de 0 a 180."""
    dx, dy = np.asarray(a.p2, float)[:2] - np.asarray(a.p1, float)[:2]
    return math.degrees(math.atan2(dy, dx)) % 180


def _paralelas(a: Achado, b: Achado, graus: float) -> bool:
    d = abs(_direcao(a) - _direcao(b))
    return min(d, 180 - d) <= graus


def _lateral(a: Achado, b: Achado) -> float:
    """A distância (px, com sinal) do centro de b à reta de a, na normal de a."""
    p1, p2 = np.asarray(a.p1, float)[:2], np.asarray(a.p2, float)[:2]
    u = (p2 - p1) / max(float(np.hypot(*(p2 - p1))), 1e-9)
    meio = (np.asarray(b.p1, float)[:2] + np.asarray(b.p2, float)[:2]) / 2
    return float(np.dot(meio - p1, [-u[1], u[0]]))


def _espacamentos(semente: Achado, linhas: list[Achado], minimo_px: float) -> list[float]:
    """Os espaçamentos (px) entre linhas vizinhas, de um lado ao outro, pelo centro de cada uma na normal da
    semente; as que ficam a menos de minimo_px uma da outra contam como uma."""
    posicoes = sorted(_lateral(semente, a) for a in linhas)
    juntas = [posicoes[0]] if posicoes else []
    for x in posicoes[1:]:
        if x - juntas[-1] >= minimo_px:
            juntas.append(x)
    return list(np.diff(juntas))


def e_textura(semente: Achado, linhas: list[Achado], ppm: float, p: dict) -> bool:
    """As linhas paralelas à semente (ela incluída) são uma textura, não uma família de rugas: mais de
    familia_linhas_max, ou 4 ou mais com espaçamentos regulares (coeficiente de variação abaixo de
    familia_regularidade_max: periódicas, como os reflexos e as estrias da folha metalizada)."""
    espacos = _espacamentos(semente, linhas, p["familia_espacamento_min_mm"] * ppm)
    if len(espacos) + 1 > p["familia_linhas_max"]:
        return True
    return len(espacos) >= 3 and float(np.std(espacos)) < p["familia_regularidade_max"] * float(np.mean(espacos))


def agrupar_familias(achados: list[Achado], ppm: float, p: dict) -> list[Familia]:
    """Junta as rugas paralelas (±familia_angulo_graus, em cadeia) em famílias; numera as de 2 ou mais
    (Achado.familia) pela ruga mais provada de cada uma."""
    grupo = list(range(len(achados)))

    def raiz(i: int) -> int:
        while grupo[i] != i:
            grupo[i] = grupo[grupo[i]]
            i = grupo[i]
        return i

    for i in range(len(achados)):
        for j in range(i + 1, len(achados)):
            if _paralelas(achados[i], achados[j], p["familia_angulo_graus"]):
                grupo[raiz(i)] = raiz(j)
    membros: dict[int, list[Achado]] = {}
    for i, a in enumerate(achados):
        a.familia = 0
        membros.setdefault(raiz(i), []).append(a)
    familias = []
    for rugas in sorted((m for m in membros.values() if len(m) >= 2), key=lambda m: -max(a.prova_mm for a in m)):
        rugas = sorted(rugas, key=lambda a: -a.prova_mm)
        base = rugas[0]
        rugas = sorted(rugas, key=lambda a: _lateral(base, a))
        dobro = np.radians([2 * _direcao(a) for a in rugas])
        angulo = math.degrees(math.atan2(np.sin(dobro).mean(), np.cos(dobro).mean())) / 2
        angulo = (angulo + 90) % 180 - 90
        espacos = [abs(_lateral(base, b) - _lateral(base, a)) / ppm for a, b in zip(rugas, rugas[1:])]
        f = Familia(len(familias) + 1, rugas, angulo, espacos)
        for a in rugas:
            a.familia = f.numero
        familias.append(f)
    return familias


def _sobre(a: Achado, mascara: np.ndarray, n: int = 64) -> float:
    """A fração da reta (n pontos de p1 a p2) que está dentro da máscara."""
    amostras, _ = _a_vista(np.asarray(a.p1, float), np.asarray(a.p2, float), ~mascara, n)
    xs = np.clip(np.round(amostras[:, 0]).astype(int), 0, mascara.shape[1] - 1)
    ys = np.clip(np.round(amostras[:, 1]).astype(int), 0, mascara.shape[0] - 1)
    return float(mascara[ys, xs].mean())


def _na_borda(a: Achado, util: np.ndarray, ppm: float, p: dict) -> bool:
    """A selagem e as arestas dos painéis: retas paralelas a um eixo do saco (o recorte está alinhado com
    ele), encostadas à borda da zona inspecionada em todo o comprimento."""
    dx, dy = np.asarray(a.p2, float)[:2] - np.asarray(a.p1, float)[:2]
    angulo = math.degrees(math.atan2(dy, dx)) % 90
    if min(angulo, 90 - angulo) > p["borda_eixo_graus"]:
        return False
    dentro = np.pad(util, 1).astype(np.uint8)  # fora da imagem também é borda
    distancia = cv2.distanceTransform(dentro, cv2.DIST_L2, 3)[1:-1, 1:-1] / ppm
    t = np.linspace(0.0, 1.0, 64)[:, None]
    pontos = np.asarray(a.p1, float)[:2] + t * np.array([dx, dy])
    x = np.clip(np.round(pontos[:, 0]).astype(int), 0, util.shape[1] - 1)
    y = np.clip(np.round(pontos[:, 1]).astype(int), 0, util.shape[0] - 1)
    return bool(distancia[y, x].max() <= p["borda_mm"])


def textura(linhas: np.ndarray, orientacao: np.ndarray | None, util: np.ndarray, ppm: float, p: dict) -> np.ndarray:
    """As zonas de textura: na janela de textura_janela_mm, mais de textura_densidade de pontos de linha e com
    as direções misturadas (coerência < textura_coerencia_max: o comprimento médio dos vetores do dobro do
    ângulo). Uma ruga, ou um feixe de dobras paralelas, tem a direção dela: coerência alta.
    textura_coerencia_max <= 0: desligada (nenhuma zona)."""
    if orientacao is None or p["textura_coerencia_max"] <= 0:
        return np.zeros_like(linhas)
    k = int(p["textura_janela_mm"] * ppm) | 1
    pontos = linhas.astype(np.float32)
    densidade = cv2.boxFilter(pontos, -1, (k, k))
    dobro = np.radians(orientacao) * 2
    c = cv2.boxFilter(pontos * np.cos(dobro), -1, (k, k))
    s = cv2.boxFilter(pontos * np.sin(dobro), -1, (k, k))
    coerencia = np.hypot(c, s) / np.maximum(densidade, 1e-6)
    return (densidade > p["textura_densidade"]) & (coerencia < p["textura_coerencia_max"]) & util


def desvio_no_saco(a: Achado, pontos: np.ndarray, orientacao: np.ndarray | None, escondido: np.ndarray,
                   util: np.ndarray, ppm: float, p: dict, alcance_mm: float | None = None,
                   outras: list[Achado] | tuple = ()) -> float:
    """Compara a ruga com o resto do mesmo saco: (prova dela − mediana) / dispersão da prova das retas
    paralelas, com o mesmo comprimento, de 1 em 1 mm por todo o saco (só a parte à vista de cada uma).

    Uma ruga é uma diferença entre zonas do saco; numa textura (reflexos da folha metalizada, impressão
    miúda) as paralelas juntam quase tanta prova como ela. Mediana e MAD: algumas rugas paralelas (um
    feixe de dobras) não sobem a referência. inf se houver menos de saco_paralelas_min paralelas à vista.
    alcance_mm: só as paralelas a menos disto (a zona à volta); None = todo o saco.
    outras: rugas já detetadas; as paralelas que passam a menos de saco_afastamento_mm de uma delas (paralela
    a esta, ±2 × familia_angulo_graus) não contam (uma família de rugas não é o "resto do saco")."""
    p1, p2 = np.asarray(a.p1, float)[:2], np.asarray(a.p2, float)[:2]
    d = p2 - p1
    comprimento = float(np.hypot(*d))
    if comprimento < 1:
        return math.inf
    u = d / comprimento
    normal = np.array([-u[1], u[0]])
    marcas = pontos
    if orientacao is not None:
        ang = math.degrees(math.atan2(u[1], u[0])) % 180
        dif = np.abs(orientacao - ang)
        marcas = pontos & (np.minimum(dif, 180 - dif) <= p["orientacao_graus"])
    k = 2 * int(round(p["faixa_mm"] * ppm)) + 1
    marcas = cv2.dilate(marcas.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    h, w = pontos.shape
    alcance = float(np.hypot(h, w))
    desvios = np.arange(-alcance, alcance + 1, ppm)  # de 1 em 1 mm
    desvios = desvios[np.abs(desvios) >= p["saco_afastamento_mm"] * ppm]
    if alcance_mm is not None:
        desvios = desvios[np.abs(desvios) <= alcance_mm * ppm]
    for o in outras:
        if o is not a and _paralelas(a, o, 2 * p["familia_angulo_graus"]):
            desvios = desvios[np.abs(desvios - _lateral(a, o)) >= p["saco_afastamento_mm"] * ppm]
    passo = 0.5 * ppm
    t = np.arange(0, comprimento + 1e-9, passo)
    xy = p1 + t[:, None] * u  # N × 2
    pts = xy[None] + desvios[:, None, None] * normal  # O × N × 2
    x, y = np.round(pts[..., 0]).astype(int), np.round(pts[..., 1]).astype(int)
    dentro = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    vista = dentro & util[y, x] & ~escondido[y, x]
    prova = (vista & marcas[y, x]).sum(1) * passo / ppm
    vistos = vista.sum(1) * passo / ppm
    # A ruga na melhor posição a ±faixa_mm: as retas saem de colunas alargadas por faixa_mm, e entre colunas
    # com a mesma prova fica a primeira (até faixa_mm ao lado do traço: aí a prova medida em cima dela era ~0)
    k = int(round(p["faixa_mm"] * ppm))
    lados = np.arange(-k, k + 1)[:, None, None] * normal  # S × 1 × 2
    em_volta = xy[None] + lados  # S × N × 2
    xs = np.clip(np.round(em_volta[..., 0]).astype(int), 0, w - 1)
    ys = np.clip(np.round(em_volta[..., 1]).astype(int), 0, h - 1)
    provas = (util[ys, xs] & ~escondido[ys, xs] & marcas[ys, xs]).sum(1)
    melhor = int(np.argmax(provas))
    xs, ys = xs[melhor], ys[melhor]
    vista_ruga = util[ys, xs] & ~escondido[ys, xs]
    prova_ruga = float(provas[melhor] * passo / ppm)
    comparaveis = prova[vistos >= 0.5 * max(float(vista_ruga.sum() * passo / ppm), 1.0)]
    if len(comparaveis) < p["saco_paralelas_min"]:
        return math.inf
    mediana = float(np.median(comparaveis))
    dispersao = max(1.4826 * float(np.median(np.abs(comparaveis - mediana))), p["saco_dispersao_min_mm"])
    return (prova_ruga - mediana) / dispersao


def detetar(prep: Preparado, ref: Referencia | None, p: dict, imagens: bool = False) -> Deteccao:
    ppm = prep.ppm
    imp = imprimir(ref, prep, p)
    impressao = imp.mascara
    # As linhas "não é ruga" não mexem nos pontos (tirá-los mudava as paralelas, a densidade e o que se
    # atravessa: aparecia uma reta ao lado da faixa): no fim, a reta que fique sobre uma faixa não conta
    nao_ruga = imp.nao_ruga if imp.nao_ruga is not None else np.zeros_like(impressao)
    usada = bool(imp.alinhadas)
    ncc = float(np.mean([a.ncc for a in imp.alinhadas])) if usada else imp.melhor_ncc
    # A tinta deste saco (texto, desenhos), com folga: um vinco (10–15 % de contraste) não é tinta, e as
    # linhas de texto, vistas de longe, parecem retas
    k = 2 * int(round(p["tinta_folga_mm"] * ppm)) + 1
    tinta = cv2.dilate(prep.tinta.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    zona_textura = textura(prep.linhas, prep.orientacao, prep.util, ppm, p)
    cand = prep.linhas & ~impressao & ~tinta & ~zona_textura
    jan = int(8 * ppm) | 1
    denso = cv2.boxFilter(cand.astype(np.float32), -1, (jan, jan)) > p["densidade_max"]
    soltos = cand & ~denso
    # A impressão, a tinta e o muito escuro (p.ex. um quadrado preto) são como o queimado: uma ruga não se
    # vê aí, por isso atravessa-se sem contar como prova
    escondido = prep.queimado | (prep.util & (impressao | tinta | zona_textura | (prep.cinza < p["escuro"])))
    achados = [Achado(c, pr, ang, p1, p2) for c, pr, ang, p1, p2 in retas(soltos, escondido, ppm, p, prep.orientacao)]
    achados = [a for a in achados if not _na_borda(a, prep.util, ppm, p)]
    nao_sao = [a for a in achados if _sobre(a, nao_ruga) >= p["nao_ruga_fracao"]] if nao_ruga.any() else []
    achados = [a for a in achados if not any(a is b for b in nao_sao)]
    for a in achados:
        a.desvio = desvio_no_saco(a, soltos, prep.orientacao, escondido, prep.util, ppm, p)
        if p["saco_local_mm"] > 0:  # 0: a comparação com a zona à volta está desligada
            a.desvio_local = desvio_no_saco(a, soltos, prep.orientacao, escondido, prep.util, ppm, p,
                                            p["saco_local_mm"])
    destaca = [a.desvio >= p["saco_desvio_min"] and a.desvio_local >= p["saco_local_desvio_min"] for a in achados]
    # Rugas paralelas: as que já se destacaram não contam no "resto do saco" das outras (2.ª passagem)
    fortes = [a for a, d in zip(achados, destaca) if d]
    for i, a in enumerate(achados):
        if any(b is not a and _paralelas(a, b, 2 * p["familia_angulo_graus"]) for b in fortes):
            a.desvio = desvio_no_saco(a, soltos, prep.orientacao, escondido, prep.util, ppm, p, outras=fortes)
            destaca[i] = a.desvio >= p["saco_desvio_min"] and a.desvio_local >= p["saco_local_desvio_min"]
    parecidas = [a for a, d in zip(achados, destaca) if not d]
    achados = [a for a, d in zip(achados, destaca) if d]
    achados, familias_textura, _ = _procurar_familias(achados, parecidas, soltos, escondido, nao_ruga,
                                                                prep, p)
    familias = agrupar_familias(achados, ppm, p)
    # A família toda (várias sementes juntam-se em cadeia) também não pode ser uma textura: se for, saem as
    # rugas que só a procura da família tinha achado
    texturas = [f for f in familias if any(a.da_familia for a in f.rugas)
                and e_textura(max(f.rugas, key=lambda a: a.prova_mm), f.rugas, ppm, p)]
    if texturas:
        fora = [a for f in texturas for a in f.rugas if a.da_familia]
        achados = [a for a in achados if not any(a is b for b in fora)]
        familias_textura += len(texturas)
        familias = agrupar_familias(achados, ppm, p)
    area = max(int(prep.util.sum()), 1)
    textura_pct = 100.0 * float(zona_textura.sum()) / area
    cobertura_pct = 100.0 * float((imp.coberto & prep.util).sum()) / area
    ver = {}
    if imagens:
        cand_img = np.zeros((*cand.shape, 3), np.uint8)
        cand_img[prep.linhas] = (90, 90, 90)
        cand_img[zona_textura] = (80, 0, 80)  # textura: magenta escuro
        for a in parecidas:  # não se destacam do saco ou da zona à volta: a magenta
            cv2.line(cand_img, tuple(int(round(v)) for v in a.p1[:2]), tuple(int(round(v)) for v in a.p2[:2]),
                     (255, 0, 255), max(1, round(ppm * 0.4)))
        cand_img[impressao & prep.util] = (60, 60, 140)
        cand_img[nao_ruga & prep.util] = (40, 120, 40)  # as faixas "não é ruga": verde escuro
        cand_img[soltos] = (0, 255, 255)
        cand_img[cand & denso] = (255, 120, 0)
        for a in nao_sao:  # sobre uma faixa "não é ruga": a verde
            cv2.line(cand_img, tuple(int(round(v)) for v in a.p1[:2]), tuple(int(round(v)) for v in a.p2[:2]),
                     (0, 220, 0), max(1, round(ppm * 0.4)))
        ver = {"resposta": prep.resposta, "linhas": prep.linhas, "impressao": impressao,
               "nao_ruga": nao_ruga, "candidatos": cand_img}
    return Deteccao(achados, ncc, usada, ver, textura_pct, len(imp.alinhadas), cobertura_pct, len(imp.nao_rugas),
                    len(nao_sao), familias, familias_textura)


def _procurar_familias(achados: list[Achado], parecidas: list[Achado], soltos: np.ndarray, escondido: np.ndarray,
                       nao_ruga: np.ndarray, prep: Preparado, p: dict) -> tuple[list[Achado], int, list[Achado]]:
    """A procura guiada: à volta de cada ruga forte (desvio >= familia_semente_desvio), retas no mesmo ângulo
    (±familia_angulo_graus) em todo o saco, com os mesmos pontos (simetria, tinta, impressão) e os limites da
    família (prova_min_mm_familia, ruga_min_mm_familia, familia_destaque_mm). Se as paralelas a essa ruga
    (as achadas assim e as que já havia) forem uma textura (e_textura), não se junta nenhuma; senão junta-se
    cada uma que fique a >= familia_espacamento_min_mm das outras e se destaque do saco desvio_min_familia
    (sem contar as paralelas que são rugas já detetadas). Devolve (achados, procuras que deram textura, juntadas)."""
    ppm = prep.ppm
    q = {**p, "ruga_min_mm": p["ruga_min_mm_familia"], "prova_min_mm": p["prova_min_mm_familia"],
         "destaque_mm": p["familia_destaque_mm"]}
    leque = p["familia_angulo_graus"]
    minimo = p["familia_espacamento_min_mm"] * ppm
    achados = list(achados)
    juntadas: list[Achado] = []
    procurados: list[Achado] = []
    texturas = 0
    for semente in sorted((a for a in achados if a.desvio >= p["familia_semente_desvio"]), key=lambda a: -a.desvio):
        if any(_paralelas(semente, s, leque) for s in procurados):
            continue  # a família desta direção já foi procurada
        procurados.append(semente)
        angulos = (semente.angulo_graus + np.arange(-leque, leque + 1e-9, p["passo_graus"]) + 90) % 180 - 90
        novos = [Achado(c, pr, ang, p1, p2) for c, pr, ang, p1, p2 in
                 retas(soltos, escondido, ppm, q, prep.orientacao, angulos)]
        novos = [n for n in novos if _paralelas(semente, n, leque) and not _na_borda(n, prep.util, ppm, p)
                 and not (nao_ruga.any() and _sobre(n, nao_ruga) >= p["nao_ruga_fracao"])]
        ja = [a for a in achados + parecidas if _paralelas(semente, a, leque)]
        novos = [n for n in novos if all(abs(_lateral(a, n)) >= minimo for a in ja)]
        if e_textura(semente, ja + novos, ppm, p):
            texturas += 1
            continue
        for n in sorted(novos, key=lambda n: -n.prova_mm):
            if len(achados) >= MAX_RUGAS:
                break
            if any(_paralelas(a, n, leque) and abs(_lateral(a, n)) < minimo for a in achados):
                continue
            n.desvio = desvio_no_saco(n, soltos, prep.orientacao, escondido, prep.util, ppm, p, outras=achados)
            if n.desvio >= p["desvio_min_familia"]:
                n.da_familia = True
                achados.append(n)
                juntadas.append(n)
    return achados, texturas, juntadas
