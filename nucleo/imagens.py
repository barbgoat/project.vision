"""Utilitários de imagem partilhados por vários módulos.

Não são escravos: são ferramentas comuns. Os módulos usam-nas em vez de terem
cada um a sua cópia.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

import cv2
import numpy as np


log = logging.getLogger(__name__)

# --- Ficheiros (caminhos com espaços e acentos) -------------------------------------

EXTENSOES_IMAGEM = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


def e_anotada(caminho: str | Path) -> bool:
    """A imagem foi desenhada pelo programa (anotada, etapas da depuração, painel), não é a da câmara?

    Pelo nome (…_anotada, painel) ou por estar numa pasta de resultados (com registo.csv), onde só os
    …_original.png e os 00_original.png da depuração são da câmara. As anotadas têm o contorno
    desenhado em cima da borda do saco: analisá-las dá medidas erradas.
    """
    p = Path(caminho)
    nome = p.stem.lower()
    if nome.endswith("_original") or nome == "00_original":
        return False
    if nome.endswith("anotada") or nome == "painel":
        return True
    return any((pai / "registo.csv").is_file() for pai in list(p.parents)[:3])


def listar_imagens(pasta: str | Path) -> list[Path]:
    """As imagens de uma pasta e das subpastas, por ordem alfabética do caminho (para as ferramentas),
    sem as anotadas (e_anotada): só o que veio da câmara."""
    imagens, anotadas = [], 0
    for p in sorted(Path(pasta).rglob("*")):
        if p.suffix.lower() in EXTENSOES_IMAGEM and p.is_file():
            if e_anotada(p):
                anotadas += 1
            else:
                imagens.append(p)
    if anotadas:
        log.warning("%d imagens anotadas (com desenhos por cima) deixadas de fora em %s; analisar os originais.",
                    anotadas, pasta)
    return imagens


def ler_original(caminho: str | Path) -> np.ndarray | None:
    """ler_imagem, mas recusa uma imagem anotada (e_anotada): as ferramentas de análise usam esta."""
    if e_anotada(caminho):
        raise ValueError(f"{caminho} é uma imagem anotada (com desenhos por cima): usar a original "
                         "(…_original.png ou 00_original.png da depuração)")
    return ler_imagem(caminho)


def limites_roi(forma: tuple[int, ...], roi: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) da ROI dentro de uma imagem com esta forma (altura, largura...); None = toda."""
    alto, largo = forma[:2]
    if roi is None:
        return 0, 0, largo, alto
    x, y, largura, altura = roi
    x0, y0 = min(max(x, 0), largo - 1), min(max(y, 0), alto - 1)
    return x0, y0, min(x + largura, largo), min(y + altura, alto)


def recortar(img: np.ndarray, roi: tuple[int, int, int, int] | None) -> np.ndarray:
    """Só a ROI (x, y, largura, altura em px; aquisicao.roi()); a parte fora da imagem fica de fora. None = toda."""
    if roi is None:
        return img
    x0, y0, x1, y1 = limites_roi(img.shape, roi)
    return img[y0:y1, x0:x1].copy()


def ler_imagem(caminho: str | Path) -> np.ndarray | None:
    """Lê um ficheiro de imagem para BGR uint8.

    O cv2.imread não abre caminhos com caracteres não-ASCII no Windows; por isso
    lê-se o ficheiro com np.fromfile e descodifica-se com cv2.imdecode.
    Devolve None se o ficheiro não for uma imagem válida (FileNotFoundError se
    não existir).
    """
    dados = np.fromfile(caminho, dtype=np.uint8)
    if dados.size == 0:
        return None
    return cv2.imdecode(dados, cv2.IMREAD_COLOR)


def guardar_imagem(caminho: str | Path, imagem: np.ndarray, parametros: list[int] | None = None) -> None:
    """Guarda uma imagem num ficheiro, com o formato dado pela extensão (.png, .jpg...).

    Substitui o cv2.imwrite, que também falha com caminhos não-ASCII no Windows.
    Cria a pasta de destino se ainda não existir. parametros: os do cv2.imencode,
    p.ex. [cv2.IMWRITE_JPEG_QUALITY, 90] ou [cv2.IMWRITE_PNG_COMPRESSION, 1].
    """
    caminho = Path(caminho)
    ok, dados = cv2.imencode(caminho.suffix, imagem, parametros or [])
    if not ok:
        raise ValueError(f"Não foi possível codificar a imagem como '{caminho.suffix}': {caminho}")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados.tofile(caminho)


# --- Para ver e mostrar ---------------------------------------------------------------

def para_ver(imagem: np.ndarray) -> np.ndarray:
    """Converte uma imagem intermédia numa imagem BGR uint8 que se possa ver.

    - BGR uint8: fica igual;
    - máscara (bool ou uint8 de um canal): cinzento;
    - mapa numérico (float): cores do mais fraco (escuro) ao mais forte
      (amarelo), esticado entre os percentis 1 e 99 para um valor isolado
      não "apagar" o resto.
    """
    if imagem.dtype == bool:
        imagem = imagem.astype(np.uint8) * 255
    if imagem.dtype == np.uint8:
        return imagem if imagem.ndim == 3 else cv2.cvtColor(imagem, cv2.COLOR_GRAY2BGR)
    valores = imagem.astype(np.float32)
    if valores.ndim == 3:
        valores = np.linalg.norm(valores, axis=2)
    baixo, alto = np.percentile(valores, (1, 99))
    escala = np.clip((valores - baixo) / max(float(alto - baixo), 1e-6), 0, 1)
    return cv2.applyColorMap((escala * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)


def texto_ascii(texto: str) -> str:
    """Tira os acentos (o cv2.putText só desenha ASCII): "rasgão" → "rasgao", "mm²" → "mm2"."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
