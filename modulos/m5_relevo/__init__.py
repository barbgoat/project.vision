"""M5 Relevo: "há riscos, dobras, rugas ou amassos?".

Os quatro defeitos são alterações de relevo que, com iluminação adequada,
aparecem na imagem como cristas (linhas mais claras ou mais escuras do que a
vizinhança). O módulo está partido por etapas, um ficheiro cada:

    escravo.py           o ponto de entrada que o Mestre chama (Relevo.analisar)
    preprocessamento.py  remove as variações lentas de iluminação
    cristas.py           realça as cristas (Hessiana) e extrai as linhas centrais
    zonas.py             zonas com muitas cristas: rugas (paralelas) ou amasso (caóticas)
    linhas.py            cristas isoladas e compridas: risco (fina) ou dobra (larga)

Cada defeito é reportado em separado: um saco com dois riscos e umas rugas dá
três Defeito. As etapas foram pensadas para não fundir defeitos que se tocam
(ver linhas.py e zonas.py).
"""

from modulos.m5_relevo.escravo import Relevo

__all__ = ["Relevo"]
