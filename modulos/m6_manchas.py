"""M6 Manchas: "há manchas?".

Uma mancha é uma ÁREA mais clara ou mais escura do que a vizinhança (sujidade,
gordura, tinta). É um fenómeno diferente das cristas do M5 Relevo, que são
LINHAS; por isso tem o seu próprio módulo, com os seus próprios parâmetros.

Para cada defeito ter um só "dono", as regiões alongadas são ignoradas aqui:
são linhas e pertencem ao M5.
"""

from __future__ import annotations

from nucleo.contrato import Escravo, Relatorio, Saco


class Manchas(Escravo):
    """O escravo M6. Usa a secção manchas da configuração."""

    nome = "manchas"
    pergunta = "Há manchas (zonas mais claras ou mais escuras)?"

    def analisar(self, saco: Saco, guardar_intermedios: bool = False) -> Relatorio:
        """Procura manchas na zona útil do saco.

        Recebe:
            saco: o Saco do M2 (só de leitura).
            guardar_intermedios: se True, pôr em intermedios o mapa de
                contraste e a máscara das manchas.

        Devolve:
            Relatorio com um Defeito MANCHA por mancha encontrada (cada uma em
            separado) e as medidas num_manchas e area_total_mm2.

        O que vai fazer (parâmetros em self.cfg.manchas):
            1. contraste = nucleo.imagens.contraste_relativo(
                   extrair_canal(saco.imagem, canal), saco.mascara,
                   sigma_px = mm_para_px(fundo_sigma_mm)).
            2. Suavizar ligeiramente (σ ≈ 0,5 mm) para tirar o ruído.
            3. Limiar conforme a polaridade: claras → contraste ≥ contraste_min;
               escuras → contraste ≤ −contraste_min; ambas → |contraste| ≥ contraste_min.
               Só dentro de saco.mascara_util.
            4. Componentes ligadas; descartar as com área < area_min_mm2 e as
               alongadas (comprimento / largura > alongamento_max), que são
               linhas do M5.
            5. Cada componente → Defeito MANCHA: centro em mm no referencial do
               saco, retângulo de área mínima (comprimento, largura, ângulo),
               área, intensidade = contraste médio, contorno, descrição
               "mancha escura 6.2 mm2".
            6. Relatorio.concluir(self.nome, defeitos, medidas).
        Nota: uma mancha maior do que ~fundo_sigma_mm é em parte "absorvida"
        pela estimativa da iluminação. Para manchas grandes, aumentar
        fundo_sigma_mm ou estimar o fundo com um filtro de mediana.
        """
        raise NotImplementedError
