"""Segmentador ΔE: o saco é o que difere da cor do fundo (CIELAB). CPU, rápido, sem treino.

    1. Modelo do fundo: uma foto do fundo vazio (fundo_referencia) ou uma superfície suave
       ajustada à faixa junto à borda da imagem, onde só há fundo.
    2. Diferença de cor ao fundo (ΔE); limiar fixo ou de Otsu (com um mínimo).
    3. Só se a cor não der uma região com forma de saco: juntar as arestas fortes
       (sacos transparentes).
    4. Fecho morfológico, interior preenchido, maior região.

Trabalha numa versão reduzida (localizacao.resolucao_px_por_mm). O mapa de diferenças
é o mapa_aresta: o contorno subpíxel procura onde ele cai mais depressa.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from modulos.m2_localizacao import fundo, segmentacao
from modulos.m2_localizacao.segmentadores import ResultadoSegmentacao, Segmentador
from nucleo import defines as D
from nucleo.config import RAIZ, Config
from nucleo.contrato import SemSaco
from nucleo.imagens import ler_imagem, recortar


class SegmentadorDeltaE(Segmentador):
    """Diferença de cor ao fundo. Usa a secção localizacao da configuração."""

    nome = "delta_e"

    def __init__(self, cfg: Config) -> None:
        """Se houver fundo_referencia, lê-a já (erro claro logo no arranque). É a imagem inteira da
        câmara (como a guarda o Fundo vazio da BIapp): corta-se com a ROI, como as imagens."""
        self.cfg = cfg
        self._referencia: np.ndarray | None = None
        self._referencia_lab: dict[tuple[int, int], np.ndarray] = {}  # por tamanho da imagem reduzida
        self._referencia_luz: dict[tuple[int, int], float] = {}  # cinzento mediano da faixa da borda, idem
        self._sigma_px = 1.0
        caminho = cfg.localizacao.fundo_referencia
        if caminho:
            caminho_abs = Path(caminho) if Path(caminho).is_absolute() else RAIZ / caminho
            if not caminho_abs.is_file():
                raise FileNotFoundError(f"localizacao.fundo_referencia não encontrada: {caminho_abs}")
            referencia = ler_imagem(caminho_abs)
            if referencia is None:
                raise ValueError(f"localizacao.fundo_referencia não é uma imagem válida: {caminho_abs}")
            roi = cfg.aquisicao.roi()
            if roi is not None and referencia.shape[0] * referencia.shape[1] > roi[2] * roi[3]:
                referencia = recortar(referencia, roi)  # a imagem inteira; uma já cortada fica como está
            self._referencia = referencia

    def segmentar(self, img: np.ndarray, px_por_mm: float, guardar_intermedios: bool = False) -> ResultadoSegmentacao:
        inicio = perf_counter()
        p = self.cfg.localizacao
        escala = min(1.0, p.resolucao_px_por_mm / px_por_mm)
        pequena = img if escala == 1.0 else cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
        px_mm = px_por_mm * pequena.shape[1] / img.shape[1]  # px/mm na imagem reduzida
        self._sigma_px = p.suavizacao_mm * px_mm
        lab = fundo.lab(pequena, self._sigma_px)
        faixa = fundo.faixa_da_borda(lab.shape[:2], max(2, round(p.faixa_fundo_mm * px_mm)))
        modelo = self._fundo(lab, faixa, img.shape[:2])
        if self._referencia is None:
            diferenca = fundo.diferenca(lab, modelo, p.peso_luminosidade)
            limiar = p.limiar_delta_e or max(segmentacao.otsu(diferenca), p.delta_e_min)
            mascara = (diferenca > limiar).astype(np.uint8) * 255
        else:
            # A luz muda um pouco entre a foto do fundo e esta (exposição, luz do dia): tira-se o desvio
            # mediano da faixa da borda, onde só há fundo. Depois, o limiar sai do ruído do fundo.
            modelo = modelo + np.median((lab - modelo)[faixa], axis=0)
            diferenca = fundo.diferenca(lab, modelo, p.peso_luminosidade)
            suave = cv2.GaussianBlur(diferenca, (0, 0), D.M2_REFERENCIA_SUAVIZACAO_PX)
            mediana = float(np.median(suave[faixa]))
            ruido = 1.4826 * float(np.median(np.abs(suave[faixa] - mediana)))
            limiar = p.limiar_delta_e or max(mediana + D.M2_REFERENCIA_DESVIOS * ruido, D.M2_REFERENCIA_LIMIAR_MIN)
            mascara = (suave > limiar).astype(np.uint8) * 255
        fecho_px, abertura_px = p.fecho_mm * px_mm, p.abertura_mm * px_mm
        regiao = segmentacao.maior_regiao(segmentacao.limpar(mascara, fecho_px, abertura_px))
        # As arestas só entram se a cor não chegar (sacos transparentes): somá-las
        # sempre "engorda" o contorno e estraga as medidas dos sacos opacos.
        usou_arestas = False
        if p.usar_arestas and not self._parece_saco(regiao, px_mm):
            arestas = segmentacao.arestas(lab, faixa, p.arestas_fator).astype(np.uint8) * 255
            com_arestas = segmentacao.maior_regiao(segmentacao.limpar(mascara | arestas, fecho_px, abertura_px))
            if com_arestas is not None:
                regiao, usou_arestas = com_arestas, True
        medidas = {"limiar_delta_e": limiar, "diferenca_max": float(diferenca.max()),
                   "usou_arestas": float(usou_arestas), "escala": escala}
        if self._referencia is not None:
            # Luz do fundo face à da foto do fundo vazio (cinzento mediano da faixa da borda, só fundo), em %.
            # O desvio tirado acima chega para mudanças pequenas; uma grande é a luz ou a exposição que mudaram.
            cinzento = cv2.cvtColor(pequena, cv2.COLOR_BGR2GRAY)
            luz_foto = self._referencia_luz[lab.shape[:2]]
            medidas["luz_pct"] = 100 * (float(np.median(cinzento[faixa])) / max(luz_foto, 1.0) - 1)
        intermedios = {"diferenca": diferenca, "segmentacao": regiao if regiao is not None else mascara} \
            if guardar_intermedios else {}
        if regiao is None:
            erro = SemSaco(f"nada difere do fundo (diferença máxima ΔE {diferenca.max():.1f}, limiar {limiar:.1f})")
            erro.medidas, erro.intermedios = medidas, intermedios  # para o diagnóstico (ver Localizador)
            raise erro
        saco = regiao > 0
        # Confiança: quanto o saco se afasta do limiar (ΔE mediano dentro vs limiar), de 0 a 1. É o
        # contraste do saco ao fundo que o controlo da medição verifica (localizacao.qualidade_contraste_min),
        # se o saco veio da cor: um transparente (pelas arestas) tem o interior igual ao fundo por natureza.
        mediana = float(np.median(diferenca[saco]))
        score = float(np.clip(1 - limiar / max(mediana, 1e-6), 0, 1))
        if not usou_arestas:
            medidas["contraste"] = score
        return ResultadoSegmentacao(saco, score, self.nome, (perf_counter() - inicio) * 1000, diferenca,
                                    medidas, intermedios)

    def _parece_saco(self, regiao: np.ndarray | None, px_mm: float) -> bool:
        """A região tem dimensões de saco e forma quase retangular (não é um pedaço de saco)?"""
        if regiao is None:
            return False
        p = self.cfg.localizacao
        contornos, _ = cv2.findContours(regiao, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        (_, _), (lado1, lado2), _ = cv2.minAreaRect(contornos[0])
        comprimento, largura = max(lado1, lado2) / px_mm, min(lado1, lado2) / px_mm
        retangularidade = cv2.contourArea(contornos[0]) / max(lado1 * lado2, 1)
        return (p.comprimento_min_mm <= comprimento <= p.comprimento_max_mm
                and p.largura_min_mm <= largura <= p.largura_max_mm
                and retangularidade >= D.M2_RETANGULARIDADE_SO_COR)

    def _fundo(self, lab: np.ndarray, faixa: np.ndarray, forma_original: tuple[int, int]) -> np.ndarray:
        """Cor esperada do fundo em cada píxel: a foto de referência ou o modelo da faixa da borda."""
        if self._referencia is None:
            return fundo.modelar_fundo(lab, faixa)
        if self._referencia.shape[:2] != forma_original:
            raise ValueError(
                f"localizacao.fundo_referencia tem {self._referencia.shape[1]}x{self._referencia.shape[0]} px, "
                f"mas a imagem tem {forma_original[1]}x{forma_original[0]} px: tem de ser tirada com a mesma câmara"
            )
        altura, largura = lab.shape[:2]
        if (altura, largura) not in self._referencia_lab:
            ref = cv2.resize(self._referencia, (largura, altura), interpolation=cv2.INTER_AREA)
            self._referencia_lab[(altura, largura)] = fundo.lab(ref, self._sigma_px)
            self._referencia_luz[(altura, largura)] = float(np.median(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)[faixa]))
        return self._referencia_lab[(altura, largura)]
