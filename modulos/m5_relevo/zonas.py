"""M5 Relevo, etapa 3: zonas com muitas cristas, isto é, rugas e amassos."""

from __future__ import annotations

import numpy as np

from modulos.m5_relevo.cristas import MapaCristas
from nucleo.config import Config
from nucleo.contrato import Defeito


def zonas_densas(cristas: MapaCristas, cfg: Config) -> tuple[list[Defeito], np.ndarray]:
    """Encontra as zonas com muitas cristas e classifica-as em rugas ou amasso.

    Cada zona é um Defeito próprio: duas zonas de rugas separadas, ou umas
    rugas encostadas a um amasso, dão defeitos distintos.

    Recebe:
        cristas: saída de mapa_cristas.
        cfg: configuração; usa geral.px_por_mm, relevo.zona_* e
            relevo.rugas_coerencia_min.

    Devolve:
        (defeitos, mascara_zonas): os Defeito do tipo RUGAS ou AMASSO e a
        máscara uint8 0/255 de todas as zonas, que classificar_linhas usa para
        não contar as mesmas cristas outra vez como riscos ou dobras.

    O que vai fazer:
        1. Densidade local de cristas: média de cristas.mascara/255 numa janela
           de zona_janela_mm (cv2.boxFilter).
        2. Coerência local de orientação, em cada píxel (é o tensor de
           estrutura aplicado às orientações das cristas): com θ =
           cristas.orientacao e w = cristas.resposta,
               C = G∗(w·cos 2θ),  S = G∗(w·sin 2θ),  N = G∗w
               coerência = √(C² + S²) / N,   direção dominante = ½·atan2(S, C)
           (G = gaussiana com o tamanho de zona_janela_mm; o ângulo é duplicado
           porque θ e θ+π são a mesma direção). ≈1 → cristas paralelas;
           ≈0 → direções caóticas.
        3. Duas máscaras separadas:
               rugas  = densidade ≥ zona_densidade_min e coerência ≥ rugas_coerencia_min
               amasso = densidade ≥ zona_densidade_min e coerência <  rugas_coerencia_min
           Limpar cada uma com fecho/abertura morfológicos e tirar as suas
           componentes ligadas; cada componente com área ≥ zona_area_min_mm2 é
           uma zona candidata.
        4. Contar as cristas distintas (troços de cristas.centro) em cada
           zona; com menos de zona_linhas_min, não é zona.
        5. Numa zona de RUGAS em que a direção dominante muda muito (duas
           famílias de rugas encostadas, com direções diferentes), partir a
           zona pelas direções (p.ex. histograma da direção dominante com dois
           picos). Melhoria a avaliar com imagens reais.
        6. Para cada zona: retângulo de área mínima → comprimento, largura e
           ângulo (a direção dominante, nas rugas); área da região; centro →
           posição em mm; intensidade = resposta média; descrição.
    """
    raise NotImplementedError
