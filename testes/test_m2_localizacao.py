"""M2 Localização: encontrar, medir e endireitar o saco em cenas sintéticas (5 px/mm, luz rasante, ruído)."""

from __future__ import annotations

import dataclasses
import math
import time

import cv2
import numpy as np
import pytest

import mestre as m
from modulos.m2_localizacao import Localizador
from nucleo.config import Config
from nucleo.contrato import Estado, SemSaco
from nucleo.imagens import guardar_imagem
from testes.apoio import CORES, alfa_retangulo, cena_saco, com_acentos, diferenca_angulo, fundo

PPM = 5.0
CFG = Config.de_dict({"geral": {"px_por_mm": PPM}})


@pytest.fixture
def rng():
    return np.random.default_rng(7)


@pytest.fixture(scope="module")
def localizador():
    return Localizador(CFG)


def verificar_medidas(localizador, img, centro, comprimento=150, largura=55, angulo=23.0, tol_mm=1.0, tol_ang=1.0):
    saco = localizador.localizar(img)
    assert abs(saco.comprimento_mm - comprimento) <= tol_mm, f"comprimento {saco.comprimento_mm:.2f}"
    assert abs(saco.largura_mm - largura) <= tol_mm, f"largura {saco.largura_mm:.2f}"
    assert diferenca_angulo(saco.angulo_graus, angulo) <= tol_ang, f"ângulo {saco.angulo_graus:.2f}"
    assert math.dist(saco.centro_px, centro) / PPM <= tol_mm, "centro"
    return saco


@pytest.mark.parametrize("cor", CORES.values(), ids=CORES.keys())
def test_sacos_opacos_de_qualquer_cor(localizador, rng, cor):
    img, centro = cena_saco(rng, cor=cor)
    verificar_medidas(localizador, img, centro, tol_mm=0.5, tol_ang=0.2)


@pytest.mark.parametrize("angulo", [-89, -60, -30, -10, -2, 0, 5, 30, 45, 75, 89, 90])
def test_qualquer_rotacao(localizador, rng, angulo):
    img, centro = cena_saco(rng, cor=CORES["azul"], angulo=angulo)
    verificar_medidas(localizador, img, centro, angulo=angulo, tol_mm=0.5, tol_ang=0.2)


@pytest.mark.parametrize("cx, cy", [(300, 250), (950, 750), (625, 500)])
def test_qualquer_posicao(localizador, rng, cx, cy):
    img, centro = cena_saco(rng, cx=cx, cy=cy, angulo=-15, comprimento=110, largura=40)
    verificar_medidas(localizador, img, centro, comprimento=110, largura=40, angulo=-15, tol_mm=0.5)


@pytest.mark.parametrize("cor, opacidade, angulo", [((230, 200, 150), 0.4, 12), ((200, 200, 200), 0.25, -35)],
                         ids=["azul_claro_40", "esbranquicado_25"])
def test_translucidos(localizador, rng, cor, opacidade, angulo):
    img, centro = cena_saco(rng, cor=cor, opacidade=opacidade, angulo=angulo)
    verificar_medidas(localizador, img, centro, angulo=angulo, tol_mm=0.5, tol_ang=0.2)


def test_transparente_so_contorno_e_reflexos(localizador, rng):
    img, centro = cena_saco(rng, transparente=True, angulo=17)
    verificar_medidas(localizador, img, centro, angulo=17, tol_mm=1.5)


@pytest.mark.parametrize("caso, motivo", [("vazio", "nada difere"), ("na_borda", "borda"),
                                          ("pequeno", "fora dos limites")])
def test_sem_saco_diz_porque(localizador, rng, caso, motivo):
    if caso == "vazio":
        img = fundo(1000, 1250, rng).clip(0, 255).astype(np.uint8)
    elif caso == "na_borda":
        img = cena_saco(rng, cx=1180)[0]
    else:
        img = cena_saco(rng, comprimento=40, largura=20)[0]
    with pytest.raises(SemSaco, match=motivo):
        localizador.localizar(img)


# --- Foto de referência do fundo ----------------------------------------------------------

@pytest.fixture
def fundo_com_textura(rng):
    referencia = fundo(1000, 1250, rng, textura=True)
    a = alfa_retangulo(1000, 1250, 645, 485, 150 * PPM, 55 * PPM, 23)[..., None]
    img = np.clip(referencia * (1 - a * 0.5) + np.array((200, 200, 200), np.float32) * a * 0.5, 0, 255).astype(np.uint8)
    foto_vazia = np.clip(referencia + rng.normal(0, 2, referencia.shape), 0, 255).astype(np.uint8)
    return img, foto_vazia


def _com_referencia(caminho):
    return dataclasses.replace(CFG, localizacao=dataclasses.replace(CFG.localizacao, fundo_referencia=str(caminho)))


def test_fundo_com_textura_com_foto_de_referencia(fundo_com_textura, tmp_path):
    pasta = com_acentos(tmp_path)
    img, foto_vazia = fundo_com_textura
    guardar_imagem(pasta / "fundo vazio ç.png", foto_vazia)
    saco = Localizador(_com_referencia(pasta / "fundo vazio ç.png")).localizar(img)
    assert abs(saco.comprimento_mm - 150) < 1.5 and abs(saco.largura_mm - 55) < 1.5


def test_referencia_de_outro_tamanho_da_erro(fundo_com_textura, tmp_path):
    pasta = com_acentos(tmp_path)
    img, foto_vazia = fundo_com_textura
    guardar_imagem(pasta / "fundo.png", foto_vazia)
    with pytest.raises(ValueError, match="mesma câmara"):
        Localizador(_com_referencia(pasta / "fundo.png")).localizar(cv2.resize(img, (1000, 800)))


def test_referencia_em_falta_da_erro_no_arranque():
    with pytest.raises(FileNotFoundError):
        Localizador(_com_referencia("nao_existe.png"))


# --- Recorte normalizado ------------------------------------------------------------------

def test_recorte_normalizado(localizador, rng):
    img, _ = cena_saco(rng, angulo=37)
    saco = localizador.localizar(img)
    altura, largura = saco.mascara.shape
    ys, xs = np.nonzero(saco.mascara)
    assert abs((xs.max() - xs.min() + 1) - 150 * PPM) <= 3, "saco na horizontal"
    assert abs((ys.max() - ys.min() + 1) - 55 * PPM) <= 3
    assert abs((xs.max() + xs.min()) / 2 - (largura - 1) / 2) <= 1.5, "saco centrado"
    assert abs((ys.max() + ys.min()) / 2 - (altura - 1) / 2) <= 1.5
    folga = CFG.localizacao.folga_mm
    assert abs(largura - (150 + 2 * folga) * PPM) <= 2 and abs(altura - (55 + 2 * folga) * PPM) <= 2

    p = CFG.localizacao
    yu, xu = np.nonzero(saco.mascara_util)
    assert abs((xu.max() - xu.min() + 1) - (150 - 2 * p.margem_extremidades_mm) * PPM) <= 3, "zona útil sem as pontas"
    assert abs((yu.max() - yu.min() + 1) - (55 - 2 * p.margem_borda_mm) * PPM) <= 3, "zona útil sem as bordas"
    assert np.abs(saco.imagem[altura // 2, largura // 2].astype(int) - CORES["vermelho"]).max() < 15
    centro_no_recorte = saco.matriz @ np.array([*saco.centro_px, 1.0])
    assert np.allclose(centro_no_recorte, ((largura - 1) / 2, (altura - 1) / 2), atol=1e-6)


# --- Com o Mestre -------------------------------------------------------------------------

def test_mestre_entrega_o_saco_so_de_leitura_e_os_intermedios(rng):
    img, _ = cena_saco(rng, angulo=37)
    r = m.Mestre(CFG, apenas=[]).inspecionar(img, guardar_intermedios=True)
    loc = r.relatorio("localizacao")
    assert loc.estado is Estado.OK and abs(loc.medidas["comprimento_mm"] - 150) < 1
    assert "limiar_delta_e" in loc.medidas
    assert set(loc.intermedios) == {"diferenca", "segmentacao", "recorte"}
    assert not r.saco.imagem.flags.writeable and not r.saco.mascara_util.flags.writeable


def test_mestre_sem_saco_com_motivo(rng):
    r = m.Mestre(CFG, apenas=[]).inspecionar(cena_saco(rng, cx=1180)[0])
    assert r.estado is Estado.SEM_SACO and "borda" in r.mensagem


def test_tempo_numa_imagem_da_blackfly(rng):
    """5 MP, ~8 px/mm: tem de caber bem dentro dos 500 ms por saco (limite largo: a máquina pode estar ocupada)."""
    cfg = Config.de_dict({"geral": {"px_por_mm": 8.0}})
    localizador = Localizador(cfg)
    img, _ = cena_saco(rng, cor=CORES["azul"], ppm=8.0, tamanho=(2048, 2448), angulo=28)
    tempos = []
    for _ in range(3):
        inicio = time.perf_counter()
        saco = localizador.localizar(img)
        tempos.append((time.perf_counter() - inicio) * 1000)
    assert abs(saco.comprimento_mm - 150) < 1 and abs(saco.largura_mm - 55) < 1
    assert min(tempos) < 400, f"{min(tempos):.0f} ms"
