"""Utilitários de imagem partilhados por vários módulos.

Não são escravos: são ferramentas comuns. Os módulos usam-nas em vez de terem
cada um a sua cópia.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import cv2
import numpy as np

from nucleo.config import Canal

# --- Ficheiros (caminhos com espaços e acentos) -------------------------------------

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


# --- Operações comuns (usadas pelo M5 Relevo e pelo M6 Manchas) ------------------------

def extrair_canal(img: np.ndarray, canal: Canal) -> np.ndarray:
    """Extrai um canal de uma imagem BGR.

    Recebe:
        img: imagem BGR uint8.
        canal: "cinzento", um canal BGR ("b", "g", "r") ou HSV ("h", "s", "v").
    Devolve:
        Imagem float32 de um só canal, com valores de 0 a 255.
    O que vai fazer:
        cv2.cvtColor para cinzento ou HSV, ou indexar o canal BGR; converter
        para float32.
    """
    raise NotImplementedError


def contraste_relativo(canal: np.ndarray, mascara: np.ndarray, sigma_px: float) -> np.ndarray:
    """Remove as variações lentas de iluminação: quanto cada píxel é mais claro/escuro do que a vizinhança.

    Recebe:
        canal: imagem float32 de um só canal (saída de extrair_canal).
        mascara: zona do saco, uint8 0/255; o que está fora não entra nas contas.
        sigma_px: σ do desfoque que estima a iluminação; tem de ser bem maior
            do que os defeitos a detetar.
    Devolve:
        Imagem float32: ≈0 onde a superfície é uniforme, +0,2 num sítio 20 %
        mais claro do que a vizinhança, −0,2 num 20 % mais escuro; 0 fora da máscara.
    O que vai fazer:
        1. Estimar a iluminação com um desfoque gaussiano normalizado pela
           máscara, para o tapete não "entrar" no saco junto à borda:
           fundo = G∗(canal·m) / G∗m. Para ganhar tempo, pode ser feito numa
           versão reduzida da imagem.
        2. Contraste = canal / fundo − 1 (dividir compensa melhor do que
           subtrair, porque a iluminação multiplica a refletância).
        3. Pôr a 0 fora da máscara.
    """
    raise NotImplementedError
