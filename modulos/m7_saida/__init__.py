"""M7 Saída: "como mostrar e registar o resultado?".

Está partido em três partes, um ficheiro cada:

    registo.py     os CSV (registo.csv, defeitos.csv) e as imagens anotadas de cada execução
    desenho.py     a imagem anotada de um saco: contorno, defeitos numerados, estado de cada módulo
    depuracao.py   para cada saco depurado, a imagem de cada etapa de cada módulo, um painel
                   com tudo e o resultado em JSON: para perceber onde está o erro

Uso pelo Mestre:

    with Saida(cfg, "resultados", mestre.nomes, depurar="nok") as saida:
        saida.registar(resultado)              # CSV + imagem anotada + depuração
"""

from modulos.m7_saida.depuracao import Depuracao, nome_curto
from modulos.m7_saida.desenho import desenhar
from modulos.m7_saida.registo import COLUNAS_DEFEITOS, Saida

__all__ = ["COLUNAS_DEFEITOS", "Depuracao", "Saida", "desenhar", "nome_curto"]
