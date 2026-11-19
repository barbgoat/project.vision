"""M3 Dimensões, easy open: os entalhes de abertura fácil na borda do saco e a posição de cada um.

Um entalhe de easy open é um corte pequeno em V na borda (~1–2 mm de fundo, ~1–2 mm de boca), às vezes a
meio de uma aba que sai um pouco da borda. A silhueta do M2 não o guarda (o fecho tapa-o), por isso
procura-se na imagem: ao longo de cada lado, a posição da borda (de fora para dentro, o primeiro ponto
com o brilho do saco); um entalhe é um vale estreito nessa posição (fecho 1D com ENTALHE_LARGURA_MAX_MM
menos a borda). Os cantos ficam de fora (o arco do canto não é um entalhe).

A posição de cada entalhe: o lado, o centro no referencial do saco e a distância à ponta mais próxima,
ao longo do lado. Só se procura nos lados do produto (dimensoes.easy_open_lados: LADOS).

O topo do saco é a ponta com mais entalhes (ponta()). Com ela, ao_topo() mede o par de entalhes dos
lados compridos a partir da aresta real do topo (a reta da geometria: 0,00 mm), com o saco direito (EO em
cima): a distância do esquerdo e do direito, a assimetria (esquerdo − direito) e a esquadria do par (o
ângulo da linha entre os dois face ao topo). Um topo cortado torto não entra na assimetria: mede-se a
partir dele, de cada lado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from nucleo.contrato import Saco

# Os lados onde se procuram os entalhes, por dimensoes.easy_open_lados (os nomes do recorte)
LADOS = {"compridos": ("cima", "baixo"), "pontas": ("esquerda", "direita"),
         "todos": ("cima", "baixo", "esquerda", "direita")}

ENTALHE_PROFUNDIDADE_MIN_MM = 1.0  # um vale da borda mais fundo do que isto é um entalhe (os reais: 1,3–2,2 mm; ruído até ~0,7)
ENTALHE_PROFUNDIDADE_MAX_MM = 3.5  # mais fundo do que isto não é um entalhe de abrir (os reais: 1,3–2,3 mm)
ENTALHE_LARGURA_MAX_MM = 3.0  # mais largo do que isto é uma falta de material (forma), não um entalhe
ENTALHE_LARGURA_MIN_MM = 1.0  # a boca de um entalhe real tem 1,2–2,5 mm; mais estreito é um reflexo ou um vinco
SACO_SEGUIDO_MM = 0.5  # a borda é o ponto mais de fora com isto de saco seguido para dentro (um reflexo
SACO_SEGUIDO_FRACAO = 0.8  # claro no selado escuro não a puxa para dentro)
CANTO_FOLGA_MM = 3.0  # além do raio do canto, a parte do lado junto a cada canto que não se procura
DENTRO_MM, FORA_MM = 4.0, 5.0  # a faixa à volta da borda medida pelo M2 onde se procura a borda verdadeira
SACO_NIVEL_MM = 2.5  # o brilho do saco: a mediana desta faixa por dentro da aresta do M2 (passa a sombra)
CONTRASTE_MIN = 15.0  # níveis entre o saco (junto à borda) e o fundo: abaixo, a borda não se vê bem
NIVEIS_JANELA_MM = 6.0  # os níveis do fundo e do saco: mediana ao longo do lado nesta janela
PASSO_NORMAL_PX = 0.25  # amostragem na normal (subpíxel)

COR_ENTALHE = (0, 165, 255)  # laranja, na imagem intermédia "forma"


@dataclass(frozen=True)
class Entalhe:
    lado: str  # no recorte: "cima", "baixo" (lados compridos), "esquerda", "direita" (pontas)
    x_mm: float  # o fundo do entalhe, no referencial do saco
    y_mm: float
    profundidade_mm: float
    largura_mm: float  # a boca, onde o vale tem mais de metade da profundidade
    distancia_ponta_mm: float  # ao longo do lado, até ao canto mais próximo (à aresta do outro lado)

    @property
    def descricao(self) -> str:
        return (f"entalhe no lado {self.lado}, a {self.distancia_ponta_mm:.1f} mm da ponta "
                f"({self.profundidade_mm:.1f} mm de fundo, {self.largura_mm:.1f} mm de boca)")


def _ao_longo(v: np.ndarray, janela_px: float) -> np.ndarray:
    """Mediana móvel (janela ímpar, as pontas repetidas) de um perfil ao longo de um lado."""
    k = max(3, int(round(janela_px)) | 1)
    estendido = np.pad(v.astype(np.float32), k // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(estendido, k), axis=1).astype(np.float32)


def _fecho_1d(v: np.ndarray, n: int) -> np.ndarray:
    k = np.ones((1, max(3, n | 1)), np.uint8)
    return cv2.morphologyEx(v.astype(np.float32)[None], cv2.MORPH_CLOSE, k, borderType=cv2.BORDER_REPLICATE)[0]


def procurar(saco: Saco, imagem: np.ndarray, ppm: float, raios_mm: tuple[float, float] = (0.0, 0.0),
             lados_eo: str = "todos") -> tuple[list[Entalhe], list[str]]:
    """(entalhes, lados que não se puderam ver por falta de contraste com o fundo).

    imagem: a imagem analisada (a do Saco, no espaço métrico), BGR ou cinzenta.
    raios_mm: os raios dos cantos (ponta esquerda, ponta direita) do recorte, para os pôr de fora.
    lados_eo: uma chave de LADOS: só se procura nesses lados."""
    cinza = imagem if imagem.ndim == 2 else cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    cinza = cv2.GaussianBlur(cinza.astype(np.float32), (0, 0), 0.7)
    inversa = cv2.invertAffineTransform(saco.matriz)
    altura, largura = saco.mascara.shape
    centro = np.array(((largura - 1) / 2, (altura - 1) / 2))
    meio_c, meio_l = saco.comprimento_mm / 2, saco.largura_mm / 2
    raio = max(raios_mm)
    # (nome, direção ao longo do lado, normal para fora, distância do centro à aresta, meio comprimento do lado)
    lados = (("cima", (1, 0), (0, -1), meio_l, meio_c), ("baixo", (1, 0), (0, 1), meio_l, meio_c),
             ("esquerda", (0, 1), (-1, 0), meio_c, meio_l), ("direita", (0, 1), (1, 0), meio_c, meio_l))
    entalhes: list[Entalhe] = []
    sem_contraste: list[str] = []
    for nome, u, n, aresta_mm, meio_mm in lados:
        if nome not in LADOS[lados_eo]:
            continue
        u, n = np.array(u, float), np.array(n, float)
        limite_mm = meio_mm - raio - CANTO_FOLGA_MM
        if limite_mm <= ENTALHE_LARGURA_MAX_MM:
            continue
        t = np.arange(-limite_mm * ppm, limite_mm * ppm + 1)  # px ao longo do lado
        desvio = np.arange(-DENTRO_MM * ppm, FORA_MM * ppm, PASSO_NORMAL_PX)  # px na normal, + para fora
        pontos = (centro + aresta_mm * ppm * n)[None, None] + t[None, :, None] * u + desvio[:, None, None] * n
        x = inversa[0, 0] * pontos[..., 0] + inversa[0, 1] * pontos[..., 1] + inversa[0, 2]
        y = inversa[1, 0] * pontos[..., 0] + inversa[1, 1] * pontos[..., 1] + inversa[1, 2]
        perfil = cv2.remap(cinza, x.astype(np.float32), y.astype(np.float32), cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)  # desvio × t
        # Os níveis do fundo e do saco em cada coluna (a luz e o fundo mudam ao longo do lado), alisados
        # ao longo dele (num entalhe a faixa de dentro também apanha fundo)
        dentro = (desvio >= -SACO_NIVEL_MM * ppm) & (desvio <= -0.4 * ppm)
        fundo = _ao_longo(np.median(perfil[desvio >= (FORA_MM - 1) * ppm], axis=0), NIVEIS_JANELA_MM * ppm)
        nivel_saco = _ao_longo(np.median(perfil[dentro], axis=0), NIVEIS_JANELA_MM * ppm)
        contraste = np.abs(nivel_saco - fundo) >= CONTRASTE_MIN
        if contraste.mean() < 0.5:
            sem_contraste.append(nome)
            continue
        e_saco = ((perfil - (fundo + nivel_saco) / 2) * np.sign(nivel_saco - fundo) > 0) & contraste
        # de fora para dentro: o primeiro ponto com SACO_SEGUIDO_MM de saco para dentro. Uma coluna sem
        # nenhum (uma janela transparente, um reflexo) não tem borda: fica com a das vizinhas (sem isto
        # seria um ombro alto e o lado dela parecia um entalhe)
        k = max(1, int(round(SACO_SEGUIDO_MM * ppm / PASSO_NORMAL_PX)))
        soma = np.vstack([np.zeros((1, e_saco.shape[1])), np.cumsum(e_saco, axis=0)])
        seguido = np.zeros_like(e_saco)
        seguido[k - 1:] = (soma[k:] - soma[:-k]) >= SACO_SEGUIDO_FRACAO * k  # os k pontos até este (para dentro)
        indice = len(desvio) - 1 - np.argmax(seguido[::-1], axis=0)
        borda = desvio[indice] / ppm  # mm para fora da aresta do M2
        achada = seguido.any(axis=0)
        if not achada.any():
            sem_contraste.append(nome)
            continue
        borda = np.interp(np.arange(len(t)), np.nonzero(achada)[0], borda[achada])
        vale = _fecho_1d(borda, int(round(ENTALHE_LARGURA_MAX_MM * ppm))) - borda
        fundo_vale = vale >= ENTALHE_PROFUNDIDADE_MIN_MM / 2
        rotulos = cv2.connectedComponentsWithStats(fundo_vale.astype(np.uint8)[None], connectivity=4)[1][0]
        for r in range(1, int(rotulos.max()) + 1):
            idx = np.nonzero(rotulos == r)[0]
            i = idx[np.argmax(vale[idx])]
            profundidade = float(vale[i])
            boca = float(np.count_nonzero(vale[idx] >= profundidade / 2)) / ppm
            if not (ENTALHE_PROFUNDIDADE_MIN_MM <= profundidade <= ENTALHE_PROFUNDIDADE_MAX_MM
                    and boca >= ENTALHE_LARGURA_MIN_MM):
                continue
            ponto = (centro + aresta_mm * ppm * n + t[i] * u + borda[i] * ppm * n)
            x_mm, y_mm = saco.posicao_mm(*ponto, ppm)
            entalhes.append(Entalhe(nome, x_mm, y_mm, profundidade, boca, meio_mm - abs(t[i]) / ppm))
    return entalhes, sem_contraste


def ponta(entalhes: list[Entalhe]) -> int | None:
    """O topo do saco no recorte: -1 a ponta esquerda, +1 a direita, a que tiver mais entalhes (um falso na
    outra não a muda); None sem entalhes ou com tantos numa como na outra."""
    votos = sum(1 if e.x_mm > 0 else -1 for e in entalhes if e.x_mm != 0)
    return None if votos == 0 else (1 if votos > 0 else -1)


@dataclass(frozen=True)
class AoTopo:
    """O par de entalhes dos lados compridos medido a partir do topo (0,00 mm), com o saco direito."""

    esquerdo_mm: float | None  # do entalhe do lado esquerdo (saco direito, EO em cima) à aresta do topo
    direito_mm: float | None
    entre_mm: float | None  # a distância entre os dois, na largura (para a esquadria)

    @property
    def assimetria_mm(self) -> float | None:
        """esquerdo − direito (0 = simétrico; > 0: o esquerdo está mais abaixo)."""
        if self.esquerdo_mm is None or self.direito_mm is None:
            return None
        return self.esquerdo_mm - self.direito_mm

    @property
    def esquadria_graus(self) -> float | None:
        """O ângulo da linha entre os dois entalhes face ao topo (0 = paralela ao topo)."""
        if self.assimetria_mm is None or not self.entre_mm:
            return None
        return math.degrees(math.atan2(self.assimetria_mm, self.entre_mm))

    @property
    def descricao(self) -> str:
        def mm(v: float | None) -> str:
            return "—" if v is None else f"{v:.2f} mm"
        texto = f"do topo (0,00): esquerdo {mm(self.esquerdo_mm)}, direito {mm(self.direito_mm)}"
        if self.assimetria_mm is not None:
            texto += f"; assimetria {self.assimetria_mm:+.2f} mm ({self.esquadria_graus:+.2f} graus)"
        return texto


def ao_topo(entalhes: list[Entalhe], topo: int, saco: Saco, ppm: float,
            reta_topo: tuple[float, float] | None = None) -> AoTopo | None:
    """O entalhe de cada lado comprido mais perto do topo (a ponta `topo`), medido até à aresta do topo.

    reta_topo: a reta dessa ponta da geometria (u = a·v + b, px do recorte com a origem no centro): mede-se
    até ela, à altura de cada entalhe (um topo torto fica de fora da assimetria). None: até à ponta do
    retângulo medido pelo M2. None se não houver entalhes nos lados compridos, nessa ponta."""
    perto: dict[str, Entalhe] = {}
    for e in entalhes:
        if e.lado in ("cima", "baixo") and e.x_mm * topo > 0:
            if e.lado not in perto or e.x_mm * topo > perto[e.lado].x_mm * topo:
                perto[e.lado] = e
    if not perto:
        return None

    def distancia(e: Entalhe) -> float:
        if reta_topo is None:
            return saco.comprimento_mm / 2 - abs(e.x_mm)
        a, b = reta_topo
        return abs(a * e.y_mm * ppm + b - e.x_mm * ppm) / ppm

    # Saco direito (topo em cima): com o topo à esquerda do recorte (roda 90° no sentido do relógio), o lado
    # de cima do recorte fica à direita; com o topo à direita (roda ao contrário), fica à esquerda
    esquerdo, direito = ("baixo", "cima") if topo < 0 else ("cima", "baixo")
    d = {lado: distancia(e) for lado, e in perto.items()}
    entre = abs(perto["cima"].y_mm - perto["baixo"].y_mm) if len(perto) == 2 else None
    return AoTopo(d.get(esquerdo), d.get(direito), entre)


def desenhar(imagem: np.ndarray, entalhes: list[Entalhe], saco: Saco, ppm: float) -> None:
    """Um círculo laranja em cada entalhe e uma linha entre o par do topo, no recorte (imagem "forma")."""
    altura, largura = saco.mascara.shape
    for e in entalhes:
        u, v = e.x_mm * ppm + (largura - 1) / 2, e.y_mm * ppm + (altura - 1) / 2
        cv2.circle(imagem, (int(round(u)), int(round(v))), max(4, round(2.5 * ppm)), COR_ENTALHE,
                   max(1, round(ppm / 3)), cv2.LINE_AA)
    topo = ponta(entalhes)
    par = [e for e in entalhes if e.lado in ("cima", "baixo") and topo is not None and e.x_mm * topo > 0]
    if {e.lado for e in par} == {"cima", "baixo"}:
        a, b = (min((e for e in par if e.lado == lado), key=lambda e: -e.x_mm * topo) for lado in ("cima", "baixo"))
        pontos = [(int(round(e.x_mm * ppm + (largura - 1) / 2)), int(round(e.y_mm * ppm + (altura - 1) / 2)))
                  for e in (a, b)]
        cv2.line(imagem, *pontos, COR_ENTALHE, 1, cv2.LINE_AA)
