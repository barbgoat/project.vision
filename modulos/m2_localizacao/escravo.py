"""M2 Localização: o ponto de entrada que o Mestre chama (Localizador.localizar).

Liga as etapas pela ordem, um ficheiro cada:

    segmentadores/    1. separar o saco do fundo (ΔE, SAM 3 ou os dois)
    segmentacao.py    2. contorno subpíxel (e o limiar, arestas e limpeza do ΔE)
    medicao.py        3. ângulo, comprimento e largura
    normalizacao.py   4. rodar, recortar, zona útil
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from modulos.m2_localizacao import folha, marcadores, medicao, normalizacao, segmentacao, segmentadores
from nucleo import defines as D
from nucleo.config import Config
from nucleo.contrato import Saco, SemSaco


@dataclass
class Diagnostico:
    """Os valores (limiar usado, escala...) e as imagens das etapas de uma chamada ao M2.

    É de quem chama, não do Localizador (sem estado entre chamadas); vai sendo preenchido,
    por isso também mostra até onde o M2 chegou quando não encontra o saco ou falha.
    """

    medidas: dict[str, float] = field(default_factory=dict)
    intermedios: dict[str, np.ndarray] = field(default_factory=dict)


class Localizador:
    """O escravo M2. Usa a secção localizacao da configuração. Sem estado entre chamadas."""

    nome = "localizacao"
    pergunta = "Onde está o saco? Está completo?"

    def __init__(self, cfg: Config, segmentador: segmentadores.Segmentador | None = None) -> None:
        """Prepara o M2 e o segmentador (com o ΔE e fundo_referencia, lê-a já: erro claro no arranque).

        segmentador: para usar outro que não o de cfg.localizacao.segmentador (testes).
        """
        self.cfg = cfg
        self.segmentador = segmentador or segmentadores.criar(cfg)

    def retificar(self, img: np.ndarray) -> np.ndarray:
        """Etapa 0, só com localizacao.referencia: a foto endireitada pela folha (folha.py) ou
        pelos marcadores ArUco (marcadores.py); com "auto", os marcadores se houver algum, senão a folha.

        O Mestre usa a imagem devolvida em vez da original. Levanta SemSaco se
        a referência não aparecer inteira.
        """
        referencia = self.cfg.localizacao.referencia
        if referencia == "folha":
            return folha.retificar(img, self.cfg)
        try:
            return marcadores.retificar(img, self.cfg)
        except marcadores.SemMarcadores:
            if referencia == "marcadores":
                raise
        try:
            return folha.retificar(img, self.cfg)
        except SemSaco:
            raise SemSaco("sem referência (nem marcadores nem folha na foto)") from None

    def localizar_sem_escala(self, img: np.ndarray, guardar_intermedios: bool = False,
                             diagnostico: Diagnostico | None = None) -> tuple[Saco, float]:
        """O saco numa foto sem referência: (Saco, px por "mm" provisório).

        Sem a referência não há mm; a foto é tratada como tendo M2_SEM_ESCALA_CAMPO_MM de
        lado maior, só para os tamanhos internos do M2 (fecho, margens), com limites de
        tamanho largos. As medidas do Saco × o valor devolvido dão as medidas em px.
        """
        ppm = max(img.shape[:2]) / D.M2_SEM_ESCALA_CAMPO_MM
        campo = D.M2_SEM_ESCALA_CAMPO_MM
        loc = replace(self.cfg.localizacao, referencia="nenhuma",
                      comprimento_min_mm=D.M2_SEM_ESCALA_COMPRIMENTO_MIN * campo,
                      comprimento_max_mm=D.M2_SEM_ESCALA_MAX * campo,
                      largura_min_mm=D.M2_SEM_ESCALA_LARGURA_MIN * campo, largura_max_mm=D.M2_SEM_ESCALA_MAX * campo)
        provisorio = Localizador(replace(self.cfg, geral=replace(self.cfg.geral, px_por_mm=ppm), localizacao=loc))
        return provisorio.localizar(img, guardar_intermedios, diagnostico), ppm

    def localizar(self, img: np.ndarray, guardar_intermedios: bool = False,
                  diagnostico: Diagnostico | None = None) -> Saco:
        """Encontra o saco na imagem e normaliza a sua pose.

        Recebe:
            img: imagem original, BGR uint8 (Captura.imagem), só de leitura.
            guardar_intermedios: se True, guarda em diagnostico.intermedios o mapa de
                diferenças ao fundo, a máscara da segmentação e o recorte com a
                zona útil contornada a amarelo.
            diagnostico: se dado, recebe os valores e as imagens das etapas (ver Diagnostico).

        Devolve:
            O Saco com o recorte normalizado. Se não houver um saco completo e
            com dimensões plausíveis, levanta SemSaco com o motivo.
        """
        p = self.cfg.localizacao
        diag = diagnostico if diagnostico is not None else Diagnostico()

        # 1. Segmentação (o backend de localizacao.segmentador: ΔE, SAM 3 ou os dois)
        try:
            seg = self.segmentador.segmentar(img, self.cfg.geral.px_por_mm, guardar_intermedios)
        except SemSaco as erro:  # o ΔE junta o diagnóstico (limiar, mapa) ao motivo
            diag.medidas.update(getattr(erro, "medidas", {}))
            diag.intermedios.update(getattr(erro, "intermedios", {}))
            raise
        diag.medidas.update(seg.medidas, score_segmentacao=seg.score, tempo_segmentacao_ms=seg.tempo_ms)
        diag.intermedios.update(seg.intermedios)
        regiao = seg.mascara.astype(np.uint8) * 255
        if p.rejeitar_borda and segmentacao.toca_na_borda(regiao):
            raise SemSaco("o saco toca na borda da imagem (incompleto)")

        # 2. Contorno na resolução original
        # e cada ponto na aresta verdadeira (subpíxel), seja qual for o limiar
        arestas, normais = segmentacao.afinar_contorno(segmentacao.contorno_original(regiao, img.shape[:2]),
                                                       seg.mapa_aresta, img.shape[:2])
        # Centros dos píxeis de borda (meio píxel por dentro da aresta): o que o fillPoly e o desenho esperam
        borda = (arestas - 0.5 * normais).reshape(-1, 1, 2).astype(np.float32)
        contorno = np.round(borda).astype(np.int32)

        # 3. Orientação e medidas
        centro, angulo, comprimento_px, largura_px = medicao.pose(arestas, self.cfg.geral.px_por_mm)
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
        contorno_recorte = cv2.transform(borda, matriz)
        cv2.fillPoly(mascara_recorte, [np.round(contorno_recorte).astype(np.int32)], 255)
        mascara_util = normalizacao.zona_util(mascara_recorte, comprimento_px,
                                  p.margem_borda_mm * ppm, p.margem_extremidades_mm * ppm)
        if guardar_intermedios:
            # O recorte com a zona útil (o que os módulos seguintes inspecionam) contornada a amarelo.
            vista = recorte.copy()
            zona, _ = cv2.findContours(mascara_util, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vista, zona, -1, (0, 255, 255), max(1, round(ppm / 4)))
            diag.intermedios["recorte"] = vista

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
