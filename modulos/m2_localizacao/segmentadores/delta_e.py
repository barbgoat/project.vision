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

from collections.abc import Callable
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
        self._referencia_lab_total: np.ndarray | None = None  # a foto do fundo em CIELAB sem reduzir (uma vez)
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
        largura_faixa = max(2, round(p.faixa_fundo_mm * px_mm))
        faixa = fundo.faixa_da_borda(lab.shape[:2], largura_faixa)  # feitas uma vez por tamanho (cache)
        indices = fundo.indices_da_faixa(lab.shape[:2], largura_faixa)
        modelo = self._fundo(lab, faixa, img.shape[:2])
        desvio = np.zeros(3, np.float32)
        if self._referencia is None:
            diferenca = fundo.diferenca(lab, modelo, p.peso_luminosidade)
            limiar = p.limiar_delta_e or max(segmentacao.otsu(diferenca), p.delta_e_min)
            mascara = (diferenca > limiar).astype(np.uint8) * 255
            limiar_ruido = limiar
        else:
            # A luz muda um pouco entre a foto do fundo e esta (exposição, luz do dia): tira-se o desvio
            # mediano da faixa da borda, onde só há fundo. Depois, o limiar sai do ruído do fundo.
            # (lab − modelo − desvio, numa só imagem temporária: o mesmo que somar o desvio ao modelo)
            d = lab - modelo
            desvio = fundo.desvio_mediano(d, indices)
            d -= desvio
            diferenca = fundo.distancia(d, p.peso_luminosidade)
            suave = cv2.GaussianBlur(diferenca, (0, 0), D.M2_REFERENCIA_SUAVIZACAO_PX)
            na_faixa = suave.reshape(-1)[indices]
            mediana = float(np.median(na_faixa))
            ruido = 1.4826 * float(np.median(np.abs(na_faixa - mediana)))
            limiar = p.limiar_delta_e or max(mediana + D.M2_REFERENCIA_DESVIOS * ruido, D.M2_REFERENCIA_LIMIAR_MIN)
            mascara = (suave > limiar).astype(np.uint8) * 255
            limiar_ruido = limiar
            if not p.limiar_delta_e:
                limiar = max(limiar, self._meia_altura(suave, mascara, px_mm))
                if limiar > limiar_ruido:
                    acima_do_ruido = mascara
                    mascara = (suave > limiar).astype(np.uint8) * 255
                    mascara = cv2.bitwise_or(mascara, self._zonas_fracas(acima_do_ruido, mascara, d, px_mm))
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
        baias_tapadas = pedacos_juntos = 0
        if regiao is not None and not usou_arestas:
            # L* menos o do fundo (com a foto do fundo, já sem o desvio da luz)
            luminosidade = cv2.extractChannel(d, 0) if self._referencia is not None \
                else cv2.subtract(cv2.extractChannel(lab, 0), cv2.extractChannel(modelo, 0))
            regiao, pedacos_juntos = self._juntar_pedacos_soltos(regiao, mascara, diferenca, limiar, luminosidade,
                                                                 px_mm)
            regiao, baias_tapadas = self._tapar_baias_de_impressao(regiao, diferenca, px_mm)
        medidas = {"limiar_delta_e": limiar, "diferenca_max": float(diferenca.max()),
                   "usou_arestas": float(usou_arestas), "escala": escala,
                   "resolucao_px_por_mm": px_mm,  # a efetiva do mapa (sem redução: a da câmara)
                   "baias_tapadas": float(baias_tapadas), "pedacos_juntos": float(pedacos_juntos)}
        if self._referencia is not None:
            # Luz do fundo face à da foto do fundo vazio (cinzento mediano da faixa da borda, só fundo), em %.
            # O desvio tirado acima chega para mudanças pequenas; uma grande é a luz ou a exposição que mudaram.
            cinzento = cv2.cvtColor(pequena, cv2.COLOR_BGR2GRAY)
            luz_foto = self._referencia_luz[lab.shape[:2]]
            medidas["luz_pct"] = 100 * (float(np.median(cinzento.reshape(-1)[indices])) / max(luz_foto, 1.0) - 1)
        intermedios = {"diferenca": diferenca, "segmentacao": regiao if regiao is not None else mascara} \
            if guardar_intermedios else {}
        if regiao is None:
            erro = SemSaco(f"nada difere do fundo (diferença máxima ΔE {diferenca.max():.1f}, limiar {limiar:.1f})")
            erro.medidas, erro.intermedios = medidas, intermedios  # para o diagnóstico (ver Localizador)
            raise erro
        saco = regiao > 0
        # Confiança: quanto o saco se afasta do limiar do ruído (ΔE mediano dentro vs esse limiar; não o
        # de meia altura, que é metade do saco por construção), de 0 a 1. É o
        # contraste do saco ao fundo que o controlo da medição verifica (localizacao.qualidade_contraste_min),
        # se o saco veio da cor: um transparente (pelas arestas) tem o interior igual ao fundo por natureza.
        mediana = float(np.median(diferenca[saco]))
        score = float(np.clip(1 - limiar_ruido / max(mediana, 1e-6), 0, 1))
        if not usou_arestas and not self._so_pelo_contorno(diferenca, saco, limiar_ruido, mediana, px_mm):
            medidas["contraste"] = score
        # Com o mapa reduzido, o contorno subpíxel lê o mesmo ΔE na imagem à resolução total.
        amostrar = self._amostrador(img, modelo, desvio, px_por_mm) if escala < 1.0 else None
        return ResultadoSegmentacao(saco, score, self.nome, (perf_counter() - inicio) * 1000, diferenca,
                                    medidas, intermedios, amostrar)

    def _amostrador(self, img: np.ndarray, modelo: np.ndarray, desvio: np.ndarray,
                    px_por_mm: float) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
        """O ΔE ao fundo em pontos (xs, ys) da imagem original, sem a reduzir: a cor dos pontos (bilinear,
        CIELAB) menos o fundo nesses pontos e o mesmo desvio de luz do mapa. O fundo é a foto do fundo
        vazio em CIELAB à resolução total (feita uma vez) ou, sem ela, o modelo suave da borda
        (interpolado do mapa reduzido: varia devagar). Serve câmaras a cores e mono (a* = b* = 0)."""
        p = self.cfg.localizacao
        if self._referencia is not None:
            if self._referencia_lab_total is None:
                sigma = max(p.suavizacao_mm * px_por_mm, D.M2_SUAVIZACAO_MIN_PX)
                self._referencia_lab_total = fundo.lab(self._referencia, sigma)
            fundo_total = self._referencia_lab_total

            def fundo_em(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
                return cv2.remap(fundo_total, xs.astype(np.float32), ys.astype(np.float32), cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
        else:
            fy, fx = img.shape[0] / modelo.shape[0], img.shape[1] / modelo.shape[1]
            reduzido = modelo.astype(np.float32, copy=False)

            def fundo_em(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
                return cv2.remap(reduzido, ((xs + 0.5) / fx - 0.5).astype(np.float32),
                                 ((ys + 0.5) / fy - 0.5).astype(np.float32), cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)

        # O mesmo desfoque que o mapa leva (fundo.lab), ao longo de cada perfil: sem ele, o ruído do sensor
        # tira o máximo do gradiente do sítio nos sacos de pouco contraste.
        sigma_px = max(p.suavizacao_mm * px_por_mm, D.M2_SUAVIZACAO_MIN_PX)

        def amostrar(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
            cor = fundo.lab_nos_pontos(img, xs, ys)
            passo = float(np.hypot(xs[0, 1] - xs[0, 0], ys[0, 1] - ys[0, 0])) if xs.shape[1] > 1 else 1.0
            cor = cv2.GaussianBlur(cor, (0, 0), sigmaX=sigma_px / max(passo, 1e-6), sigmaY=1e-3,
                                   borderType=cv2.BORDER_REPLICATE)
            d = cor - fundo_em(xs, ys)
            d -= desvio
            return fundo.distancia(d, p.peso_luminosidade)
        return amostrar

    @staticmethod
    def _meia_altura(suave: np.ndarray, mascara: np.ndarray, px_mm: float) -> float:
        """Metade do ΔE do saco junto à aresta (faixa de M2_MEIA_ALTURA_FAIXA_MM a 2× isso para dentro).

        Com a foto do fundo, o limiar sai do ruído e é baixo: num saco de muito contraste, a cauda do
        desfoque passa-o já fora do saco e a máscara engorda (~1 mm por lado), longe de mais para o
        contorno subpíxel a ir buscar. A aresta de um degrau desfocado está a meia altura dele; com
        este limiar a máscara fica lá. Num saco de pouco contraste fica abaixo do limiar do ruído e não
        muda nada. 0 se não houver faixa (máscara vazia ou fina de mais).
        """
        raio = max(1, round(D.M2_MEIA_ALTURA_FAIXA_MM * px_mm))
        elemento = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * raio + 1, 2 * raio + 1))
        dentro = cv2.erode(mascara, elemento)
        faixa = (dentro > 0) & (cv2.erode(dentro, elemento) == 0)
        return 0.5 * float(np.median(suave[faixa])) if faixa.any() else 0.0

    @staticmethod
    def _zonas_fracas(acima_do_ruido: np.ndarray, mascara: np.ndarray, d: np.ndarray, px_mm: float) -> np.ndarray:
        """As zonas do saco de pouco contraste que o limiar de meia altura deixou de fora (uint8, 0 ou 255).

        A meia altura é metade do contraste mediano do saco. Num saco com impressão clara e escura
        num fundo escuro, as zonas escuras da impressão ficam abaixo dela: saem da máscara e, onde
        chegam à aresta, o contorno entra pelo saco dentro. Voltam a entrar as zonas acima do ruído
        (acima_do_ruido menos mascara) que sejam:
            grossas (≥ M2_ZONA_FRACA_MIN_MM): a cauda do desfoque de uma aresta forte, que a meia
                altura existe para cortar, é uma orla fina;
            mais claras do que o fundo (d = lab − fundo, L* > 0): uma sombra só escurece, por isso
                continua de fora, tenha a largura que tiver.
        """
        zonas = cv2.subtract(acima_do_ruido, mascara)
        raio = max(1, round(D.M2_ZONA_FRACA_MIN_MM * px_mm / 2))
        elemento = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * raio + 1, 2 * raio + 1))
        grossas = np.zeros_like(mascara)
        # Só o retângulo onde há zonas grossas (o caso comum é não haver nenhuma: só a orla fina)
        x, y, largura, altura = cv2.boundingRect(cv2.erode(zonas, elemento))
        if largura:
            aqui = np.s_[max(y - raio, 0):y + altura + raio, max(x - raio, 0):x + largura + raio]
            mais_clara = cv2.compare(cv2.extractChannel(d[aqui], 0), 0, cv2.CMP_GT)
            grossas[aqui] = cv2.morphologyEx(zonas[aqui] & mais_clara, cv2.MORPH_OPEN, elemento,
                                             borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return grossas

    @staticmethod
    def _juntar_pedacos_soltos(regiao: np.ndarray, mascara: np.ndarray, diferenca: np.ndarray, limiar: float,
                               luminosidade: np.ndarray, px_mm: float) -> tuple[np.ndarray, int]:
        """A região com os pedaços soltos do saco juntos, e quantos foram (uint8 0/255, int).

        Uma zona da impressão igual ao fundo pode separar da região um pedaço do saco de muito contraste
        (papa no fundo preto: um painel escuro no canto, cinzento igual ao do pano, e para lá dele a tira
        clara da solda). Na baía entre os dois o ΔE é o do fundo, por isso _tapar_baias_de_impressao não
        a tapa, e o canto todo ficava de fora (M3: "falta material num canto", 137 mm²). Mas o saco é uma
        peça só e convexo: um pedaço dele além da baía prova que a baía também é saco. Entram os pedaços
        da máscara fora da região que:
            têm pelo menos M2_PEDACO_AREA_MIN_MM2 e ficam a menos de M2_PEDACO_DISTANCIA_MAX_MM dela;
            têm muito contraste: o ΔE mediano no limiar (a meia altura do saco) ou acima (papa: 67 com
                limiar 20). As zonas fracas que _zonas_fracas trouxe (o pano, ΔE ~10) não provam nada;
            não tocam na borda da imagem (o fundo ou outro objeto cortados pelo campo);
            ficam dentro do retângulo mínimo da região (M2_PEDACO_DENTRO_MIN deles, com
                M2_PEDACO_RETANGULO_FOLGA_MM): só completam um canto ou uma aresta, nunca aumentam as
                medidas (as sombras e os restos do fundo ao longo de um lado ficam fora dele);
            são mais claros do que o fundo (luminosidade = L* − fundo, média > 0): uma sombra só escurece.
        O que o invólucro cresce com eles passa a ser saco, e com isso o que fica fechado entre eles e a
        região; as outras baías seguem para _tapar_baias_de_impressao.
        Limite: um rasgão num canto com um pedaço claro e solto (sujidade, um bocado de plástico) dentro do
        retângulo do saco fica tapado.
        """
        altura, largura = regiao.shape
        n, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(cv2.subtract(mascara, regiao), connectivity=8)
        if n < 2:
            return regiao, 0
        contornos, _ = cv2.findContours(regiao, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        maior = max(contornos, key=cv2.contourArea)
        retangulo = np.zeros_like(regiao)
        cv2.fillPoly(retangulo, [np.round(cv2.boxPoints(cv2.minAreaRect(maior))).astype(np.int32)], 255)
        folga = max(1, round(D.M2_PEDACO_RETANGULO_FOLGA_MM * px_mm))
        retangulo = cv2.dilate(retangulo, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * folga + 1,) * 2))
        distancia = cv2.distanceTransform(cv2.bitwise_not(regiao), cv2.DIST_L2, 5)
        pedacos = np.zeros_like(regiao)
        juntos = 0
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in estatisticas[i])
            if area < D.M2_PEDACO_AREA_MIN_MM2 * px_mm ** 2 or x == 0 or y == 0 or x + w == largura \
                    or y + h == altura:
                continue
            aqui = np.s_[y:y + h, x:x + w]
            este = rotulos[aqui] == i
            if (distancia[aqui][este].min() > D.M2_PEDACO_DISTANCIA_MAX_MM * px_mm
                    or np.count_nonzero(retangulo[aqui][este]) < D.M2_PEDACO_DENTRO_MIN * area
                    or float(np.median(diferenca[aqui][este])) < limiar
                    or float(np.mean(luminosidade[aqui][este])) <= 0):
                continue
            pedacos[aqui][este] = 255
            juntos += 1
        if not juntos:
            return regiao, 0
        dos_pedacos, _ = cv2.findContours(pedacos, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        antes, depois = np.zeros_like(regiao), np.zeros_like(regiao)
        cv2.fillPoly(antes, [cv2.convexHull(maior)], 255)
        cv2.fillPoly(depois, [cv2.convexHull(np.vstack([maior, *dos_pedacos]))], 255)
        # O que o invólucro cresceu, mais 1 px para dentro (a aresta antiga do invólucro, que o liga à região)
        cresceu = cv2.dilate(cv2.subtract(depois, antes), np.ones((3, 3), np.uint8)) & depois
        juntada = segmentacao.maior_regiao(regiao | pedacos | cresceu)
        return (regiao, 0) if juntada is None else (juntada, juntos)

    @staticmethod
    def _tapar_baias_de_impressao(regiao: np.ndarray, diferenca: np.ndarray, px_mm: float) -> tuple[np.ndarray, int]:
        """A região com as baías de impressão tapadas, e quantas foram (uint8 0/255, int).

        Um saco é convexo: o que falta à região para o seu invólucro convexo (uma "baía" na aresta) é
        material em falta (rasgão, corte, canto dobrado) ou uma zona da impressão tão parecida com o fundo
        que, píxel a píxel, não passou o limiar (impressão escura num fundo escuro, câmara mono). Os dois
        distinguem-se pela média: num rasgão, o que se vê na baía é o próprio fundo, com o mesmo ΔE que o
        fundo logo fora do invólucro; na impressão, o ΔE médio de centenas de píxeis fica claramente acima
        (bluey no fundo preto: +3,5 a +5; rasgões simulados com a foto do fundo: −2 a +1).

        Conta-se só a baía a mais de M2_BAIA_PAREDE_MM da parede do saco (o desfoque da aresta, e a sombra
        de um rasgão, ficam de fora), em médias locais (janelas de M2_BAIA_JANELA_MM), contra a faixa de
        M2_BAIA_FORA_MM fora do invólucro junto a ela. As zonas com o ΔE médio pelo menos
        M2_BAIA_DELTA_E_MIN acima passam a ser saco; o resto da baía fica, e o M3 avalia-o como sempre.
        Numa baía com impressão, o que sobra mais estreito do que M2_BAIA_SOBRA_MIN_MM (traços pretos do
        desenho, iguais ao fundo) vai com ela. Bluey (77 fotos) e Garnier (39): todos OK. Em 288 rasgões
        simulados em fotos reais do bluey (o fundo vazio colado no saco), só um (5 × 4 mm, encostado à
        impressão escura) ficou tapado em mais de metade; mediana 0 %.
        Limite: uma sombra ou sujidade dentro de um rasgão também sobe o ΔE.
        """
        contornos, _ = cv2.findContours(regiao, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        involucro = np.zeros_like(regiao)
        cv2.fillPoly(involucro, [cv2.convexHull(max(contornos, key=cv2.contourArea))], 255)
        baias = cv2.subtract(involucro, regiao)
        n, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(baias, connectivity=8)
        grandes = [i for i in range(1, n) if estatisticas[i, cv2.CC_STAT_AREA] >= D.M2_BAIA_AREA_MIN_MM2 * px_mm ** 2]
        if not grandes:
            return regiao, 0
        parede = D.M2_BAIA_PAREDE_MM * px_mm
        raio_fora = max(1, round(D.M2_BAIA_FORA_MM * px_mm))
        elemento = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * raio_fora + 1, 2 * raio_fora + 1))
        altura, largura = regiao.shape
        tapada, tapadas = regiao.copy(), 0
        for i in grandes:
            x, y, w, h = (int(estatisticas[i, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH,
                                                           cv2.CC_STAT_HEIGHT))
            aqui = np.s_[max(y - raio_fora, 0):min(y + h + raio_fora, altura),
                         max(x - raio_fora, 0):min(x + w + raio_fora, largura)]
            baia = (rotulos[aqui] == i).astype(np.uint8)
            longe_do_saco = cv2.distanceTransform(cv2.bitwise_not(regiao[aqui]), cv2.DIST_L2, 5) > parede
            dentro = (baia > 0) & longe_do_saco
            fora = (cv2.dilate(baia, elemento) > 0) & (involucro[aqui] == 0)
            if np.count_nonzero(dentro) < D.M2_BAIA_PIXEIS_MIN or np.count_nonzero(fora) < D.M2_BAIA_PIXEIS_MIN:
                continue
            # A média local (janela de M2_BAIA_JANELA_MM) e não a da baía toda: um rasgão encostado a uma
            # zona de impressão escura junta-se-lhe numa só baía, e a média dela taparia o rasgão também.
            peso = dentro.astype(np.float32)
            janela = (max(3, round(D.M2_BAIA_JANELA_MM * px_mm)),) * 2
            soma = cv2.boxFilter(diferenca[aqui] * peso, -1, janela, normalize=False, borderType=cv2.BORDER_CONSTANT)
            contagem = cv2.boxFilter(peso, -1, janela, normalize=False, borderType=cv2.BORDER_CONSTANT)
            media_local = soma / np.maximum(contagem, 1)
            fundo_fora = float(np.mean(diferenca[aqui][fora]))
            impressao = (dentro & (contagem >= 0.25 * janela[0] * janela[1])
                         & (media_local - fundo_fora >= D.M2_BAIA_DELTA_E_MIN)).astype(np.uint8)
            if not impressao.any():
                continue
            # A faixa junto à parede (que não se mediu) vai com a zona de impressão ao lado dela
            raio = max(1, int(np.ceil(parede)) + 1)
            impressao = cv2.dilate(impressao, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * raio + 1,) * 2)) & baia
            # O que sobra da baía e é estreito (um traço preto do desenho, já sem interior para a média) vai
            # com a impressão; só uma sobra larga (M2_BAIA_SOBRA_MIN_MM) pode ser material em falta.
            sobra = cv2.subtract(baia, impressao)
            k = max(3, 2 * round(D.M2_BAIA_SOBRA_MIN_MM * px_mm / 2) + 1)
            larga = cv2.morphologyEx(sobra, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
                                     borderType=cv2.BORDER_CONSTANT, borderValue=0)
            impressao = cv2.subtract(baia, larga)
            tapada[aqui][impressao > 0] = 255
            tapadas += 1
        return tapada, tapadas

    @staticmethod
    def _so_pelo_contorno(diferenca: np.ndarray, saco: np.ndarray, limiar: float, mediana: float,
                          px_mm: float) -> bool:
        """Um saco transparente visto pela cor: o interior não difere do fundo (mediana ≤ limiar), mas o
        anel junto à aresta (M2_CONTORNO_ANEL_MM) difere. Aí o contraste do interior não diz nada (é 0
        por natureza), como quando o saco vem das arestas; quem diz se a medida é boa é a aresta."""
        if mediana > limiar:
            return False
        raio = max(1, round(D.M2_CONTORNO_ANEL_MM * px_mm))
        mascara = saco.astype(np.uint8)
        anel = saco & (cv2.erode(mascara, np.ones((2 * raio + 1, 2 * raio + 1), np.uint8)) == 0)
        return bool(anel.any()) and float(np.median(diferenca[anel])) > limiar

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
