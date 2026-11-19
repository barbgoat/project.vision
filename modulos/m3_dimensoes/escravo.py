"""M3 Dimensões: o ponto de entrada que o Mestre chama (Dimensoes.inspecionar).

    1 e 2. comprimento e largura face ao nominal ± tolerância (aqui)
    3.     forma: silhueta vs retângulo ideal (forma.py)
    4.     geometria: esquadria das pontas e paralelismo dos lados (geometria.py)
    5.     easy open (só com dimensoes.easy_open, do produto ou da OF): os entalhes de abertura na borda e
           onde estão (easy_open.py); sem nenhum, NOK
"""

from __future__ import annotations

import cv2

from modulos.m3_dimensoes import easy_open, forma, geometria
from nucleo.config import Config
from nucleo.contrato import NOTA_PODE_SER_RELEVO, Captura, Defeito, Escravo, Relatorio, Saco, TipoDefeito


class Dimensoes(Escravo):
    """O escravo M3. Usa a secção dimensoes da configuração. Sem estado entre chamadas."""

    nome = "dimensoes"
    pergunta = "O saco tem o tamanho e a forma certos?"

    def inspecionar(self, saco: Saco, captura: Captura, cfg: Config, guardar_intermedios: bool = False) -> Relatorio:
        """Compara as medidas e a forma do saco com o nominal.

        Recebe:
            saco: o Saco do M2 (só de leitura), com comprimento_mm, largura_mm e
                a silhueta (mascara) no recorte normalizado.
            captura: só com easy_open: a imagem onde se procuram os entalhes (a silhueta do M2 não os
                guarda).
            cfg: usa dimensoes e geral.px_por_mm.
            guardar_intermedios: se True, devolve a imagem "nominal" (o medido, verde, face ao
                nominal do produto, azul, e à banda da tolerância) e a imagem "forma": o recorte
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
            Com easy_open (só nos lados de easy_open_lados): easy_open_entalhes e, se houver, easy_open_distancia_ponta_mm (do entalhe mais
            perto de uma ponta), easy_open_profundidade_mm (o mais fundo) e, por entalhe (1, 2...),
            easy_open_<n>_x_mm, _y_mm e _distancia_ponta_mm; o topo (easy_open_ponta: -1/+1, a ponta com
            mais entalhes) e, a partir da aresta do topo (0,00 mm), com o saco direito: easy_open_esquerdo_mm,
            easy_open_direito_mm, easy_open_assimetria_mm (esquerdo − direito) e easy_open_esquadria_graus. Um saco com EO e sem entalhes: um Defeito
            DIMENSAO (o mesmo se a borda não se via, sem contraste com o fundo: não se pode aprovar).
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
                    descricao=(f"lados compridos não paralelos: {geo.paralelismo_graus:+.1f} graus (saco em trapézio?)"
                               + NOTA_PODE_SER_RELEVO),
                    pode_ser_relevo=True,
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
        # 5. Easy open
        entalhes: list[easy_open.Entalhe] = []
        mensagem = ""
        if p.easy_open:
            entalhes, sem_contraste = easy_open.procurar(saco, captura.imagem, ppm, raios, p.easy_open_lados)
            medidas["easy_open_entalhes"] = float(len(entalhes))
            if entalhes:
                medidas["easy_open_distancia_ponta_mm"] = min(e.distancia_ponta_mm for e in entalhes)
                medidas["easy_open_profundidade_mm"] = max(e.profundidade_mm for e in entalhes)
                for i, e in enumerate(sorted(entalhes, key=lambda e: (e.lado, e.x_mm, e.y_mm)), start=1):
                    medidas.update({f"easy_open_{i}_x_mm": e.x_mm, f"easy_open_{i}_y_mm": e.y_mm,
                                    f"easy_open_{i}_distancia_ponta_mm": e.distancia_ponta_mm})
                mensagem = "easy open: " + "; ".join(e.descricao for e in entalhes)
                topo = easy_open.ponta(entalhes)
                if topo is not None:
                    medidas["easy_open_ponta"] = float(topo)
                    reta = None if geo is None else (geo.esquerda if topo < 0 else geo.direita)
                    par = easy_open.ao_topo(entalhes, topo, saco, ppm, reta)
                    if par is not None:
                        for nome, valor in (("esquerdo_mm", par.esquerdo_mm), ("direito_mm", par.direito_mm),
                                            ("assimetria_mm", par.assimetria_mm),
                                            ("esquadria_graus", par.esquadria_graus)):
                            if valor is not None:
                                medidas[f"easy_open_{nome}"] = valor
                        mensagem = f"easy open {par.descricao} | {mensagem}"
            else:
                nao_vistos = f" (a borda não se viu em: {', '.join(sem_contraste)})" if sem_contraste else ""
                defeitos.append(Defeito(
                    TipoDefeito.DIMENSAO, intensidade=0.0, severidade=1.0,
                    descricao=f"easy open em falta: nenhum entalhe de abertura na borda{nao_vistos}"))
        intermedios = {}
        if guardar_intermedios:
            intermedios["nominal"] = forma.desenhar_nominal(
                saco, ppm, (p.comprimento_nominal_mm, p.largura_nominal_mm),
                (p.tolerancia_comprimento_mm, p.tolerancia_largura_mm), raios)
            intermedios["forma"] = forma.desenhar_forma(saco, ppm, falta, sobra, raios)
            if geo is not None:
                geometria.desenhar(intermedios["forma"], geo, saco, ppm)
            easy_open.desenhar(intermedios["forma"], entalhes, saco, ppm)
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios, mensagem)
