"""Segmentador SAM 3: o modelo da Meta encontra o saco por uma descrição em texto.

Não depende da cor do fundo (aprendeu o que é "um saco de plástico" em milhões de
imagens). Precisa do pacote oficial `sam3`, de PyTorch com CUDA e dos pesos do Hugging
Face (acesso pedido em huggingface.co/facebook/sam3 e `hf auth login`; ver o README).
Em CPU funciona, mas é muito lento: só para testes.

    1. O modelo carrega-se uma só vez (na primeira imagem) e fica partilhado.
    2. set_image + set_text_prompt(localizacao.sam3_prompt) → máscaras, caixas e confianças.
    3. Fica a maior máscara com confiança ≥ sam3_score_min que não toque na borda; nenhuma
       → SegmentacaoIncerta (ERRO, nunca OK).
    4. Fecho opcional (sam3_fecho_mm, 0 por omissão: o SAM 3 não tem falhas a tapar e o
       fecho esconderia entalhes estreitos), buracos tapados.

O mapa_aresta é a imagem em CIELAB, ligeiramente suavizada: o contorno subpíxel procura
o máximo do gradiente da cor ao longo da normal, como faz com o ΔE.
"""

from __future__ import annotations

import inspect
import logging
from time import perf_counter
from typing import Any

import cv2
import numpy as np

from modulos.m2_localizacao import fundo, segmentacao
from modulos.m2_localizacao.segmentadores import ResultadoSegmentacao, SegmentacaoIncerta, Segmentador
from nucleo.config import Config

log = logging.getLogger("m2.sam3")


class SegmentadorSam3(Segmentador):
    """SAM 3 por texto. Usa localizacao.sam3_* e rejeitar_borda."""

    nome = "sam3"
    _processador: Any = None  # partilhado: o modelo carrega-se uma vez por processo

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def segmentar(self, img: np.ndarray, px_por_mm: float, guardar_intermedios: bool = False) -> ResultadoSegmentacao:
        inicio = perf_counter()
        p = self.cfg.localizacao
        mascaras, scores = mascaras_por_texto(img, p.sam3_prompt)
        escolhida = escolher(mascaras, scores, p.sam3_score_min, p.rejeitar_borda)
        if escolhida is None:
            melhor = f"{max(scores):.2f}" if len(scores) else "nenhuma máscara"
            raise SegmentacaoIncerta(f"segmentação incerta: o SAM 3 não encontrou '{p.sam3_prompt}' com confiança "
                                     f"≥ {p.sam3_score_min:g} sem tocar na borda (melhor: {melhor})")
        mascara, score = escolhida
        saco = limpar(mascara, p.sam3_fecho_mm * px_por_mm)
        lab = fundo.lab(img, p.suavizacao_mm * px_por_mm)
        intermedios = {"segmentacao": saco.astype(np.uint8) * 255} if guardar_intermedios else {}
        return ResultadoSegmentacao(saco, score, self.nome, (perf_counter() - inicio) * 1000, lab,
                                    {"score_sam3": score, "mascaras_sam3": float(len(scores))}, intermedios)


def escolher(mascaras: np.ndarray, scores: np.ndarray, score_min: float,
             rejeitar_borda: bool) -> tuple[np.ndarray, float] | None:
    """(máscara, confiança) da maior máscara com confiança ≥ score_min que (se pedido) não
    toca na borda; None se não houver nenhuma. mascaras: N×H×W bool; scores: N."""
    candidatas = [(int(np.count_nonzero(m)), float(s), m) for m, s in zip(mascaras, scores)
                  if s >= score_min and m.any()
                  and not (rejeitar_borda and segmentacao.toca_na_borda(m.astype(np.uint8)))]
    if not candidatas:
        return None
    _, score, mascara = max(candidatas, key=lambda c: c[0])
    return mascara, score


def limpar(mascara: np.ndarray, fecho_px: float) -> np.ndarray:
    """A maior região, com os buracos tapados e (se fecho_px > 0) um fecho morfológico antes."""
    u8 = mascara.astype(np.uint8) * 255
    if fecho_px > 0:
        k = max(3, 2 * round(fecho_px / 2) + 1)
        u8 = cv2.morphologyEx(u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    regiao = segmentacao.maior_regiao(u8)
    return np.zeros(mascara.shape, bool) if regiao is None else regiao > 0


def mascaras_por_texto(img: np.ndarray, prompt: str) -> tuple[np.ndarray, np.ndarray]:
    """As máscaras (N×H×W bool, à resolução da imagem) e confianças (N) do SAM 3 para o texto.

    Também usada pela ferramenta de anotação. RuntimeError se o SAM 3 não estiver instalado.
    """
    from PIL import Image

    processador = _carregar()
    estado = processador.set_image(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    saida = processador.set_text_prompt(state=estado, prompt=prompt)
    mascaras = _numpy(saida["masks"]).astype(bool)
    scores = _numpy(saida["scores"]).astype(float).reshape(-1)
    mascaras = mascaras.reshape(len(scores), *mascaras.shape[-2:]) if len(scores) else \
        np.zeros((0, *img.shape[:2]), bool)
    if mascaras.shape[1:] != img.shape[:2]:  # por precaução: à resolução da imagem
        mascaras = np.array([cv2.resize(m.astype(np.uint8), img.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
                             for m in mascaras]).reshape(-1, *img.shape[:2])
    return mascaras, scores


def disponivel() -> bool:
    """O SAM 3 está instalado (o pacote; os pesos só se sabe ao carregar)?"""
    try:
        import sam3  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _carregar() -> Any:
    """O Sam3Processor, carregado uma só vez (lazy). RuntimeError com o que falta."""
    if SegmentadorSam3._processador is None:
        try:
            import torch
            from sam3.model.sam3_image_processor import Sam3Processor
            from sam3.model_builder import build_sam3_image_model
        except ImportError as erro:
            raise RuntimeError("o segmentador sam3 precisa do pacote sam3 e do PyTorch (ver o README: "
                               f"'Segmentação com o SAM 3'): {erro}") from erro
        dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        if dispositivo == "cpu":
            log.warning("SAM 3 sem GPU (CUDA): vai ser muito lento; serve só para testes.")
        argumentos = {"device": dispositivo} if "device" in inspect.signature(build_sam3_image_model).parameters else {}
        inicio = perf_counter()
        SegmentadorSam3._processador = Sam3Processor(build_sam3_image_model(**argumentos))
        log.info("SAM 3 carregado em %s (%.1f s)", dispositivo, perf_counter() - inicio)
    return SegmentadorSam3._processador


def _numpy(valor: Any) -> np.ndarray:
    """Tensor do PyTorch (CPU ou GPU) ou array → numpy."""
    if hasattr(valor, "detach"):
        valor = valor.detach().float().cpu().numpy() if valor.dtype.is_floating_point else valor.detach().cpu().numpy()
    return np.asarray(valor)
