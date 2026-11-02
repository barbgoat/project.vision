"""M1 Aquisição, pré-processamento: da imagem bruta do sensor à imagem BGR 8 bits corrigida.

Tudo o que depende da câmara fica aqui: o resto do projeto recebe sempre BGR uint8,
sem distorção da lente e (com calibração) já no espaço métrico.

    debayer(bruta, formato)       Bayer (RG, BG, GB, GR) ou Mono8 → BGR (OpenCV); BGR8 passa como está
    mosaico_bayer_rg(bgr)         o inverso: o que o sensor da câmara veria (para simular)
    Calibracao, carregar_calibracao, guardar_calibracao
                                  matriz da câmara, coeficientes de distorção e homografia
                                  para o plano do tapete, num ficheiro .npz (aquisicao.calibracao)
    corrigir(img, calibracao)     tira a distorção da lente e leva ao plano do tapete
    preparar(bruta, formato, cal) debayer + corrigir
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from nucleo.config import RAIZ


# Formato de uma camada (GenICam PixelFormat) → conversão do OpenCV para BGR.
# BayerRG8: mosaico RGGB (a linha de cima começa em vermelho, verde); os outros, o mesmo
# com as cores pela ordem do nome. Mono8: cinzento, repetido nos três canais.
_CONVERSOES = {
    "BayerRG8": cv2.COLOR_BayerRGGB2BGR,
    "BayerBG8": cv2.COLOR_BayerBGGR2BGR,
    "BayerGB8": cv2.COLOR_BayerGBRG2BGR,
    "BayerGR8": cv2.COLOR_BayerGRBG2BGR,
    "Mono8": cv2.COLOR_GRAY2BGR,
}


def debayer(bruta: np.ndarray, formato: str) -> np.ndarray:
    """A imagem do sensor em BGR uint8.

    Bayer…8 e Mono8: uma camada uint8; BGR8: já está em BGR (a câmara fez a conversão).
    ValueError noutro formato.
    """
    if formato in _CONVERSOES:
        if bruta.ndim != 2 or bruta.dtype != np.uint8:
            raise ValueError(f"{formato} espera uma camada uint8, chegou {bruta.dtype} {bruta.shape}")
        return cv2.cvtColor(bruta, _CONVERSOES[formato])
    if formato == "BGR8":
        if bruta.ndim != 3 or bruta.shape[2] != 3 or bruta.dtype != np.uint8:
            raise ValueError(f"BGR8 espera altura × largura × 3 uint8, chegou {bruta.dtype} {bruta.shape}")
        return bruta
    raise ValueError(f"formato de píxel {formato!r} não suportado no pré-processamento "
                     f"(usar BGR8, Mono8 ou {', '.join(k for k in _CONVERSOES if k != 'Mono8')})")


def mosaico_bayer_rg(bgr: np.ndarray) -> np.ndarray:
    """O mosaico RGGB que um sensor BayerRG8 daria para esta cena (uma camada uint8)."""
    mosaico = np.empty(bgr.shape[:2], np.uint8)
    mosaico[0::2, 0::2] = bgr[0::2, 0::2, 2]  # R
    mosaico[0::2, 1::2] = bgr[0::2, 1::2, 1]  # G
    mosaico[1::2, 0::2] = bgr[1::2, 0::2, 1]  # G
    mosaico[1::2, 1::2] = bgr[1::2, 1::2, 0]  # B
    return mosaico


@dataclass(frozen=True)
class Calibracao:
    """A calibração de uma montagem (câmara fixa): lente e plano do tapete.

    matriz e distorcao: os do cv2.calibrateCamera (xadrez). homografia: da imagem já sem
    distorção para o plano do tapete a px_por_mm (None = só tira a distorção);
    tamanho: (largura, altura) da imagem corrigida. id: identifica a calibração (registos).
    """

    id: str
    matriz: np.ndarray  # 3×3
    distorcao: np.ndarray  # k1, k2, p1, p2[, k3...]
    px_por_mm: float | None = None
    homografia: np.ndarray | None = None  # 3×3
    tamanho: tuple[int, int] | None = None  # (largura, altura) depois da homografia


def carregar_calibracao(caminho: str | Path) -> Calibracao:
    """A calibração gravada num .npz (caminho relativo à raiz do projeto ou absoluto)."""
    caminho = Path(caminho)
    caminho = caminho if caminho.is_absolute() else RAIZ / caminho
    if not caminho.is_file():
        raise FileNotFoundError(f"aquisicao.calibracao não encontrada: {caminho}")
    dados = np.load(caminho)
    falta = [c for c in ("id", "matriz", "distorcao") if c not in dados]
    if falta:
        raise ValueError(f"{caminho}: a calibração não tem {', '.join(falta)}")
    homografia = dados["homografia"] if "homografia" in dados else None
    tamanho = tuple(int(v) for v in dados["tamanho"]) if "tamanho" in dados else None
    if homografia is not None and tamanho is None:
        raise ValueError(f"{caminho}: com homografia é preciso o tamanho (largura, altura) da imagem corrigida")
    return Calibracao(str(dados["id"]), dados["matriz"].astype(np.float64), dados["distorcao"].astype(np.float64),
                      float(dados["px_por_mm"]) if "px_por_mm" in dados else None, homografia, tamanho)


def guardar_calibracao(caminho: str | Path, calibracao: Calibracao) -> None:
    """Grava a calibração num .npz (o formato que carregar_calibracao lê)."""
    dados = {"id": calibracao.id, "matriz": calibracao.matriz, "distorcao": calibracao.distorcao}
    if calibracao.px_por_mm is not None:
        dados["px_por_mm"] = calibracao.px_por_mm
    if calibracao.homografia is not None:
        dados["homografia"], dados["tamanho"] = calibracao.homografia, np.array(calibracao.tamanho)
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    np.savez(caminho, **dados)


_MAPAS: dict[tuple[str, tuple[int, int]], tuple[np.ndarray, np.ndarray]] = {}  # (id, tamanho) → mapas


def corrigir(img: np.ndarray, calibracao: Calibracao) -> np.ndarray:
    """A imagem sem a distorção da lente e, com homografia, levada ao plano do tapete."""
    altura, largura = img.shape[:2]
    chave = (calibracao.id, (largura, altura))
    if chave not in _MAPAS:
        _MAPAS[chave] = cv2.initUndistortRectifyMap(calibracao.matriz, calibracao.distorcao, None, calibracao.matriz,
                                                    (largura, altura), cv2.CV_32FC1)
    sem_distorcao = cv2.remap(img, *_MAPAS[chave], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if calibracao.homografia is None:
        return sem_distorcao
    return cv2.warpPerspective(sem_distorcao, calibracao.homografia, calibracao.tamanho, flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)


def preparar(bruta: np.ndarray, formato: str, calibracao: Calibracao | None = None) -> np.ndarray:
    """Imagem bruta do sensor → BGR uint8 corrigida (o que o resto do projeto recebe)."""
    bgr = debayer(bruta, formato)
    return bgr if calibracao is None else corrigir(bgr, calibracao)
