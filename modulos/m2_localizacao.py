"""M2 Localização: "onde está o saco? está completo?".

Encontra o saco na imagem, mede-o e devolve-o num recorte normalizado: rodado
para o eixo maior ficar na horizontal, centrado e com uma máscara da zona a
inspecionar (sem a faixa junto à borda). Todos os escravos de inspeção (M3 a
M6) trabalham sobre esse recorte.

O saco pode chegar em qualquer posição da imagem e com qualquer rotação; o M2
desfaz as duas, e os módulos seguintes veem sempre o saco "direito". Funciona
da mesma forma para uma fotografia de uma pasta e para uma imagem da câmara:
o M2 recebe só a imagem e não sabe de onde ela veio (isso é com o M1). O que
muda entre montagens (telemóvel, câmara) é a escala, por isso cada uma tem o
seu px_por_mm.

Como separa o saco do fundo (sacos de qualquer cor, opacos, translúcidos ou
transparentes):
    1. Modelo do fundo: uma foto do fundo vazio (fundo_referencia) ou, sem ela,
       uma superfície suave ajustada à faixa junto à borda da imagem, onde só
       há fundo; a superfície acompanha a luz rasante (um lado mais claro).
    2. Diferença de cor ao fundo, em CIELAB (ΔE): o saco é o que difere, seja
       mais claro, mais escuro ou de outra cor.
    3. Só se a cor não der uma região com forma de saco: juntar as arestas
       fortes (contorno, vincos, reflexos), que apanham os sacos
       transparentes, cujo interior quase não difere do fundo.
    4. Fecho morfológico, interior preenchido, maior região.

É o único módulo de que todos os outros dependem: sem saco localizado, o
Mestre não chama mais ninguém. Quando não encontra saco, levanta SemSaco com
o motivo, que aparece no registo.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.config import RAIZ, Config
from nucleo.contrato import Saco, SemSaco
from nucleo.imagens import ler_imagem

log = logging.getLogger(__name__)

_DELTA_E_PARA_U8 = 255 / 100  # escala do mapa de diferenças para o Otsu (ΔE 100 → 255)


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
        lab = _lab(pequena, self._sigma_px)
        faixa = _faixa_da_borda(lab.shape[:2], max(2, round(p.faixa_fundo_mm * px_mm)))
        fundo = self._fundo(lab, faixa, img.shape[:2])
        diferenca = _diferenca(lab, fundo, p.peso_luminosidade)

        limiar = p.limiar_delta_e or max(_otsu(diferenca), p.delta_e_min)
        mascara = (diferenca > limiar).astype(np.uint8) * 255
        fecho_px, abertura_px = p.fecho_mm * px_mm, p.abertura_mm * px_mm
        regiao = _maior_regiao(_limpar(mascara, fecho_px, abertura_px))
        # As arestas só entram se a cor não chegar (sacos transparentes): somá-las
        # sempre "engorda" o contorno e estraga as medidas dos sacos opacos.
        usou_arestas = False
        if p.usar_arestas and not self._parece_saco(regiao, px_mm):
            arestas = _arestas(lab, faixa, p.arestas_fator).astype(np.uint8) * 255
            com_arestas = _maior_regiao(_limpar(mascara | arestas, fecho_px, abertura_px))
            if com_arestas is not None:
                regiao, usou_arestas = com_arestas, True
        self.medidas.update(limiar_delta_e=limiar, diferenca_max=float(diferenca.max()),
                            usou_arestas=float(usou_arestas), escala=escala)
        if guardar_intermedios:
            self.intermedios.update(diferenca=diferenca, segmentacao=regiao if regiao is not None else mascara)
        if regiao is None:
            raise SemSaco(f"nada difere do fundo (diferença máxima ΔE {diferenca.max():.1f}, limiar {limiar:.1f})")
        if p.rejeitar_borda and _toca_na_borda(regiao):
            raise SemSaco("o saco toca na borda da imagem (incompleto)")

        # 2. Contorno na resolução original
        contorno = _contorno_original(regiao, img.shape[:2])

        # 3. Orientação e medidas
        centro, angulo, comprimento_px, largura_px = _pose(contorno)
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
        matriz, tamanho = _matriz_normalizacao(centro, angulo, comprimento_px, largura_px, p.folga_mm * ppm)
        recorte = cv2.warpAffine(img, matriz, tamanho, flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        mascara_recorte = np.zeros((tamanho[1], tamanho[0]), np.uint8)
        contorno_recorte = cv2.transform(contorno.astype(np.float32), matriz)
        cv2.fillPoly(mascara_recorte, [np.round(contorno_recorte).astype(np.int32)], 255)
        mascara_util = _zona_util(mascara_recorte, comprimento_px,
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
            return _modelar_fundo(lab, faixa)
        if self._referencia.shape[:2] != forma_original:
            raise ValueError(
                f"localizacao.fundo_referencia tem {self._referencia.shape[1]}x{self._referencia.shape[0]} px, "
                f"mas a imagem tem {forma_original[1]}x{forma_original[0]} px: tem de ser tirada com a mesma câmara"
            )
        altura, largura = lab.shape[:2]
        if (altura, largura) not in self._referencia_lab:
            ref = cv2.resize(self._referencia, (largura, altura), interpolation=cv2.INTER_AREA)
            self._referencia_lab[(altura, largura)] = _lab(ref, self._sigma_px)
        return self._referencia_lab[(altura, largura)]


# --- Segmentação ---------------------------------------------------------------------

def _lab(img: np.ndarray, sigma_px: float) -> np.ndarray:
    """BGR uint8 → CIELAB float32 (L* 0–100, a*, b* com sinal), ligeiramente suavizada."""
    lab = cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)
    return cv2.GaussianBlur(lab, (0, 0), max(sigma_px, 0.3))


def _faixa_da_borda(forma: tuple[int, int], largura: int) -> np.ndarray:
    """Máscara booleana da faixa junto à borda da imagem (onde só há fundo)."""
    faixa = np.zeros(forma, bool)
    faixa[:largura], faixa[-largura:] = True, True
    faixa[:, :largura], faixa[:, -largura:] = True, True
    return faixa


def _modelar_fundo(lab: np.ndarray, faixa: np.ndarray) -> np.ndarray:
    """Ajusta uma superfície suave (polinómio de 2.º grau em x, y) à cor da faixa da borda.

    Um polinómio acompanha as variações lentas da luz (luz rasante, vinhetagem).
    O ajuste é robusto: em cada volta descarta os píxeis que se afastam muito
    (sujidade, um canto do saco), para o modelo representar só o fundo.
    """
    altura, largura = lab.shape[:2]
    ys, xs = np.nonzero(faixa)
    passo = max(1, len(xs) // D.M2_FUNDO_PONTOS_MAX)
    xs, ys = xs[::passo], ys[::passo]
    valores = lab[ys, xs].astype(np.float64)
    base = _base_polinomial(xs / (largura - 1) * 2 - 1, ys / (altura - 1) * 2 - 1)

    usar = np.ones(len(xs), bool)
    coeficientes = np.zeros((6, 3))
    for _ in range(D.M2_FUNDO_ITERACOES):
        if usar.sum() < 12:
            break
        coeficientes, *_ = np.linalg.lstsq(base[usar], valores[usar], rcond=None)
        residuo = np.linalg.norm(valores - base @ coeficientes, axis=1)
        mediana = np.median(residuo[usar])
        mad = np.median(np.abs(residuo[usar] - mediana))
        # 1,4826·MAD ≈ desvio-padrão, mas sem se deixar levar pelos pontos estranhos
        usar = residuo <= mediana + D.M2_FUNDO_DESVIOS * 1.4826 * mad + D.M2_FUNDO_TOLERANCIA_DELTA_E

    # Avaliar a superfície em toda a imagem: os termos só de x e só de y
    # calculam-se numa linha/coluna; só o termo x·y precisa da grelha inteira.
    c = coeficientes.astype(np.float32)
    x = np.linspace(-1, 1, largura, dtype=np.float32)[None, :, None]
    y = np.linspace(-1, 1, altura, dtype=np.float32)[:, None, None]
    so_x = c[0] + c[1] * x + c[3] * x * x  # 1 × largura × 3
    so_y = c[2] * y + c[5] * y * y  # altura × 1 × 3
    return so_x + so_y + c[4] * (y * x)


def _base_polinomial(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Colunas 1, x, y, x², xy, y² (a mesma ordem usada em _modelar_fundo)."""
    return np.stack([np.ones_like(x, dtype=np.float64), x, y, x * x, x * y, y * y], axis=1)


def _diferenca(lab: np.ndarray, fundo: np.ndarray, peso_luminosidade: float) -> np.ndarray:
    """ΔE de cada píxel face ao fundo, com a luminosidade pesada por peso_luminosidade."""
    d = lab - fundo
    return np.sqrt((peso_luminosidade * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def _otsu(diferenca: np.ndarray) -> float:
    """Limiar de Otsu sobre o mapa de diferenças, devolvido em ΔE."""
    u8 = np.clip(diferenca * _DELTA_E_PARA_U8, 0, 255).astype(np.uint8)
    limiar, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(limiar) / _DELTA_E_PARA_U8


def _arestas(lab: np.ndarray, faixa: np.ndarray, fator: float) -> np.ndarray:
    """Arestas bem mais fortes do que o "grão" normal do fundo (percentil 99 na faixa da borda)."""
    gx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3)
    gradiente = np.sqrt((gx * gx + gy * gy).sum(axis=2)) / 8  # /8: o Sobel 3×3 amplifica 8×
    normal = max(float(np.percentile(gradiente[faixa], 99)), D.M2_GRADIENTE_FUNDO_MIN)
    return gradiente > fator * normal


def _limpar(mascara: np.ndarray, fecho_px: float, abertura_px: float) -> np.ndarray:
    """Fecho (tapa falhas, liga as arestas), preenchimento dos buracos e abertura (tira pontos e fios).

    O preenchimento vem antes da abertura: o contorno de um saco transparente é
    um anel fino, que a abertura apagaria se o interior ainda estivesse vazio.
    """
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, _disco(fecho_px))
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    preenchida = np.zeros_like(mascara)
    cv2.drawContours(preenchida, contornos, -1, 255, thickness=cv2.FILLED)
    return cv2.morphologyEx(preenchida, cv2.MORPH_OPEN, _disco(abertura_px))


def _disco(diametro_px: float) -> np.ndarray:
    """Elemento estruturante circular com o diâmetro dado (ímpar, no mínimo 3 px)."""
    k = max(3, 2 * round(diametro_px / 2) + 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _maior_regiao(mascara: np.ndarray) -> np.ndarray | None:
    """A maior região (há um saco por imagem), preenchida; None se não houver nenhuma."""
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    maior = max(contornos, key=cv2.contourArea)
    if cv2.contourArea(maior) < 1:
        return None
    regiao = np.zeros_like(mascara)
    cv2.drawContours(regiao, [maior], -1, 255, thickness=cv2.FILLED)
    return regiao


def _toca_na_borda(regiao: np.ndarray) -> bool:
    return bool(regiao[0].any() or regiao[-1].any() or regiao[:, 0].any() or regiao[:, -1].any())


def _contorno_original(regiao: np.ndarray, forma_original: tuple[int, int]) -> np.ndarray:
    """Leva a região para a resolução original e devolve o seu contorno exterior (N×1×2, int32).

    Só amplia a zona à volta do saco (mais rápido do que a imagem toda), com a
    mesma correspondência de píxeis que o cv2.resize da imagem inteira.
    """
    altura, largura = forma_original
    if regiao.shape != (altura, largura):
        fx, fy = largura / regiao.shape[1], altura / regiao.shape[0]
        x, y, w, h = cv2.boundingRect(regiao)
        x0, y0 = max(x - 2, 0), max(y - 2, 0)
        x1, y1 = min(x + w + 2, regiao.shape[1]), min(y + h + 2, regiao.shape[0])
        X0, Y0 = round(x0 * fx), round(y0 * fy)
        X1, Y1 = min(round(x1 * fx), largura), min(round(y1 * fy), altura)
        # píxel (X, Y) da original ↔ ((X + 0,5)/fx − 0,5, (Y + 0,5)/fy − 0,5) da reduzida
        matriz = np.array([[fx, 0, fx * (x0 + 0.5) - 0.5 - X0], [0, fy, fy * (y0 + 0.5) - 0.5 - Y0]])
        ampliada = cv2.warpAffine(regiao[y0:y1, x0:x1], matriz, (X1 - X0, Y1 - Y0), flags=cv2.INTER_LINEAR)
        mascara = np.where(ampliada > 127, 255, 0).astype(np.uint8)
        deslocamento = (X0, Y0)
    else:
        mascara, deslocamento = regiao, (0, 0)
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=deslocamento)
    return max(contornos, key=cv2.contourArea).astype(np.int32)


# --- Medidas e normalização ----------------------------------------------------------

def _pose(contorno: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    """Centro, ângulo do eixo maior (rad, em (−π/2, π/2]), comprimento e largura em px.

    O ângulo vem dos momentos centrais, θ = ½·atan2(2·μ11, μ20 − μ02), que não
    depende das convenções de ângulo do cv2.minAreaRect, e é depois afinado
    pelos lados compridos (reta ajustada à parte central de cada um): um canto
    dobrado ou uma aba desequilibram os momentos e inclinavam o saco ~1°.

    As medidas são como as de um paquímetro encostado aos lados: a posição de
    cada lado é a mediana dos pontos do contorno nesse lado, longe dos cantos.
    Assim, a "escada" de píxeis de um saco rodado, um canto saliente ou um
    píxel a mais não mexem nas medidas (a forma irregular é assunto do M3).
    O centro é o meio entre os lados (não o centro de massa), para as posições
    em mm irem de −comprimento/2 a +comprimento/2.
    """
    m = cv2.moments(contorno)
    if m["m00"] <= 0:
        raise SemSaco("o contorno encontrado não tem área")
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    pontos = contorno.reshape(-1, 2).astype(np.float64) - (cx, cy)

    angulo = 0.5 * math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"])
    u, v = _projetar(pontos, angulo)
    if np.ptp(v) > np.ptp(u):  # forma quase quadrada: garantir que o eixo "maior" é o maior
        angulo += math.pi / 2
    for _ in range(D.M2_ANGULO_ITERACOES):
        correcao_angulo = _inclinacao_lados(*_projetar(pontos, angulo))
        if correcao_angulo is None:
            break
        angulo += correcao_angulo
    if angulo > math.pi / 2:
        angulo -= math.pi
    elif angulo <= -math.pi / 2:
        angulo += math.pi
    u, v = _projetar(pontos, angulo)

    esquerda, direita, cima, baixo = _lados(u, v)
    cos, sen = math.cos(angulo), math.sin(angulo)
    # Os pontos do contorno são centros de píxeis da borda, em média meio píxel
    # (projetado na normal do lado) para dentro de cada lado.
    correcao = max(abs(cos), abs(sen))
    comprimento = direita - esquerda + correcao
    largura = baixo - cima + correcao
    meio_u, meio_v = (esquerda + direita) / 2, (cima + baixo) / 2
    centro = np.array([cx + meio_u * cos - meio_v * sen, cy + meio_u * sen + meio_v * cos])
    return centro, angulo, comprimento, largura


def _projetar(pontos: np.ndarray, angulo: float) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas dos pontos ao longo do eixo maior (u) e do eixo menor (v)."""
    cos, sen = math.cos(angulo), math.sin(angulo)
    return pontos[:, 0] * cos + pontos[:, 1] * sen, -pontos[:, 0] * sen + pontos[:, 1] * cos


def _inclinacao_lados(u: np.ndarray, v: np.ndarray) -> float | None:
    """Quanto rodar (rad) para os lados compridos ficarem na horizontal.

    Ajusta uma reta v = a·u + b à parte central de cada lado comprido, deita
    fora os pontos que se afastam mais do que M2_ANGULO_DESVIO_PX (uma aba,
    um rasgão) e volta a ajustar. Devolve a média das inclinações, pesada
    pelo nº de pontos, ou None se não houver pontos suficientes.
    """
    meio_u, meio_v = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    central = np.abs(u - meio_u) < D.M2_PARTE_CENTRAL_LADOS / 2 * np.ptp(u)
    inclinacoes, pesos = [], []
    for lado in (central & (v < meio_v), central & (v > meio_v)):
        uu, vv = u[lado], v[lado]
        if len(uu) < 10:
            continue
        a, b = np.polyfit(uu, vv, 1)
        perto = np.abs(vv - (a * uu + b)) < D.M2_ANGULO_DESVIO_PX
        if perto.sum() >= 10:
            a, _ = np.polyfit(uu[perto], vv[perto], 1)
        inclinacoes.append(a)
        pesos.append(int(perto.sum()))
    if not inclinacoes:
        return None
    return math.atan(float(np.average(inclinacoes, weights=pesos)))


def _lados(u: np.ndarray, v: np.ndarray) -> tuple[float, float, float, float]:
    """Posição (mediana) dos lados esquerdo, direito, de cima e de baixo, longe dos cantos."""
    meio_u, meio_v = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    metade = D.M2_PARTE_CENTRAL_LADOS / 2
    central_u = np.abs(u - meio_u) < metade * np.ptp(u)  # parte central dos lados compridos
    central_v = np.abs(v - meio_v) < metade * np.ptp(v)  # parte central dos lados curtos

    def mediana(valores: np.ndarray, extremo: float) -> float:
        return float(np.median(valores)) if len(valores) >= 3 else extremo

    return (
        mediana(u[central_v & (u < meio_u)], u.min()),
        mediana(u[central_v & (u > meio_u)], u.max()),
        mediana(v[central_u & (v < meio_v)], v.min()),
        mediana(v[central_u & (v > meio_v)], v.max()),
    )


def _matriz_normalizacao(
    centro: np.ndarray, angulo: float, comprimento_px: float, largura_px: float, folga_px: float
) -> tuple[np.ndarray, tuple[int, int]]:
    """Matriz afim 2×3 (imagem original → recorte) e tamanho do recorte (largura, altura).

    Roda de −ângulo em torno do centro do saco (o eixo maior fica na
    horizontal) e põe esse centro no centro do recorte, ((L−1)/2, (A−1)/2).
    """
    largura_rec = int(math.ceil(comprimento_px + 2 * folga_px))
    altura_rec = int(math.ceil(largura_px + 2 * folga_px))
    cos, sen = math.cos(angulo), math.sin(angulo)
    rotacao = np.array([[cos, sen], [-sen, cos]])
    destino = np.array([(largura_rec - 1) / 2, (altura_rec - 1) / 2])
    translacao = destino - rotacao @ centro
    return np.hstack([rotacao, translacao[:, None]]), (largura_rec, altura_rec)


def _zona_util(mascara: np.ndarray, comprimento_px: float, margem_px: float, extremidades_px: float) -> np.ndarray:
    """Máscara sem a faixa junto ao contorno e sem as pontas do eixo maior."""
    distancia = cv2.distanceTransform(mascara, cv2.DIST_L2, 5)
    util = distancia > margem_px
    centro_x = (mascara.shape[1] - 1) / 2
    colunas = np.arange(mascara.shape[1])
    fora = np.abs(colunas - centro_x) > comprimento_px / 2 - extremidades_px
    util[:, fora] = False
    return util.astype(np.uint8) * 255
