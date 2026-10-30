"""M3 Dimensões, forma: a silhueta do saco comparada com o retângulo ideal.

Cada zona onde falta material (canto dobrado, corte torto, rasgão) ou sobra
(aba, dobra para fora) com área OU profundidade acima do mínimo é um Defeito,
com a posição em mm e a causa provável. A profundidade (quanto a zona entra
no saco ou sai dele) apanha entalhes estreitos e fundos, de pouca área.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from nucleo.contrato import Defeito, Saco, TipoDefeito

# Cores da imagem intermédia "forma" (BGR).
_COR_IDEAL = (0, 255, 255)  # retângulo ideal: amarelo
_COR_FALTA = (0, 0, 255)  # falta material: vermelho
_COR_SOBRA = (255, 0, 255)  # material a mais: magenta

# Uma zona está "numa ponta" / "num lado" se o seu centro estiver no último
# quarto do saco nessa direção (só serve para a descrição em texto).
_FRACAO_BORDA = 0.25


def retangulo_ideal(saco: Saco, ppm: float, margem_mm: float, raios_mm: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Máscara booleana do retângulo com as medidas do saco, centrado no recorte, com cantos de
    raio raios_mm[0] na ponta esquerda e raios_mm[1] na direita, e alargado (margem > 0) ou
    encolhido (margem < 0) de margem_mm em cada lado.

    Alargar ou encolher um retângulo arredondado de m dá outro com raio r + m
    (nunca abaixo de 0: encolher um canto vivo deixa-o vivo).
    """
    altura, largura = saco.mascara.shape
    meio_comprimento = saco.comprimento_mm * ppm / 2 + margem_mm * ppm
    meia_largura = saco.largura_mm * ppm / 2 + margem_mm * ppm
    x = np.arange(largura) - (largura - 1) / 2
    y = np.arange(altura) - (altura - 1) / 2
    dentro = (np.abs(y) <= meia_largura)[:, None] & (np.abs(x) <= meio_comprimento)[None, :]
    # Nos cantos só conta o que está dentro do arco; só se calcula no quadrado de cada canto.
    for lado, raio_mm in ((-1, raios_mm[0]), (1, raios_mm[1])):
        raio = min(max(raio_mm + margem_mm, 0.0) * ppm, meio_comprimento, meia_largura)
        if raio <= 0:
            continue
        colunas = np.nonzero((lado * x > meio_comprimento - raio) & (np.abs(x) <= meio_comprimento))[0]
        ux = np.abs(x[colunas]) - (meio_comprimento - raio)
        for sinal in (-1, 1):
            linhas = np.nonzero((sinal * y > meia_largura - raio) & (np.abs(y) <= meia_largura))[0]
            uy = np.abs(y[linhas]) - (meia_largura - raio)
            fora = ux[None, :] ** 2 + uy[:, None] ** 2 > raio * raio
            dentro[np.ix_(linhas, colunas)] &= ~fora
    return dentro


def raios_das_pontas(saco: Saco, ppm: float, raio_mm: float, raio_outra_ponta_mm: float) -> tuple[float, float]:
    """Os raios (ponta esquerda, ponta direita) do recorte.

    Com raios diferentes nas duas pontas, o saco pode chegar virado para
    qualquer lado: fica a atribuição em que a silhueta mais se parece com a
    forma ideal (menos píxeis diferentes).
    """
    if raio_outra_ponta_mm < 0 or raio_outra_ponta_mm == raio_mm:
        return raio_mm, raio_mm
    silhueta = saco.mascara > 0
    opcoes = [(raio_mm, raio_outra_ponta_mm), (raio_outra_ponta_mm, raio_mm)]
    return min(opcoes, key=lambda r: int(np.count_nonzero(retangulo_ideal(saco, ppm, 0.0, r) ^ silhueta)))


def profundidades(ideal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distância (px) de cada píxel à aresta da forma ideal: por dentro (falta) e por fora (sobra)."""
    dentro = cv2.distanceTransform(ideal.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    fora = cv2.distanceTransform((~ideal).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return dentro, fora


def zonas(mascara: np.ndarray, o_que: str, saco: Saco, ppm: float, area_min_mm2: float,
          profundidade_px: np.ndarray, profundidade_min_mm: float) -> list[Defeito]:
    """Um Defeito por zona ligada da máscara com área ≥ area_min_mm2 ou profundidade ≥ profundidade_min_mm.

    profundidade_px: distância de cada píxel à aresta da forma ideal (de profundidades()).
    A profundidade fica em Defeito.intensidade (mm).
    """
    n, rotulos, estatisticas, centros = cv2.connectedComponentsWithStats(mascara.astype(np.uint8), connectivity=8)
    defeitos = []
    for i in range(1, n):
        area_mm2 = estatisticas[i, cv2.CC_STAT_AREA] / ppm**2
        x, y, w, h = (estatisticas[i, k] for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH,
                                                    cv2.CC_STAT_HEIGHT))
        zona = rotulos[y:y + h, x:x + w] == i
        profundidade_mm = float(profundidade_px[y:y + h, x:x + w][zona].max()) / ppm
        if area_mm2 < area_min_mm2 and profundidade_mm < profundidade_min_mm:
            continue
        contornos, _ = cv2.findContours((rotulos == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contorno = max(contornos, key=cv2.contourArea)  # px do recorte; no Defeito fica em mm
        (_, _), (lado1, lado2), angulo = cv2.minAreaRect(contorno)
        if lado1 < lado2:
            angulo += 90
        x_mm, y_mm = saco.posicao_mm(*centros[i], ppm)
        defeitos.append(Defeito(
            TipoDefeito.DIMENSAO,
            x_mm=x_mm,
            y_mm=y_mm,
            comprimento_mm=max(lado1, lado2) / ppm,
            largura_mm=min(lado1, lado2) / ppm,
            area_mm2=area_mm2,
            angulo_graus=angulo,
            intensidade=profundidade_mm,
            severidade=max(area_mm2 / area_min_mm2, profundidade_mm / profundidade_min_mm),
            contorno=saco.px_para_mm(contorno, ppm),
            descricao=_descrever(o_que, x_mm, y_mm, area_mm2, profundidade_mm, saco),
        ))
    return defeitos


def _sitio(x_mm: float, y_mm: float, saco: Saco) -> str:
    """ "num canto", "numa ponta" (extremidade do eixo maior) ou "num lado"."""
    na_ponta = abs(x_mm) > saco.comprimento_mm * (0.5 - _FRACAO_BORDA)
    no_lado = abs(y_mm) > saco.largura_mm * (0.5 - _FRACAO_BORDA)
    return "num canto" if na_ponta and no_lado else "numa ponta" if na_ponta else "num lado"


def _descrever(o_que: str, x_mm: float, y_mm: float, area_mm2: float, profundidade_mm: float, saco: Saco,
               causa: str | None = None) -> str:
    """Texto do defeito, p.ex. "falta material num canto (canto dobrado?): 63 mm2, 7.5 mm de fundo, em x=70, y=-22 mm"."""
    sitio = _sitio(x_mm, y_mm, saco)
    if causa is None:
        if o_que == "material a mais":
            causa = "aba ou dobra para fora?"
        else:
            causa = {"num canto": "canto dobrado?", "numa ponta": "corte torto?"}.get(sitio, "rasgão ou corte?")
    return (f"{o_que} {sitio} ({causa}): {area_mm2:.0f} mm2, {profundidade_mm:.1f} mm de fundo, "
            f"em x={x_mm:.0f}, y={y_mm:.0f} mm")


def reconhecer_cortes_tortos(faltas: list[Defeito], sobras: list[Defeito], saco: Saco
                             ) -> tuple[list[Defeito], list[Defeito], list[tuple[Defeito, Defeito]]]:
    """Um corte torto deixa, na mesma ponta, falta de material num canto e sobra no canto oposto.

    Quando aparece esse par, a causa provável dos dois passa a "corte torto?".
    Devolve (faltas, sobras, pares): as listas com esses defeitos já redescritos (os
    Defeitos são imutáveis) e os pares (falta, sobra) reconhecidos.
    """
    novos: dict[int, Defeito] = {}
    pares_idx = []
    for i, f in enumerate(faltas):
        for j, s in enumerate(sobras):
            na_mesma_ponta = (f.x_mm * s.x_mm > 0
                              and min(abs(f.x_mm), abs(s.x_mm)) > saco.comprimento_mm * (0.5 - _FRACAO_BORDA))
            em_lados_opostos = f.y_mm * s.y_mm < 0
            if na_mesma_ponta and em_lados_opostos:
                for d, o_que in ((f, "falta material"), (s, "material a mais")):
                    novos[id(d)] = replace(d, descricao=_descrever(o_que, d.x_mm, d.y_mm, d.area_mm2, d.intensidade,
                                                                   saco, causa="corte torto?"))
                pares_idx.append((i, j))
    faltas = [novos.get(id(d), d) for d in faltas]
    sobras = [novos.get(id(d), d) for d in sobras]
    return faltas, sobras, [(faltas[i], sobras[j]) for i, j in pares_idx]


def desenhar_forma(saco: Saco, ppm: float, falta: np.ndarray, sobra: np.ndarray,
                   raios_mm: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """O recorte com o retângulo ideal (amarelo), a falta (vermelho) e a sobra (azul)."""
    imagem = saco.imagem.copy()
    espessura = max(2, round(ppm / 3))
    ideal = retangulo_ideal(saco, ppm, 0.0, raios_mm).astype(np.uint8)
    contornos, _ = cv2.findContours(ideal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(imagem, contornos, -1, _COR_IDEAL, espessura)
    for mascara, cor in ((falta, _COR_FALTA), (sobra, _COR_SOBRA)):
        imagem[mascara] = (0.3 * imagem[mascara] + 0.7 * np.array(cor)).astype(np.uint8)
        contornos, _ = cv2.findContours(mascara.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(imagem, contornos, -1, (0, 0, 0), espessura + 2)  # orla escura: vê-se sobre qualquer cor
        cv2.drawContours(imagem, contornos, -1, cor, espessura)
    return imagem
