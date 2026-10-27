"""Apoio aos testes: cenas sintéticas (fundo, sacos, tapete a andar) e correr os comandos do projeto.

Medidas em mm; imagens BGR uint8, com as bordas desenhadas a 4× e reduzidas
(anti-aliasing), como numa câmara real.
"""

from __future__ import annotations

import csv
import math
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from nucleo.config import RAIZ
from nucleo.contrato import Captura


# --- Comandos e ficheiros -----------------------------------------------------------------

def correr(*argumentos: str | Path) -> subprocess.CompletedProcess:
    """Corre um comando do projeto (p.ex. "main.py", "--help") com o Python deste ambiente, na raiz."""
    return subprocess.run(
        [sys.executable, *map(str, argumentos)], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=300,
    )


def ler_csv(caminho: Path) -> list[dict[str, str]]:
    """Linhas de um CSV do M7 (separador ';', utf-8 com BOM)."""
    return list(csv.DictReader(caminho.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))


def execucao(pasta_saida: Path) -> Path:
    """A pasta <saida>/<data_hora>/ criada por uma execução do main.py."""
    pastas = [p for p in pasta_saida.iterdir() if p.is_dir()]
    assert len(pastas) == 1, pastas
    return pastas[0]


def com_acentos(tmp_path: Path) -> Path:
    """Subpasta de tmp_path com espaços e acentos no nome (como as pastas do utilizador)."""
    pasta = tmp_path / "Ensaios de iluminação ç"
    pasta.mkdir(exist_ok=True)
    return pasta


# --- Cenas sintéticas ---------------------------------------------------------------------

COMPRIMENTO, LARGURA = 150.0, 55.0  # saco nominal (mm)
FUNDO_BGR = (110, 115, 105)  # tapete cinzento

# Sacos opacos de várias cores (BGR)
CORES = {
    "branco": (235, 235, 235),
    "preto": (25, 25, 25),
    "vermelho": (40, 40, 200),
    "azul": (190, 90, 30),
    "verde": (60, 170, 60),
    "amarelo": (40, 210, 230),
    "cinzento_escuro": (70, 72, 68),
    "rosa_claro": (200, 180, 240),
}


def fundo(altura: int, largura: int, rng: np.random.Generator, base=FUNDO_BGR, gradiente: float = 60,
          ruido: float = 3.0, textura: bool = False) -> np.ndarray:
    """Tapete com luz rasante (mais claro à esquerda) e ruído, em float32."""
    x = np.linspace(1, -1, largura)[None, :, None]
    img = np.array(base, np.float32)[None, None, :] + gradiente / 2 * x + np.zeros((altura, 1, 1), np.float32)
    if textura:
        manchas = cv2.GaussianBlur(rng.normal(0, 1, (altura, largura)).astype(np.float32), (0, 0), 6)
        img += (manchas / manchas.std() * 12)[..., None]
    return img + rng.normal(0, ruido, img.shape).astype(np.float32)


def alfa_poligono(altura: int, largura: int, pontos_px, sobre: int = 4) -> np.ndarray:
    """Máscara 0..1 de um polígono (em px), com bordas anti-aliased."""
    grande = np.zeros((altura * sobre, largura * sobre), np.uint8)
    cv2.fillPoly(grande, [np.round(np.asarray(pontos_px, np.float64) * sobre).astype(np.int32)], 255)
    return cv2.resize(grande, (largura, altura), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def alfa_retangulo(altura: int, largura: int, cx: float, cy: float, comp_px: float, larg_px: float,
                   angulo_graus: float) -> np.ndarray:
    """Máscara 0..1 de um retângulo rodado (centro e lados em px)."""
    return alfa_poligono(altura, largura, cv2.boxPoints(((cx, cy), (comp_px, larg_px), angulo_graus)))


def retangulo(comprimento: float = COMPRIMENTO, largura: float = LARGURA) -> list[tuple[float, float]]:
    """Os quatro cantos de um saco, em mm, no referencial do saco (centro na origem)."""
    c, l = comprimento / 2, largura / 2
    return [(-c, -l), (c, -l), (c, l), (-c, l)]


def _c(dx: float = 0.0) -> float:
    return COMPRIMENTO / 2 + dx


def _l(dy: float = 0.0) -> float:
    return LARGURA / 2 + dy


# Sacos com defeitos de forma (polígonos em mm, no referencial do saco)
FORMAS = {
    "certo": retangulo(),
    "comprido_158": retangulo(158, LARGURA),
    "estreito_50": retangulo(COMPRIMENTO, 50),
    "canto_dobrado": [(-_c(), -_l()), (_c(-15), -_l()), (_c(), -_l(-15)), (_c(), _l()), (-_c(), _l())],
    "corte_torto": [(-_c(), -_l()), (_c(), -_l()), (_c(-12), _l()), (-_c(), _l())],
    "aba": [(-_c(), -_l()), (_c(), -_l()), (_c(), _l()), (10, _l()), (10, _l(6)), (-10, _l(6)), (-10, _l()),
            (-_c(), _l())],
    "rasgao_lado": [(-_c(), -_l()), (_c(), -_l()), (_c(), _l()), (5, _l()), (5, _l(-5)), (-5, _l(-5)), (-5, _l()),
                    (-_c(), _l())],
    "canto_e_aba": [(-_c(), -_l()), (_c(-15), -_l()), (_c(), -_l(-15)), (_c(), _l()), (10, _l()), (10, _l(6)),
                    (-10, _l(6)), (-10, _l()), (-_c(), _l())],
}


def cena_saco(rng: np.random.Generator, cor=CORES["vermelho"], angulo: float = 23.0,
              comprimento: float = COMPRIMENTO, largura: float = LARGURA, cx: float | None = None,
              cy: float | None = None, ppm: float = 5.0, tamanho: tuple[int, int] = (1000, 1250),
              opacidade: float = 1.0, transparente: bool = False, sombra: bool = False,
              **kw_fundo) -> tuple[np.ndarray, tuple[float, float]]:
    """Um saco retangular numa imagem. Devolve (imagem, centro em px).

    opacidade < 1 dá um saco translúcido; transparente=True, um saco que só se
    vê pelo contorno e pelos reflexos.
    """
    altura, largura_img = tamanho
    cx = largura_img / 2 + 20 if cx is None else cx
    cy = altura / 2 - 15 if cy is None else cy
    img = fundo(altura, largura_img, rng, **kw_fundo)
    a = alfa_retangulo(altura, largura_img, cx, cy, comprimento * ppm, largura * ppm, angulo)[..., None]
    if sombra:  # luz da esquerda: sombra deslocada para a direita
        s = alfa_retangulo(altura, largura_img, cx + 3 * ppm, cy + ppm, comprimento * ppm, largura * ppm, angulo)
        img = img * (1 - 0.35 * s[..., None] * (1 - a))
    if transparente:
        corpo = img * 0.96 + 4  # quase igual ao fundo por dentro
        borda = cv2.morphologyEx((a[..., 0] > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        img = img * (1 - a) + corpo * a
        img[borda > 0] += 45  # o contorno do plástico brilha
        for k in range(3):  # reflexos
            p0 = (int(cx - 150 + 120 * k), int(cy - 40))
            cv2.line(img, p0, (p0[0] + 60, p0[1] + 70), (175, 175, 175), 3)
    else:
        img = img * (1 - a * opacidade) + np.array(cor, np.float32) * a * opacidade
    return np.clip(img, 0, 255).astype(np.uint8), (cx, cy)


def cena_forma(rng: np.random.Generator, poligono_mm, angulo: float = 20.0, cor=CORES["vermelho"],
               cx: float = 640, cy: float = 480, ppm: float = 5.0,
               tamanho: tuple[int, int] = (1000, 1250)) -> np.ndarray:
    """Um saco com a forma dada (polígono em mm, no referencial do saco), rodado e colocado na imagem."""
    altura, largura = tamanho
    img = fundo(altura, largura, rng, gradiente=50, ruido=2.5)
    a = math.radians(angulo)
    pts = [(cx + ppm * (x * math.cos(a) - y * math.sin(a)), cy + ppm * (x * math.sin(a) + y * math.cos(a)))
           for x, y in poligono_mm]
    alfa = alfa_poligono(altura, largura, pts)[..., None]
    img = img * (1 - alfa) + np.array(cor, np.float32) * alfa
    return np.clip(img, 0, 255).astype(np.uint8)


class Linha:
    """Tapete a andar com sacos a passar da esquerda para a direita, filmado a FPS constantes."""

    PPM, FPS, VELOCIDADE = 2.0, 24.0, 500.0  # px/mm, imagens/s, mm/s
    CAMPO = (250, 200)  # campo de visão (mm): largura, altura
    CORES = [CORES[n] for n in ("vermelho", "azul", "branco", "verde", "preto", "amarelo")]
    ANGULOS = [5, -8, 3, 0, -4, 7]
    YS = [100, 95, 106, 100, 92, 104]

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.base = fundo(int(self.CAMPO[1] * self.PPM), int(self.CAMPO[0] * self.PPM), rng, gradiente=50, ruido=0)

    def imagem(self, sacos) -> np.ndarray:
        """sacos: lista de (centro_x_mm, centro_y_mm, angulo, cor)."""
        altura, largura = self.base.shape[:2]
        img = self.base + self.rng.normal(0, 2.5, self.base.shape).astype(np.float32)
        for cx, cy, ang, cor in sacos:
            a = alfa_retangulo(altura, largura, cx * self.PPM, cy * self.PPM,
                               COMPRIMENTO * self.PPM, LARGURA * self.PPM, ang)[..., None]
            img = img * (1 - a) + np.array(cor, np.float32) * a
        return np.clip(img, 0, 255).astype(np.uint8)

    def gerar(self, n_sacos: int = 6, passo_mm: float = 250.0, vazio_s: float = 0.6,
              encostados: bool = False) -> tuple[list[np.ndarray], list[bool]]:
        """Imagens da linha e, para cada uma, se um sensor no centro do campo está tapado.

        Com passo_mm 250 ficam 100 mm entre sacos e a ponta do seguinte já está à
        vista quando o anterior está centrado.
        """
        campo = self.CAMPO[0]
        duracao = vazio_s + (n_sacos * passo_mm + campo + COMPRIMENTO) / self.VELOCIDADE
        imagens, sensor = [], []
        for k in range(int(duracao * self.FPS)):
            t = k / self.FPS
            sacos, tapado = [], False
            for i in range(n_sacos):
                frente = (t - vazio_s) * self.VELOCIDADE - i * (COMPRIMENTO if encostados else passo_mm)
                cx = frente - COMPRIMENTO / 2
                if -COMPRIMENTO < cx < campo + COMPRIMENTO:
                    sacos.append((cx, self.YS[i % 6], self.ANGULOS[i % 6], self.CORES[i % 6]))
                if abs(cx - campo / 2) < COMPRIMENTO / 2:
                    tapado = True
            imagens.append(self.imagem(sacos))
            sensor.append(tapado)
        return imagens, sensor

    def capturas(self, imagens, inicio: float = 1000.0, prefixo: str = "linha") -> list[Captura]:
        return [Captura(img, f"{prefixo}#{i + 1}", inicio + i / self.FPS) for i, img in enumerate(imagens)]


def diferenca_angulo(a: float, b: float) -> float:
    """Diferença entre duas orientações (graus), ignorando o sentido: 0..90."""
    d = (a - b) % 180
    return min(d, 180 - d)
