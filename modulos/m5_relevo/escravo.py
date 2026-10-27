"""M5 Relevo: o ponto de entrada que o Mestre chama.

Liga as etapas do módulo pela ordem certa e mede o tempo de cada uma, para se
saber onde está o problema quando algo falha ou demora: se der erro, a
mensagem diz em que etapa foi; se for lento, as medidas "tempo_<etapa>_ms"
dizem qual.
"""

from __future__ import annotations

from time import perf_counter

from modulos.m5_relevo.cristas import mapa_cristas
from modulos.m5_relevo.linhas import classificar_linhas
from modulos.m5_relevo.preprocessamento import preprocessar
from modulos.m5_relevo.zonas import zonas_densas
from nucleo.contrato import Escravo, Relatorio, Saco


def _ms_desde(inicio: float) -> float:
    return (perf_counter() - inicio) * 1000


class Relevo(Escravo):
    """O escravo M5. Usa a secção relevo da configuração."""

    nome = "relevo"
    pergunta = "Há riscos, dobras, rugas ou amassos?"

    def analisar(self, saco: Saco, guardar_intermedios: bool = False) -> Relatorio:
        """Corre as quatro etapas do módulo sobre o saco.

        Recebe:
            saco: o Saco do M2 (só de leitura).
            guardar_intermedios: se True, devolve em intermedios as imagens de
                cada etapa (pré-processada, resposta, linhas centrais, zonas).

        Devolve:
            Relatorio com um Defeito por risco, dobra, zona de rugas ou amasso,
            ordenados ao longo do saco (x_mm), e as medidas num_linhas,
            num_zonas e tempo_<etapa>_ms.
        """
        tempos: dict[str, float] = {}

        inicio = perf_counter()
        pre = preprocessar(saco.imagem, saco.mascara, self.cfg)
        tempos["preprocessamento"] = _ms_desde(inicio)

        inicio = perf_counter()
        cristas = mapa_cristas(pre, saco.mascara_util, self.cfg)
        tempos["cristas"] = _ms_desde(inicio)

        # As zonas vêm antes das linhas: as cristas que pertencem a umas rugas
        # não devem ser contadas outra vez como riscos.
        inicio = perf_counter()
        zonas, mascara_zonas = zonas_densas(cristas, self.cfg)
        tempos["zonas"] = _ms_desde(inicio)

        inicio = perf_counter()
        linhas = classificar_linhas(cristas, self.cfg, excluir=mascara_zonas)
        tempos["linhas"] = _ms_desde(inicio)

        defeitos = sorted(zonas + linhas, key=lambda d: d.x_mm)
        medidas = {
            "num_linhas": float(len(linhas)),
            "num_zonas": float(len(zonas)),
            **{f"tempo_{etapa}_ms": ms for etapa, ms in tempos.items()},
        }
        intermedios = {}
        if guardar_intermedios:
            intermedios = {
                "preprocessada": pre,
                "resposta": cristas.resposta,
                "linhas_centrais": cristas.centro,
                "zonas": mascara_zonas,
            }
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios)
