"""M5 Relevo, etapa 4: cristas isoladas e compridas, isto é, riscos e dobras."""

from __future__ import annotations

import numpy as np

from modulos.m5_relevo.cristas import MapaCristas
from nucleo.config import Config
from nucleo.contrato import Defeito


def classificar_linhas(
    cristas: MapaCristas, cfg: Config, excluir: np.ndarray | None = None
) -> list[Defeito]:
    """Encontra as cristas isoladas e compridas e classifica-as em risco ou dobra.

    Cada linha encontrada é um Defeito próprio: dois riscos que se cruzam, ou
    um risco que toca numa dobra, dão dois defeitos.

    Recebe:
        cristas: saída de mapa_cristas.
        cfg: configuração; usa geral.px_por_mm, relevo.linha_*,
            relevo.risco_largura_max_mm e relevo.dobra_largura_max_mm.
        excluir: máscara uint8 0/255 das zonas densas (de zonas_densas); as
            linhas com a maior parte do comprimento lá dentro já pertencem a
            rugas/amasso.

    Devolve:
        Lista de Defeito do tipo RISCO ou DOBRA (vazia se não houver nenhum).

    O que vai fazer:
        1. Partir as linhas centrais (cristas.centro) em troços: contar os
           vizinhos (conectividade 8) de cada píxel; os pontos com 1 vizinho
           são extremidades e os com 3 ou mais são cruzamentos. Retirando os
           cruzamentos, as componentes ligadas são troços simples, sem ramos.
        2. Para cada troço: lista ordenada de pontos, direção em cada
           extremidade e largura (mediana de cristas.largura_mm ao longo dele).
        3. Religar os troços numa só linha quando a falha entre extremidades é
           ≤ linha_ligar_distancia_mm e as direções diferem ≤
           linha_ligar_angulo_graus. Num cruzamento, cada troço continua no que
           tem a direção mais parecida; assim um "X" dá duas linhas e um risco
           fraco partido volta a ser um só.
        4. Juntar pares de linhas paralelas muito próximas (distância menor do
           que dobra_largura_max_mm e sobrepostas ao longo do comprimento):
           uma dobra costuma aparecer como uma crista clara ao lado de uma
           escura. Ficam uma só DOBRA, com largura = afastamento entre elas.
        5. Medir cada linha: comprimento ao longo da curva (soma das distâncias
           entre pontos), largura, alongamento = comprimento / largura.
        6. Descartar se comprimento < linha_comprimento_min_mm, alongamento <
           linha_alongamento_min, largura > dobra_largura_max_mm, ou se mais de
           metade do comprimento estiver dentro de `excluir`.
        7. Tipo: RISCO se largura ≤ risco_largura_max_mm, senão DOBRA.
        8. Posição (ponto médio da linha, em mm no referencial do saco),
           ângulo (direção principal), área (comprimento × largura),
           intensidade (resposta média), contorno (a linha engrossada pela sua
           largura) e descrição → Defeito.
    """
    raise NotImplementedError
