"""M5 Relevo, etapa 1: remover as variações lentas de iluminação."""

from __future__ import annotations

import numpy as np

from nucleo.config import Config


def preprocessar(img: np.ndarray, mascara: np.ndarray, cfg: Config) -> np.ndarray:
    """Remove as variações lentas de iluminação do recorte normalizado.

    Recebe:
        img: recorte normalizado do saco, BGR uint8 (Saco.imagem).
        mascara: o saco no recorte, uint8 0/255 (Saco.mascara).
        cfg: configuração; usa geral.px_por_mm, relevo.canal e relevo.fundo_sigma_mm.

    Devolve:
        Imagem float32 do mesmo tamanho com o contraste relativo: ≈0 onde a
        superfície é uniforme, positiva/negativa onde é mais clara/escura do
        que a vizinhança; 0 fora da máscara.

    O que vai fazer:
        1. canal = nucleo.imagens.extrair_canal(img, cfg.relevo.canal).
        2. nucleo.imagens.contraste_relativo(canal, mascara,
           sigma_px = cfg.mm_para_px(cfg.relevo.fundo_sigma_mm)).
        (As duas operações são partilhadas com o M6 Manchas, que as chama com
        os seus próprios parâmetros.)
    """
    raise NotImplementedError
