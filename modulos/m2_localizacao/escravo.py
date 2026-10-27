"""M2 Localização: o ponto de entrada que o Mestre chama (Localizador.localizar).

Liga as etapas pela ordem, um ficheiro cada:

    fundo.py          1. modelo do fundo e diferença de cor ao fundo
    segmentacao.py    2. limiar, arestas, limpeza, maior região, contorno
    medicao.py        3. ângulo, comprimento e largura
    normalizacao.py   4. rodar, recortar, zona útil
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from modulos.m2_localizacao import fundo, medicao, normalizacao, segmentacao
from nucleo import defines as D
from nucleo.config import RAIZ, Config
from nucleo.contrato import Saco, SemSaco
from nucleo.imagens import ler_imagem


class Localizador:
    """O escravo M2. Usa a secção localizacao da configuração."""

    nome = "localizacao"
    pergunta = "Onde está o saco? Está completo?"

    def __init__(self, cfg: Config) -> None:
        """Prepara o M2; se houver fundo_referencia, lê-a já (erro claro logo no arranque)."""
        self.cfg = cfg
        self.medidas: dict[str, float] = {}  # valores da última chamada (limiar usado, escala...)
        self.intermedios: dict[str, np.ndarray] = {}  # imagens da última chamada, se pedidas
        self._referencia: np.ndarray | None = None
        self._sigma_px = 1.0  # suavização em px da imagem reduzida (calculada em cada chamada)
        self._referencia_lab: dict[tuple[int, int], np.ndarray] = {}  # por tamanho da imagem reduzida

        caminho = cfg.localizacao.fundo_referencia
        if caminho:
            caminho_abs = Path(caminho) if Path(caminho).is_absolute() else RAIZ / caminho
            if not caminho_abs.is_file():
                raise FileNotFoundError(f"localizacao.fundo_referencia não encontrada: {caminho_abs}")
            self._referencia = ler_imagem(caminho_abs)
            if self._referencia is None:
                raise ValueError(f"localizacao.fundo_referencia não é uma imagem válida: {caminho_abs}")

    def localizar(self, img: np.ndarray, guardar_intermedios: bool = False) -> Saco:
        """Encontra o saco na imagem e normaliza a sua pose.

        Recebe:
            img: imagem original, BGR uint8 (Captura.imagem), só de leitura.
            guardar_intermedios: se True, guarda em self.intermedios o mapa de
                diferenças ao fundo, a máscara da segmentação e o recorte com a
                zona útil contornada a amarelo.

        Devolve:
            O Saco com o recorte normalizado. Se não houver um saco completo e
            com dimensões plausíveis, levanta SemSaco com o motivo.
        """
        p = self.cfg.localizacao
        self.medidas, self.intermedios = {}, {}

        # 1. Segmentação numa versão reduzida da imagem (rápido)
        escala = min(1.0, p.resolucao_px_por_mm / self.cfg.geral.px_por_mm)
        pequena = img if escala == 1.0 else cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
        px_mm = self.cfg.geral.px_por_mm * pequena.shape[1] / img.shape[1]  # px/mm na imagem reduzida
        self._sigma_px = D.M2_SUAVIZACAO_MM * px_mm
        lab = fundo.lab(pequena, self._sigma_px)
        faixa = fundo.faixa_da_borda(lab.shape[:2], max(2, round(p.faixa_fundo_mm * px_mm)))
        modelo_fundo = self._fundo(lab, faixa, img.shape[:2])
        diferenca = fundo.diferenca(lab, modelo_fundo, p.peso_luminosidade)

        limiar = p.limiar_delta_e or max(segmentacao.otsu(diferenca), p.delta_e_min)
        mascara = (diferenca > limiar).astype(np.uint8) * 255
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
        self.medidas.update(limiar_delta_e=limiar, diferenca_max=float(diferenca.max()),
                            usou_arestas=float(usou_arestas), escala=escala)
        if guardar_intermedios:
            self.intermedios.update(diferenca=diferenca, segmentacao=regiao if regiao is not None else mascara)
        if regiao is None:
            raise SemSaco(f"nada difere do fundo (diferença máxima ΔE {diferenca.max():.1f}, limiar {limiar:.1f})")
        if p.rejeitar_borda and segmentacao.toca_na_borda(regiao):
            raise SemSaco("o saco toca na borda da imagem (incompleto)")

        # 2. Contorno na resolução original
        contorno = segmentacao.contorno_original(regiao, img.shape[:2])

        # 3. Orientação e medidas
        centro, angulo, comprimento_px, largura_px = medicao.pose(contorno)
        ppm = self.cfg.geral.px_por_mm
        comprimento_mm, largura_mm = comprimento_px / ppm, largura_px / ppm
        if not (p.comprimento_min_mm <= comprimento_mm <= p.comprimento_max_mm
                and p.largura_min_mm <= largura_mm <= p.largura_max_mm):
            raise SemSaco(
                f"objeto com {comprimento_mm:.0f} x {largura_mm:.0f} mm, fora dos limites de um saco "
                f"({p.comprimento_min_mm:.0f}–{p.comprimento_max_mm:.0f} x {p.largura_min_mm:.0f}–"
                f"{p.largura_max_mm:.0f} mm); o px_por_mm está calibrado?"
            )

        # 4. Normalização: rodar, centrar e recortar
        matriz, tamanho = normalizacao.matriz_normalizacao(centro, angulo, comprimento_px, largura_px, p.folga_mm * ppm)
        recorte = cv2.warpAffine(img, matriz, tamanho, flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        mascara_recorte = np.zeros((tamanho[1], tamanho[0]), np.uint8)
        contorno_recorte = cv2.transform(contorno.astype(np.float32), matriz)
        cv2.fillPoly(mascara_recorte, [np.round(contorno_recorte).astype(np.int32)], 255)
        mascara_util = normalizacao.zona_util(mascara_recorte, comprimento_px,
                                  p.margem_borda_mm * ppm, p.margem_extremidades_mm * ppm)
        if guardar_intermedios:
            # O recorte com a zona útil (o que os módulos seguintes inspecionam) contornada a amarelo.
            vista = recorte.copy()
            zona, _ = cv2.findContours(mascara_util, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vista, zona, -1, (0, 255, 255), max(1, round(ppm / 4)))
            self.intermedios["recorte"] = vista

        return Saco(
            contorno=contorno,
            centro_px=(float(centro[0]), float(centro[1])),
            angulo_graus=math.degrees(angulo),
            comprimento_mm=comprimento_mm,
            largura_mm=largura_mm,
            matriz=matriz,
            imagem=recorte,
            mascara=mascara_recorte,
            mascara_util=mascara_util,
        )

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
        return self._referencia_lab[(altura, largura)]
