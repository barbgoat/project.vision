"""M3 Dimensões: o ponto de entrada que o Mestre chama (Dimensoes.analisar).

    1 e 2. comprimento e largura face ao nominal ± tolerância (aqui)
    3.     forma: silhueta vs retângulo ideal (forma.py)
"""

from __future__ import annotations

import cv2

from modulos.m3_dimensoes import forma
from nucleo.contrato import Defeito, Escravo, Relatorio, Saco, TipoDefeito


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
        falta = forma.retangulo_ideal(saco, ppm, -p.forma_folga_mm) & ~silhueta
        sobra = silhueta & ~forma.retangulo_ideal(saco, ppm, +p.forma_folga_mm)
        faltas = forma.zonas(falta, "falta material", saco, ppm, p.forma_area_min_mm2)
        sobras = forma.zonas(sobra, "material a mais", saco, ppm, p.forma_area_min_mm2)
        forma.reconhecer_cortes_tortos(faltas, sobras, saco)
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
        intermedios = {"forma": forma.desenhar_forma(saco, ppm, falta, sobra)} if guardar_intermedios else {}
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios)
