"""M3 Dimensões: "o saco tem o tamanho e a forma certos?".

Usa as medidas que o M2 já fez (comprimento e largura do Saco) e a silhueta do
saco no recorte normalizado. Não volta a medir: as medidas do M2 e do M3
nunca podem discordar.

    1. Comprimento: |medido − nominal| ≤ tolerância?
    2. Largura: idem.
    3. Forma: a silhueta é comparada com o retângulo ideal do tamanho medido.
       Cada zona onde FALTA material (canto dobrado, corte torto, rasgão na
       borda) ou SOBRA (aba, dobra para fora), acima de uma área mínima, é um
       defeito próprio, com a sua posição.

           retângulo ideal          saco com o canto dobrado
           ┌────────────────┐        ╱───────────────┐
           │                │       ╱  ◣ falta        │
           │                │       │                 │
           └────────────────┘       └─────────────────┘

A diferença para o M2: o M2 só verifica se "é um saco" (limites largos); o M3
verifica se é o saco CERTO (tolerâncias apertadas).

Atenção à lente (Basler 8 mm a 300 mm do tapete, grande-angular): o que está
mais alto parece maior. Um saco com 5 mm de altura parece ~1,7 % maior (~2,5 mm
em 150 mm), perto da tolerância de ±3 mm. Para o compensar:
    - calibrar GERAL_PX_POR_MM com a régua à altura do contorno do saco, e não
      pousada no tapete;
    - medir na imagem com o saco centrado (o disparo já escolhe essa: longe do
      centro, a perspetiva aumenta o erro).
A calculadora mostra este erro para outras alturas e lentes
(python -m ferramentas.calcular_montagem --espessura 8). A distorção da lente
(0,07 %) é desprezável.
"""

from __future__ import annotations

import cv2
import numpy as np

from nucleo.contrato import Defeito, Escravo, Relatorio, Saco, TipoDefeito

# Cores da imagem intermédia "forma" (BGR).
_COR_IDEAL = (0, 255, 255)  # retângulo ideal: amarelo
_COR_FALTA = (0, 0, 255)  # falta material: vermelho
_COR_SOBRA = (255, 0, 255)  # material a mais: magenta

# Uma zona está "numa ponta" / "num lado" se o seu centro estiver no último
# quarto do saco nessa direção (só serve para a descrição em texto).
_FRACAO_BORDA = 0.25


class Dimensoes(Escravo):
    """O escravo M3. Usa a secção dimensoes da configuração."""

    nome = "dimensoes"
    pergunta = "O saco tem o tamanho e a forma certos?"

    def analisar(self, saco: Saco, guardar_intermedios: bool = False) -> Relatorio:
        """Compara as medidas e a forma do saco com o nominal.

        Recebe:
            saco: o Saco do M2 (só de leitura), com comprimento_mm, largura_mm e
                a silhueta (mascara) no recorte normalizado.
            guardar_intermedios: se True, devolve a imagem "forma": o recorte
                com o retângulo ideal (amarelo), a falta (vermelho) e a sobra
                de material (azul).

        Devolve:
            Relatorio NOK com um Defeito DIMENSAO por medida fora da tolerância
            (sem contorno: diz respeito ao saco todo) e por cada zona de falta
            ou sobra de material (com contorno e posição); OK se não houver
            nenhum. Medidas: comprimento_mm, largura_mm, desvio_comprimento_mm,
            desvio_largura_mm, area_mm2, retangularidade, falta_mm2, sobra_mm2.
        """
        p = self.cfg.dimensoes
        ppm = self.cfg.geral.px_por_mm
        area_mm2 = cv2.countNonZero(saco.mascara) / ppm**2
        defeitos: list[Defeito] = []

        # 1 e 2. Comprimento e largura
        desvios = {}
        for nome, medido, nominal, tolerancia in (
            ("comprimento", saco.comprimento_mm, p.comprimento_nominal_mm, p.tolerancia_comprimento_mm),
            ("largura", saco.largura_mm, p.largura_nominal_mm, p.tolerancia_largura_mm),
        ):
            desvio = medido - nominal
            desvios[nome] = desvio
            if abs(desvio) > tolerancia:
                defeitos.append(Defeito(
                    TipoDefeito.DIMENSAO,
                    comprimento_mm=saco.comprimento_mm,
                    largura_mm=saco.largura_mm,
                    area_mm2=area_mm2,
                    intensidade=abs(desvio),
                    descricao=(f"{nome} {medido:.1f} mm, fora de {nominal:g} +/- {tolerancia:g} mm "
                               f"({desvio:+.1f} mm)"),
                ))

        # 3. Forma: silhueta vs retângulo ideal do tamanho medido
        silhueta = saco.mascara > 0
        falta = _retangulo_ideal(saco, ppm, -p.forma_folga_mm) & ~silhueta
        sobra = silhueta & ~_retangulo_ideal(saco, ppm, +p.forma_folga_mm)
        faltas = _zonas(falta, "falta material", saco, ppm, p.forma_area_min_mm2)
        sobras = _zonas(sobra, "material a mais", saco, ppm, p.forma_area_min_mm2)
        _reconhecer_cortes_tortos(faltas, sobras, saco)
        defeitos += faltas + sobras

        medidas = {
            "comprimento_mm": saco.comprimento_mm,
            "largura_mm": saco.largura_mm,
            "desvio_comprimento_mm": desvios["comprimento"],
            "desvio_largura_mm": desvios["largura"],
            "area_mm2": area_mm2,
            "retangularidade": area_mm2 / (saco.comprimento_mm * saco.largura_mm),
            "falta_mm2": float(falta.sum()) / ppm**2,
            "sobra_mm2": float(sobra.sum()) / ppm**2,
        }
        intermedios = {"forma": _desenhar_forma(saco, ppm, falta, sobra)} if guardar_intermedios else {}
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios)


def _retangulo_ideal(saco: Saco, ppm: float, margem_mm: float) -> np.ndarray:
    """Máscara booleana do retângulo com as medidas do saco, centrado no recorte e
    alargado (margem > 0) ou encolhido (margem < 0) de margem_mm em cada lado."""
    altura, largura = saco.mascara.shape
    meio_comprimento = saco.comprimento_mm * ppm / 2 + margem_mm * ppm
    meia_largura = saco.largura_mm * ppm / 2 + margem_mm * ppm
    dentro_x = np.abs(np.arange(largura) - (largura - 1) / 2) <= meio_comprimento
    dentro_y = np.abs(np.arange(altura) - (altura - 1) / 2) <= meia_largura
    return dentro_y[:, None] & dentro_x[None, :]


def _zonas(mascara: np.ndarray, o_que: str, saco: Saco, ppm: float, area_min_mm2: float) -> list[Defeito]:
    """Um Defeito por zona ligada da máscara com pelo menos area_min_mm2."""
    n, rotulos, estatisticas, centros = cv2.connectedComponentsWithStats(mascara.astype(np.uint8), connectivity=8)
    defeitos = []
    for i in range(1, n):
        area_mm2 = estatisticas[i, cv2.CC_STAT_AREA] / ppm**2
        if area_mm2 < area_min_mm2:
            continue
        contornos, _ = cv2.findContours((rotulos == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contorno = max(contornos, key=cv2.contourArea)
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
            intensidade=area_mm2,
            contorno=contorno.astype(np.int32),
            descricao=_descrever(o_que, x_mm, y_mm, area_mm2, saco),
        ))
    return defeitos


def _sitio(x_mm: float, y_mm: float, saco: Saco) -> str:
    """ "num canto", "numa ponta" (extremidade do eixo maior) ou "num lado"."""
    na_ponta = abs(x_mm) > saco.comprimento_mm * (0.5 - _FRACAO_BORDA)
    no_lado = abs(y_mm) > saco.largura_mm * (0.5 - _FRACAO_BORDA)
    return "num canto" if na_ponta and no_lado else "numa ponta" if na_ponta else "num lado"


def _descrever(o_que: str, x_mm: float, y_mm: float, area_mm2: float, saco: Saco, causa: str | None = None) -> str:
    """Texto do defeito, p.ex. "falta material num canto (canto dobrado?): 63 mm2 em x=70, y=-22 mm"."""
    sitio = _sitio(x_mm, y_mm, saco)
    if causa is None:
        if o_que == "material a mais":
            causa = "aba ou dobra para fora?"
        else:
            causa = {"num canto": "canto dobrado?", "numa ponta": "corte torto?"}.get(sitio, "rasgão ou corte?")
    return f"{o_que} {sitio} ({causa}): {area_mm2:.0f} mm2 em x={x_mm:.0f}, y={y_mm:.0f} mm"


def _reconhecer_cortes_tortos(faltas: list[Defeito], sobras: list[Defeito], saco: Saco) -> None:
    """Um corte torto deixa, na mesma ponta, falta de material num canto e sobra no canto oposto.

    Quando aparece esse par, a causa provável dos dois passa a "corte torto?".
    """
    for f in faltas:
        for s in sobras:
            na_mesma_ponta = (f.x_mm * s.x_mm > 0
                              and min(abs(f.x_mm), abs(s.x_mm)) > saco.comprimento_mm * (0.5 - _FRACAO_BORDA))
            em_lados_opostos = f.y_mm * s.y_mm < 0
            if na_mesma_ponta and em_lados_opostos:
                for d, o_que in ((f, "falta material"), (s, "material a mais")):
                    d.descricao = _descrever(o_que, d.x_mm, d.y_mm, d.area_mm2, saco, causa="corte torto?")


def _desenhar_forma(saco: Saco, ppm: float, falta: np.ndarray, sobra: np.ndarray) -> np.ndarray:
    """O recorte com o retângulo ideal (amarelo), a falta (vermelho) e a sobra (azul)."""
    imagem = saco.imagem.copy()
    espessura = max(2, round(ppm / 3))
    altura, largura = saco.mascara.shape
    cx, cy = (largura - 1) / 2, (altura - 1) / 2
    mc, ml = saco.comprimento_mm * ppm / 2, saco.largura_mm * ppm / 2
    cv2.rectangle(imagem, (round(cx - mc), round(cy - ml)), (round(cx + mc), round(cy + ml)), _COR_IDEAL, espessura)
    for mascara, cor in ((falta, _COR_FALTA), (sobra, _COR_SOBRA)):
        imagem[mascara] = (0.3 * imagem[mascara] + 0.7 * np.array(cor)).astype(np.uint8)
        contornos, _ = cv2.findContours(mascara.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(imagem, contornos, -1, (0, 0, 0), espessura + 2)  # orla escura: vê-se sobre qualquer cor
        cv2.drawContours(imagem, contornos, -1, cor, espessura)
    return imagem
