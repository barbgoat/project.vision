"""Primeira versão simples (só OpenCV clássico): adquirir, segmentar, detetar e medir sacos em PÍXEIS.

Serve para validar a aquisição e a deteção antes da medição em mm. Não converte para mm:
isso só depois de calibrar a câmara (calibrateCamera + undistort) e a escala na montagem.

    python ferramentas/detetar_simples.py --camara 0
    python ferramentas/detetar_simples.py --pasta dataset/linha/dimensao
    python ferramentas/detetar_simples.py --camara 0 --hsv 90 50 50 130 255 255   (segmentar por cor)
    python ferramentas/detetar_simples.py --calibrar pasta_xadrez --xadrez 9 6      (gera calibracao.npz)
    python ferramentas/detetar_simples.py --camara 0 --calibracao calibracao.npz    (com a lente corrigida)

Teclas: q ou Esc sai; qualquer outra passa à imagem seguinte (no modo pasta).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


# --- 1. Aquisição ----------------------------------------------------------------------------

def imagens_da_camara(indice: int) -> Iterator[np.ndarray]:
    """Imagens em tempo real de uma câmara (cv2.VideoCapture)."""
    camara = cv2.VideoCapture(indice)
    if not camara.isOpened():
        raise RuntimeError(f"câmara {indice} não abriu")
    try:
        while True:
            ok, img = camara.read()
            if not ok:
                return
            yield img
    finally:
        camara.release()


def imagens_da_pasta(pasta: Path) -> Iterator[np.ndarray]:
    """As imagens de uma pasta (lidas com imdecode: aguenta acentos no caminho)."""
    for f in sorted(p for p in pasta.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")):
        img = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            yield img


# --- 2. Pré-processamento e segmentação -----------------------------------------------------

def segmentar(img: np.ndarray, hsv: tuple[int, ...] | None = None) -> np.ndarray:
    """Máscara binária (255 = saco).

    Sem hsv: escala de cinzentos (cvtColor) + desfoque (GaussianBlur) + limiar automático
    de Otsu (threshold); fica "saco" a classe que menos toca na borda da imagem (o fundo
    rodeia o saco). Com hsv = (h, s, v mínimos, h, s, v máximos): os píxeis nessa gama (inRange).
    """
    suave = cv2.GaussianBlur(img, (5, 5), 0)
    if hsv is not None:
        return cv2.inRange(cv2.cvtColor(suave, cv2.COLOR_BGR2HSV), np.array(hsv[:3]), np.array(hsv[3:]))
    cinza = cv2.cvtColor(suave, cv2.COLOR_BGR2GRAY)
    _, mascara = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    borda = np.concatenate([mascara[0], mascara[-1], mascara[:, 0], mascara[:, -1]])
    return cv2.bitwise_not(mascara) if borda.mean() > 127 else mascara


def limpar(mascara: np.ndarray, tamanho: int = 7) -> np.ndarray:
    """Abertura (tira pontos soltos) e fecho (tapa falhas) da máscara (morphologyEx)."""
    nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tamanho, tamanho))
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, nucleo)
    return cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, nucleo, iterations=2)


# --- 3. Contornos e medida em píxeis ----------------------------------------------------------

def detetar(mascara: np.ndarray, area_min_px: float) -> list[tuple[np.ndarray, tuple]]:
    """(contorno, retângulo mínimo orientado) de cada objeto com área ≥ area_min_px.

    findContours dá os contornos exteriores; contourArea filtra o ruído; minAreaRect dá o
    retângulo rodado ((cx, cy), (lado1, lado2), ângulo) que envolve cada saco.
    """
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [(c, cv2.minAreaRect(c)) for c in contornos if cv2.contourArea(c) >= area_min_px]


def medidas_px(retangulo: tuple) -> tuple[float, float]:
    """(comprimento, largura) em PÍXEIS: o lado maior e o menor do retângulo orientado."""
    (_, _), (a, b), _ = retangulo
    return max(a, b), min(a, b)


def desenhar(img: np.ndarray, sacos: list[tuple[np.ndarray, tuple]]) -> np.ndarray:
    """O contorno (verde), o retângulo (boxPoints, amarelo) e as medidas em px (putText)."""
    saida = img.copy()
    for contorno, retangulo in sacos:
        cv2.drawContours(saida, [contorno], -1, (0, 255, 0), 2)
        cv2.drawContours(saida, [np.int32(cv2.boxPoints(retangulo))], -1, (0, 255, 255), 2)
        comprimento, largura = medidas_px(retangulo)
        x, y = np.int32(retangulo[0])
        cv2.putText(saida, f"{comprimento:.0f} x {largura:.0f} px", (x - 80, y), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 0, 255), 2)
    return saida


# --- 4. Calibração da lente (prepara a medição em mm; não dá a escala) -----------------------

def calibrar(pasta: Path, cantos: tuple[int, int], destino: Path) -> None:
    """Matriz da câmara e distorção da lente a partir de fotos de um xadrez (calibrateCamera).

    cantos: cantos interiores do xadrez (colunas, linhas). Grava destino (.npz).
    """
    modelo = np.zeros((cantos[0] * cantos[1], 3), np.float32)
    modelo[:, :2] = np.mgrid[:cantos[0], :cantos[1]].T.reshape(-1, 2)
    pontos_3d, pontos_2d, tamanho = [], [], None
    for img in imagens_da_pasta(pasta):
        cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ok, achados = cv2.findChessboardCorners(cinza, cantos)
        if ok:
            achados = cv2.cornerSubPix(cinza, achados, (11, 11), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            pontos_3d.append(modelo)
            pontos_2d.append(achados)
            tamanho = cinza.shape[::-1]
    if len(pontos_2d) < 3:
        raise RuntimeError(f"só {len(pontos_2d)} fotos com o xadrez encontrado (são precisas ≥ 3, melhor 10+)")
    erro, matriz, distorcao, _, _ = cv2.calibrateCamera(pontos_3d, pontos_2d, tamanho, None, None)
    np.savez(destino, matriz=matriz, distorcao=distorcao)
    print(f"Calibração em {destino}: erro de reprojeção {erro:.2f} px ({len(pontos_2d)} fotos)")


def corrigir_lente(img: np.ndarray, calibracao: dict) -> np.ndarray:
    """A imagem sem a distorção da lente (undistort)."""
    return cv2.undistort(img, calibracao["matriz"], calibracao["distorcao"])


# --- Programa -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camara", type=int, help="índice da câmara (0, 1...)")
    parser.add_argument("--pasta", type=Path, help="pasta com imagens")
    parser.add_argument("--hsv", type=int, nargs=6, help="segmentar por cor: h s v mínimos, h s v máximos")
    parser.add_argument("--area-min", type=float, default=5000, help="área mínima de um saco (px²)")
    parser.add_argument("--calibracao", type=Path, help="ficheiro .npz de --calibrar (corrige a lente)")
    parser.add_argument("--calibrar", type=Path, help="pasta com fotos do xadrez: grava calibracao.npz")
    parser.add_argument("--xadrez", type=int, nargs=2, default=(9, 6), help="cantos interiores do xadrez")
    args = parser.parse_args()

    if args.calibrar:
        calibrar(args.calibrar, tuple(args.xadrez), Path("calibracao.npz"))
        return
    if (args.camara is None) == (args.pasta is None):
        parser.error("indica --camara ou --pasta")
    imagens = imagens_da_camara(args.camara) if args.pasta is None else imagens_da_pasta(args.pasta)
    calibracao = dict(np.load(args.calibracao)) if args.calibracao else None
    espera = 1 if args.pasta is None else 0  # câmara: contínuo; pasta: uma imagem de cada vez

    for img in imagens:
        if calibracao is not None:
            img = corrigir_lente(img, calibracao)
        mascara = limpar(segmentar(img, args.hsv))
        sacos = detetar(mascara, args.area_min)
        for _, retangulo in sacos:
            print("saco: %.1f x %.1f px" % medidas_px(retangulo))
        escala = min(1.0, 900 / max(img.shape[:2]))  # só para caber no ecrã
        cv2.imshow("sacos (medidas em px)", cv2.resize(desenhar(img, sacos), None, fx=escala, fy=escala))
        cv2.imshow("mascara", cv2.resize(mascara, None, fx=escala, fy=escala))
        if cv2.waitKey(espera) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
