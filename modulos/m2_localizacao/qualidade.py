"""M2 Localização, etapa 5: a imagem deixou medir bem? (o controlo da medição)

Antes de entregar as medidas, o M2 vê se pode confiar nelas. Em cada lado do saco (só a parte
central, a que é medida):
    aresta      em quantos pontos foi encontrada a aresta subpíxel (uma sombra, um reflexo ou um
                fundo da cor do saco escondem-na, e aí a medida fica na aresta da máscara)
    reta        quantos pontos se afastam da reta do lado (fundo ou sombra colados, saco dobrado)
    reflexos    quantos pontos têm píxeis saturados ao pé (um reflexo esconde ou desvia a aresta)
e, no saco todo, o resíduo do contorno às retas dos lados; com o ΔE, o contraste do saco ao fundo
e, com a foto do fundo vazio, a luz face à da foto (luz que falhou ou mudou, exposição errada).

Um valor fora do seu limite (localizacao.qualidade_*; 0 = não verificar) dá MedicaoNaoFiavel: o
Mestre dá ERRO "medição não fiável", com o lado e o motivo, e o saco vai à revisão em vez de levar
um OK ou NOK com uma medida em que não se pode confiar. Estes limites não são tolerâncias do saco
(essas são do M3): nunca aprovam um saco, só dizem se a medida é de confiança.
"""

from __future__ import annotations

import numpy as np

from modulos.m2_localizacao import medicao
from nucleo import defines as D
from nucleo.config import ConfigLocalizacao
from nucleo.contrato import Saco, SemConfianca

# Os lados de medicao.LADOS, como aparecem nas mensagens (no recorte endireitado: eixo maior na horizontal)
NOMES_LADOS = {"esquerda": "ponta esquerda", "direita": "ponta direita", "cima": "lado de cima",
               "baixo": "lado de baixo"}


class MedicaoNaoFiavel(SemConfianca):
    """O saco foi encontrado e medido, mas a imagem não deixa confiar na medida: ERRO (revisão).

    Leva o que falhou (problemas, em texto) e o Saco medido, para a afinação mostrar o contorno e o porquê.
    """

    def __init__(self, problemas: list[str], saco: Saco) -> None:
        super().__init__("medição não fiável: " + "; ".join(problemas))
        self.problemas, self.saco = problemas, saco


def sinais(img: np.ndarray, arestas: np.ndarray, normais: np.ndarray, encontrada: np.ndarray,
           centro: np.ndarray, angulo: float, px_por_mm: float) -> dict[str, float]:
    """A aresta encontrada e os reflexos de cada lado, para o diagnóstico e para problemas().

    Recebe a imagem (BGR uint8) e o contorno de segmentacao.afinar_contorno (arestas e normais N×2
    em px; encontrada: se havia mesmo uma aresta em cada ponto), com a pose de medicao.pose.
    Devolve aresta_<lado>_pct e aresta_min_pct (o pior lado), reflexos_<lado>_pct e reflexos_max_pct.
    """
    lados = medicao.por_lado(arestas, centro, angulo)
    _, junto, _ = saturados_ao_pe(img, arestas, normais, max(1, round(D.M2_REFLEXO_DISTANCIA_MM * px_por_mm)))
    medidas: dict[str, float] = {}
    for nome, parte in lados.items():
        n = int(np.count_nonzero(parte))
        medidas[f"aresta_{nome}_pct"] = 100 * np.count_nonzero(encontrada[parte]) / n if n else 0.0
        medidas[f"reflexos_{nome}_pct"] = 100 * np.count_nonzero(junto[parte]) / n if n else 0.0
    medidas["aresta_min_pct"] = min(medidas[f"aresta_{nome}_pct"] for nome in lados)
    medidas["reflexos_max_pct"] = max(medidas[f"reflexos_{nome}_pct"] for nome in lados)
    return medidas


def saturados_ao_pe(img: np.ndarray, pontos: np.ndarray, normais: np.ndarray,
                    raio: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Píxeis saturados (todos os canais ≥ M2_REFLEXO_NIVEL) ao longo da normal de cada ponto, até raio px.

    Devolve três máscaras (N): do lado de dentro do saco (1 a raio px para dentro), de um lado ou do
    outro (−raio a +raio px) e do lado de fora (1 a raio px para fora).
    """
    altura, largura = img.shape[:2]
    passos = np.arange(-raio, raio + 1)
    xs = np.clip(np.round(pontos[:, 0:1] + passos * normais[:, 0:1]).astype(int), 0, largura - 1)
    ys = np.clip(np.round(pontos[:, 1:2] + passos * normais[:, 1:2]).astype(int), 0, altura - 1)
    amostras = img[ys, xs]  # N × (2·raio + 1) [× canais]
    saturado = (amostras if amostras.ndim == 2 else amostras.min(axis=2)) >= D.M2_REFLEXO_NIVEL
    return saturado[:, passos < 0].any(axis=1), saturado.any(axis=1), saturado[:, passos > 0].any(axis=1)


def problemas(medidas: dict[str, float], p: ConfigLocalizacao, px_por_mm: float) -> list[str]:
    """O que está fora dos limites (localizacao.qualidade_*), em texto; [] se a medição é de confiança.

    medidas: as do diagnóstico do M2 (sinais(), medicao.qualidade e as do segmentador). Um limite 0, ou
    um valor que este segmentador não dá (o contraste e a luz só existem com o ΔE), não é verificado.
    """
    lista: list[str] = []

    def por_lado(chave: str, limite: float, fora, texto: str) -> None:
        if not limite:
            return
        for lado in medicao.LADOS:
            valor = medidas.get(f"{chave}_{lado}_pct")
            if valor is not None and fora(valor, limite):
                lista.append(f"{NOMES_LADOS[lado]}: {texto.format(valor=valor, limite=limite)}")

    por_lado("aresta", p.qualidade_aresta_min_pct, lambda v, lim: v < lim,
             "aresta encontrada em só {valor:.0f} % (mínimo {limite:g} %)")
    por_lado("rejeitados", p.qualidade_rejeitados_max_pct, lambda v, lim: v > lim,
             "{valor:.0f} % do contorno longe da reta do lado (máximo {limite:g} %)")
    por_lado("reflexos", p.qualidade_reflexos_max_pct, lambda v, lim: v > lim,
             "reflexos junto a {valor:.0f} % da aresta (máximo {limite:g} %)")
    rms_px = medidas.get("contorno_rms_px")
    if p.qualidade_residuo_max_mm and rms_px is not None and rms_px / px_por_mm > p.qualidade_residuo_max_mm:
        lista.append(f"contorno irregular: resíduo de {rms_px / px_por_mm:.2f} mm às retas dos lados "
                     f"(máximo {p.qualidade_residuo_max_mm:g} mm)")
    contraste = medidas.get("contraste")
    if p.qualidade_contraste_min and contraste is not None and contraste < p.qualidade_contraste_min:
        lista.append(f"o saco quase não difere do fundo: contraste {contraste:.2f} "
                     f"(mínimo {p.qualidade_contraste_min:g})")
    luz = medidas.get("luz_pct")
    if p.qualidade_luz_max_pct and luz is not None and abs(luz) > p.qualidade_luz_max_pct:
        lista.append(f"luz {abs(luz):.0f} % {'abaixo' if luz < 0 else 'acima'} da da foto do fundo vazio "
                     f"(máximo ±{p.qualidade_luz_max_pct:g} %): a luz ou a exposição mudaram")
    return lista
