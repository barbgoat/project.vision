"""M5 Relevo: "o saco tem rugas?".

Uma ruga (um vinco de lado a lado, ou várias) aparece na imagem como uma linha fina e comprida, mais
escura ou mais clara do que a vizinhança, conforme o ângulo da luz. A impressão também tem linhas:
desconta-se a que se repete nos sacos OK do produto (referencia.py). Só rugas por agora: os riscos,
as dobras largas e os amassos ainda não têm detetor próprio.
"""

from modulos.m5_relevo.escravo import Relevo

__all__ = ["Relevo"]
