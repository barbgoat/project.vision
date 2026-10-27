"""M1 Aquisição: "dá-me as imagens de cada saco que passa".

Está partido em três partes, um ficheiro cada:

    fontes.py       DE ONDE vêm as imagens: pasta, vídeo, webcam, câmara Blackfly S
    disparo.py      QUANDO começa e acaba a passagem de cada saco (trigger):
                    foto, sensor de presença ou barreira virtual
    em_paralelo.py  capta o saco seguinte enquanto o anterior é analisado

Fases de ensaio com a câmara (BFS-PGE-50S5C-C + lente Basler 8 mm), ainda sem sensor:

    1. Sem tapete, saco parado (bancada, fundo parecido com o tapete).
    2. Em cima do tapete, parado.
    3. Tapete a andar.

    Nas fases 1 e 2 não há movimento: a velocidade do tapete não importa e a
    exposição escolhe-se só pelo brilho.
    Trigger nas fases 1 e 2: um BOTÃO ligado à entrada Line0 da câmara, com o
    disparo "foto" (o valor por omissão): pousar o saco, carregar no botão →
    uma imagem → um resultado. Os saltos do botão são ignorados
    (DISPARO_ANTI_RESSALTO_MS). Os mesmos fios servem depois para o sensor.
    Alternativas sem botão: o disparo "virtual" com DISPARO_DURACAO_MAX_MS
    grande (p.ex. 60000), em que pousar o saco abre a passagem e retirá-lo
    fecha-a; ou o trigger por software (uma tecla) na FonteSpinnaker.
    Na fase 3: medir a velocidade (GERAL_VELOCIDADE_MM_S), ajustar a exposição
    (arrasto ≤ 0,5 px) e os FPS (python -m ferramentas.calcular_montagem); o
    disparo "virtual" serve até haver sensor, e depois o "sensor".

Uso pelo Mestre:

    fonte = criar_fonte("video", cfg, video="linha.mp4")
    with AquisicaoEmParalelo(fonte, criar_disparo(cfg), cfg.disparo.fila_max) as aquisicao:
        for passagem in aquisicao:          # uma passagem = um saco
            resultado = mestre.inspecionar_passagem(passagem)
"""

from modulos.m1_aquisicao.disparo import BarreiraVirtual, Disparo, criar_disparo
from modulos.m1_aquisicao.em_paralelo import AquisicaoEmParalelo
from modulos.m1_aquisicao.fontes import (
    EXTENSOES_IMAGEM,
    FonteImagem,
    FontePasta,
    FonteSpinnaker,
    FonteVideo,
    FonteWebcam,
    criar_fonte,
)

__all__ = [
    "EXTENSOES_IMAGEM", "AquisicaoEmParalelo", "BarreiraVirtual", "Disparo", "FonteImagem", "FontePasta",
    "FonteSpinnaker", "FonteVideo", "FonteWebcam", "criar_disparo", "criar_fonte",
]
