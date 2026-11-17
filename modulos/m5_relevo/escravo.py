"""M5 Relevo: o ponto de entrada que o Mestre chama.

Por fazer. Até lá devolve Relatorio.por_implementar, que não conta para a decisão
(com geral.exigir_todos, um M5 ativo por fazer dá ERRO ao saco).
"""

from __future__ import annotations

from nucleo.config import Config
from nucleo.contrato import Captura, Escravo, Relatorio, Saco


class Relevo(Escravo):
    """O escravo M5. Usa a secção relevo da configuração."""

    nome = "relevo"
    pergunta = "Há riscos, dobras, rugas ou amassos?"

    def inspecionar(self, saco: Saco, captura: Captura, cfg: Config, guardar_intermedios: bool = False) -> Relatorio:
        """Um Defeito por risco, dobra, zona de rugas ou amasso (por fazer)."""
        return Relatorio.por_implementar(self.nome)
