"""M6 Manchas: "há manchas?".

Uma mancha é uma ÁREA mais clara ou mais escura do que a vizinhança (sujidade,
gordura, tinta). É um fenómeno diferente das cristas do M5 Relevo, que são
LINHAS; por isso tem o seu próprio módulo, com os seus próprios parâmetros.

Para cada defeito ter um só "dono", as regiões alongadas são ignoradas aqui:
são linhas e pertencem ao M5.

Ponto de entrada que o Mestre chama: escravo.py. As etapas, quando o
módulo for implementado, ficam cada uma no seu ficheiro (como no M2 e no M5).
"""

from modulos.m6_manchas.escravo import Manchas

__all__ = ["Manchas"]
