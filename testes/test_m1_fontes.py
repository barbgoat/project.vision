"""M1 Aquisição, fontes: de onde vêm as imagens (pasta, vídeo), com acentos nos caminhos."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from modulos.m1_aquisicao import FonteVideo, criar_fonte
from nucleo.config import Config
from nucleo.imagens import guardar_imagem, ler_imagem

IMG = np.zeros((40, 60, 3), np.uint8)
IMG[:, :30] = (255, 0, 0)


def test_ler_e_guardar_imagem_com_acentos(pasta_com_acentos):
    caminho = pasta_com_acentos / "ok" / "saco ã 1.png"
    guardar_imagem(caminho, IMG)
    assert np.array_equal(ler_imagem(caminho), IMG)


def test_fonte_pasta_le_subpastas_e_salta_imagens_corrompidas(pasta_com_acentos):
    guardar_imagem(pasta_com_acentos / "ok" / "saco ã 1.png", IMG)
    guardar_imagem(pasta_com_acentos / "rugas" / "saco é 2.png", IMG)
    (pasta_com_acentos / "rugas" / "corrompida.jpg").write_bytes(b"xx")
    with criar_fonte("pasta", Config(), pasta=pasta_com_acentos) as fonte:
        n, capturas = len(fonte), list(fonte)
    assert n == 3 and len(capturas) == 2
    assert all(np.array_equal(c.imagem, IMG) for c in capturas)


def test_fonte_video_com_acentos(pasta_com_acentos):
    temporario = pasta_com_acentos.parent / "v.mp4"  # o VideoWriter não aceita acentos
    escritor = cv2.VideoWriter(str(temporario), cv2.VideoWriter_fourcc(*"mp4v"), 24, (64, 48))
    for k in range(10):
        escritor.write(np.full((48, 64, 3), 20 * k, np.uint8))
    escritor.release()
    video = pasta_com_acentos / "vídeo linha ç.mp4"
    temporario.rename(video)
    fonte = FonteVideo(video)
    try:
        assert sum(1 for _ in fonte) == 10
    finally:
        fonte.fechar()


@pytest.mark.parametrize("tipo", ["webcam", "spinnaker"])
def test_fontes_por_implementar(tipo):
    with pytest.raises(NotImplementedError):
        criar_fonte(tipo, Config())
