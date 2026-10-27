"""Testes automáticos (pytest), com imagens sintéticas: python -m pytest

Cada ficheiro testa uma parte do sistema:

    test_arquitetura.py    regras Mestre–Escravos, configuração, perfis, isolamento de falhas
    test_m1_fontes.py      fontes de imagens (pasta, vídeo, caminhos com acentos)
    test_m1_disparo.py     disparo (foto/botão, sensor, virtual) e aquisição em paralelo
    test_m2_localizacao.py medidas, ângulo e recorte do M2 em cenas sintéticas
    test_m3_dimensoes.py   comprimento, largura e forma (falta / sobra de material)
    test_m7_saida.py       registos CSV, desenho e depuração
    test_ponta_a_ponta.py  main.py e ferramentas, corridos como na linha de comandos

As cenas sintéticas (fundo com luz rasante e ruído, sacos de qualquer cor,
rotação, transparência, linha com tapete a andar) estão em sinteticos.py.
"""
