"""M1 Aquisição: "dá-me as imagens de cada saco que passa".

Está partido em quatro partes, um ficheiro cada:

    fontes.py            DE ONDE vêm as imagens: a câmara Basler (pypylon); a Blackfly, mais tarde
    preprocessamento.py  da imagem bruta do sensor à imagem BGR (debayer, lente), dentro da fonte
    disparo.py           QUANDO começa e acaba a passagem de cada saco: "ao_vivo" ou "trigger"
    em_paralelo.py       capta o saco seguinte enquanto o anterior é analisado

Os dois modos (disparo.modo):

    ao_vivo   a câmara capta em contínuo e uma barreira virtual na imagem separa os
              sacos. Arrancar com o tapete vazio: as primeiras imagens ensinam o fundo.
              Serve enquanto não houver sensor; com o saco parado, pousá-lo abre a
              passagem e retirá-lo fecha-a (DISPARO_DURACAO_MAX_MS grande).
    trigger   um botão ou um sensor de presença ligado à entrada da câmara
              (DISPARO_TRIGGER_LINHA): cada sinal, uma imagem, um saco.

Uso pelo Mestre:

    fonte = criar_fonte(cfg)
    with AquisicaoEmParalelo(fonte, criar_disparo(cfg), cfg.disparo.fila_max) as aquisicao:
        for passagem in aquisicao:          # uma passagem = um saco
            resultado = mestre.inspecionar_passagem(passagem)
"""

from modulos.m1_aquisicao.disparo import BarreiraVirtual, Disparo, criar_disparo
from modulos.m1_aquisicao.em_paralelo import AquisicaoEmParalelo
from modulos.m1_aquisicao.fontes import FonteBasler, FonteImagem, criar_fonte

__all__ = [
    "AquisicaoEmParalelo", "BarreiraVirtual", "Disparo", "FonteBasler", "FonteImagem", "criar_disparo",
    "criar_fonte",
]
