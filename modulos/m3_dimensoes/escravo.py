"""M3 Dimensões: o ponto de entrada que o Mestre chama (Dimensoes.inspecionar).

    1 e 2. comprimento e largura face ao nominal ± tolerância (aqui)
    3.     forma: silhueta vs retângulo ideal (forma.py)
    4.     geometria: esquadria das pontas e paralelismo dos lados (geometria.py)
"""

from __future__ import annotations

import cv2

from modulos.m3_dimensoes import forma, geometria
from nucleo.config import Config
from nucleo.contrato import Captura, Defeito, Escravo, Relatorio, Saco, TipoDefeito


class Dimensoes(Escravo):
    """O escravo M3. Usa a secção dimensoes da configuração. Sem estado entre chamadas."""

    nome = "dimensoes"
    pergunta = "O saco tem o tamanho e a forma certos?"

    def inspecionar(self, saco: Saco, captura: Captura, cfg: Config, guardar_intermedios: bool = False) -> Relatorio:
        """Compara as medidas e a forma do saco com o nominal.

        Recebe:
            saco: o Saco do M2 (só de leitura), com comprimento_mm, largura_mm e
                a silhueta (mascara) no recorte normalizado.
            captura: não usada (o M3 só precisa da silhueta).
            cfg: usa dimensoes e geral.px_por_mm.
            guardar_intermedios: se True, devolve a imagem "forma": o recorte
                com o retângulo ideal (amarelo), a falta (vermelho), a sobra
                de material (magenta) e as retas dos lados com os ângulos (ciano).

        Devolve:
            Relatorio NOK com um Defeito DIMENSAO por medida fora da tolerância
            (sem contorno: diz respeito ao saco todo) e por cada zona de falta
            ou sobra de material (com contorno e posição); OK se não houver
            nenhum. Uma ponta fora de esquadria é um defeito nessa ponta (e
            substitui o par falta/sobra de corte torto da forma, que é o
            mesmo corte); lados não paralelos, um defeito do saco todo.
            Medidas: comprimento_mm, largura_mm, desvio_comprimento_mm,
            desvio_largura_mm, area_mm2, retangularidade, falta_mm2, sobra_mm2,
            forma_profundidade_max_mm, proporcao e, se houver as 4 retas, esquadria_esquerda_graus,
            esquadria_direita_graus, paralelismo_graus, canto_desvio_max_graus.
        """
        p = cfg.dimensoes
        ppm = cfg.geral.px_por_mm
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
                    severidade=abs(desvio) / tolerancia if tolerancia > 0 else float("inf"),
                    descricao=(f"{nome} {medido:.1f} mm, fora de {nominal:g} +/- {tolerancia:g} mm "
                               f"({desvio:+.1f} mm)"),
                ))

        # 3. Forma: silhueta vs retângulo ideal do tamanho medido, com os cantos do produto
        silhueta = saco.mascara > 0
        raios = forma.raios_das_pontas(saco, ppm, p.forma_raio_canto_mm, p.forma_raio_canto_outra_ponta_mm)
        falta = forma.retangulo_ideal(saco, ppm, -p.forma_folga_mm, raios) & ~silhueta
        sobra = silhueta & ~forma.retangulo_ideal(saco, ppm, +p.forma_folga_mm, raios)
        dentro, fora = forma.profundidades(forma.retangulo_ideal(saco, ppm, 0.0, raios))
        faltas = forma.zonas(falta, "falta material", saco, ppm, p.forma_area_min_mm2, dentro,
                             p.forma_profundidade_min_mm)
        sobras = forma.zonas(sobra, "material a mais", saco, ppm, p.forma_area_min_mm2, fora,
                             p.forma_profundidade_min_mm)
        faltas, sobras, cortes_tortos = forma.reconhecer_cortes_tortos(faltas, sobras, saco)

        # 4. Geometria: ângulos das pontas e dos lados (não dependem da escala)
        geo = geometria.medir(saco, ppm)
        pontas_tortas = set()
        if geo is not None:
            for ponta, lado in (("esquerda", -1), ("direita", 1)):
                if abs(geo.esquadria_graus(ponta)) > p.esquadria_max_graus:
                    defeitos.append(geometria.defeito_ponta(geo, ponta, saco, ppm, p.esquadria_max_graus))
                    pontas_tortas.add(lado)
            if abs(geo.paralelismo_graus) > p.paralelismo_max_graus:
                defeitos.append(Defeito(
                    TipoDefeito.DIMENSAO,
                    angulo_graus=geo.paralelismo_graus,
                    intensidade=abs(geo.paralelismo_graus),
                    severidade=abs(geo.paralelismo_graus) / p.paralelismo_max_graus,
                    descricao=f"lados compridos não paralelos: {geo.paralelismo_graus:+.1f} graus (saco em trapézio?)",
                ))
        # O par falta/sobra de um corte torto numa ponta já marcada é o mesmo defeito
        repetidos = {id(d) for par in cortes_tortos if (1 if par[0].x_mm > 0 else -1) in pontas_tortas for d in par}
        defeitos += [d for d in faltas + sobras if id(d) not in repetidos]

        medidas = {
            "comprimento_mm": saco.comprimento_mm,
            "largura_mm": saco.largura_mm,
            "desvio_comprimento_mm": desvios["comprimento"],
            "desvio_largura_mm": desvios["largura"],
            "area_mm2": area_mm2,
            "retangularidade": area_mm2 / (saco.comprimento_mm * saco.largura_mm),
            "falta_mm2": float(falta.sum()) / ppm**2,
            "sobra_mm2": float(sobra.sum()) / ppm**2,
            "forma_profundidade_max_mm": max([float(dentro[falta].max(initial=0)), float(fora[sobra].max(initial=0))]) / ppm,
            "proporcao": saco.comprimento_mm / saco.largura_mm,
        }
        if geo is not None:
            medidas.update(
                esquadria_esquerda_graus=geo.esquadria_graus("esquerda"),
                esquadria_direita_graus=geo.esquadria_graus("direita"),
                paralelismo_graus=geo.paralelismo_graus,
                canto_desvio_max_graus=max(abs(c - 90) for c in geo.cantos_graus()),
            )
        intermedios = {}
        if guardar_intermedios:
            intermedios["forma"] = forma.desenhar_forma(saco, ppm, falta, sobra, raios)
            if geo is not None:
                geometria.desenhar(intermedios["forma"], geo, saco, ppm)
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios)
