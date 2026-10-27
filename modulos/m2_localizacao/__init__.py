"""M2 Localização: "onde está o saco? está completo?".

Encontra o saco na imagem, mede-o e devolve-o num recorte normalizado: rodado
para o eixo maior ficar na horizontal, centrado e com uma máscara da zona a
inspecionar (sem a faixa junto à borda). Todos os escravos de inspeção (M3 a
M6) trabalham sobre esse recorte.

O saco pode chegar em qualquer posição da imagem e com qualquer rotação; o M2
desfaz as duas, e os módulos seguintes veem sempre o saco "direito". Funciona
da mesma forma para uma fotografia de uma pasta e para uma imagem da câmara:
o M2 recebe só a imagem e não sabe de onde ela veio (isso é com o M1). O que
muda entre montagens (telemóvel, câmara) é a escala, por isso cada uma tem o
seu px_por_mm.

Como separa o saco do fundo (sacos de qualquer cor, opacos, translúcidos ou
transparentes):
    1. Modelo do fundo: uma foto do fundo vazio (fundo_referencia) ou, sem ela,
       uma superfície suave ajustada à faixa junto à borda da imagem, onde só
       há fundo; a superfície acompanha a luz rasante (um lado mais claro).
    2. Diferença de cor ao fundo, em CIELAB (ΔE): o saco é o que difere, seja
       mais claro, mais escuro ou de outra cor.
    3. Só se a cor não der uma região com forma de saco: juntar as arestas
       fortes (contorno, vincos, reflexos), que apanham os sacos
       transparentes, cujo interior quase não difere do fundo.
    4. Fecho morfológico, interior preenchido, maior região.

É o único módulo de que todos os outros dependem: sem saco localizado, o
Mestre não chama mais ninguém. Quando não encontra saco, levanta SemSaco com
o motivo, que aparece no registo.
Está partido por etapas, um ficheiro cada:

    escravo.py        o ponto de entrada que o Mestre chama (Localizador)
    fundo.py          1. modelo do fundo e diferença de cor ao fundo
    segmentacao.py    2. limiar, arestas, limpeza, maior região, contorno
    medicao.py        3. ângulo, comprimento e largura
    normalizacao.py   4. rodar, recortar, zona útil
"""

from modulos.m2_localizacao.escravo import Localizador

__all__ = ["Localizador"]
