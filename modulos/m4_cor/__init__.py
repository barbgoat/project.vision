"""M4 Cor: "o saco tem a cor certa?".

Mede a cor média do saco e compara-a com uma cor de referência, em CIELAB,
onde a distância entre duas cores (ΔE) corresponde à diferença que o olho vê.
Deteta sacos trocados (de outra cor) e sacos descolorados.

Atenção: só é fiável com o balanço de brancos e a exposição fixos (câmara da
linha). Com fotos de telemóvel, que ajustam a cor sozinhas, vai dar falsos
alarmes; nesse caso desligar o módulo (COR_ATIVO = False em nucleo/defines.py).

Ponto de entrada que o Mestre chama: escravo.py. As etapas, quando o
módulo for implementado, ficam cada uma no seu ficheiro (como no M2 e no M5).
"""

from modulos.m4_cor.escravo import Cor

__all__ = ["Cor"]
