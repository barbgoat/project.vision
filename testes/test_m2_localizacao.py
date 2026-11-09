"""M2 Localização: encontrar, medir e endireitar o saco em cenas sintéticas (5 px/mm, luz rasante, ruído)."""

from __future__ import annotations

import dataclasses
import json
import math
import time

import cv2
import numpy as np
import pytest

import mestre as m
from modulos.m2_localizacao import Diagnostico, Localizador, qualidade
from modulos.m2_localizacao.qualidade import MedicaoNaoFiavel
from nucleo.contrato import Estado, SemSaco
from nucleo.imagens import guardar_imagem, recortar
from testes.apoio import (CORES, alfa_retangulo, cena_saco, com_acentos, config_teste, diferenca_angulo,
                          fundo)

PPM = 5.0
CFG = config_teste(geral={"px_por_mm": PPM})


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


def _saco_claro_em_fundo_claro(rng, deriva: float = 0.0):
    """Como o saco Garnier: cinzento só 15 níveis mais escuro do que o papel, luz desigual (o fundo
    varia ~70 níveis) e um bloco de texto escuro no meio. Devolve (imagem, foto do fundo vazio)."""
    altura, largura = 1000, 1250
    y = np.linspace(-1, 1, altura)[:, None, None]
    luz = fundo(altura, largura, rng, base=(125, 125, 125), gradiente=50, ruido=0) + 20 * y
    a = alfa_retangulo(altura, largura, 645, 485, 150 * PPM, 55 * PPM, 23)[..., None]
    texto = alfa_retangulo(altura, largura, 645, 485, 100 * PPM, 30 * PPM, 23)[..., None]
    riscas = (np.sin(np.arange(largura) / 2.5) > 0.2)[None, :, None]
    img = (luz * (1 - a) + (luz - 15) * a) * (1 - 0.7 * texto * riscas)
    img = np.clip(img + deriva + rng.normal(0, 3.4, img.shape), 0, 255).astype(np.uint8)
    return img, np.clip(luz + rng.normal(0, 3.4, luz.shape), 0, 255).astype(np.uint8)


def test_saco_claro_em_fundo_claro_com_foto_do_fundo(rng, tmp_path):
    """Sem a foto do fundo, o limiar automático só vê o texto; com ela, o saco todo, e ao centésimo
    de mm mesmo com a luz mais forte do que na foto e com a ROI (a foto é a imagem inteira)."""
    img, vazio = _saco_claro_em_fundo_claro(rng)
    guardar_imagem(tmp_path / "fundo.png", vazio)
    com_ref = _com_referencia(tmp_path / "fundo.png")
    with pytest.raises(MedicaoNaoFiavel, match="quase não difere do fundo") as erro:
        Localizador(CFG).localizar(img)
    assert erro.value.saco.comprimento_mm < 120, "sem a foto do fundo mede o texto; o controlo recusa-o"
    for imagem in (img, _saco_claro_em_fundo_claro(np.random.default_rng(8), deriva=8)[0]):
        saco = Localizador(com_ref).localizar(imagem)
        assert abs(saco.comprimento_mm - 150) < 0.1 and abs(saco.largura_mm - 55) < 0.1,             (saco.comprimento_mm, saco.largura_mm)
    roi = (150, 100, 1000, 800)
    com_roi = dataclasses.replace(com_ref, aquisicao=dataclasses.replace(
        com_ref.aquisicao, roi_x_px=roi[0], roi_y_px=roi[1], roi_largura_px=roi[2], roi_altura_px=roi[3]))
    saco = Localizador(com_roi).localizar(recortar(img, roi))
    assert abs(saco.comprimento_mm - 150) < 0.1 and abs(saco.largura_mm - 55) < 0.1


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
    cfg = config_teste(geral={"px_por_mm": 8.0})
    localizador = Localizador(cfg)
    img, _ = cena_saco(rng, cor=CORES["azul"], ppm=8.0, tamanho=(2048, 2448), angulo=28)
    tempos = []
    for _ in range(3):
        inicio = time.perf_counter()
        saco = localizador.localizar(img)
        tempos.append((time.perf_counter() - inicio) * 1000)
    assert abs(saco.comprimento_mm - 150) < 1 and abs(saco.largura_mm - 55) < 1
    assert min(tempos) < 400, f"{min(tempos):.0f} ms"


# --- Segmentadores (ΔE, SAM 3, duplo) -------------------------------------------------------

from modulos.m2_localizacao import fundo as fundo_m2, segmentadores  # noqa: E402
from modulos.m2_localizacao.segmentadores import ResultadoSegmentacao, SegmentacaoIncerta  # noqa: E402
from modulos.m2_localizacao.segmentadores import sam3 as seg_sam3  # noqa: E402
from modulos.m2_localizacao.segmentadores.delta_e import SegmentadorDeltaE  # noqa: E402


class _Fixo(segmentadores.Segmentador):
    """Um segmentador que devolve sempre a mesma máscara (e o mapa de cor, como o SAM 3)."""

    def __init__(self, mascara: np.ndarray, score: float = 0.9, nome: str = "fixo", mapa=None) -> None:
        self.mascara, self.score, self.nome, self.mapa = mascara, score, nome, mapa

    def segmentar(self, img, px_por_mm, guardar_intermedios=False):
        mapa = self.mapa if self.mapa is not None else fundo_m2.lab(img, 1.0)
        return ResultadoSegmentacao(self.mascara, self.score, self.nome, 0.0, mapa)


def _verdade(rng, **kw):
    """Uma cena com o saco de 150 × 55 mm e a sua máscara verdadeira (à resolução da imagem)."""
    img, (cx, cy) = cena_saco(rng, comprimento=150, largura=55, **kw)
    return img, alfa_retangulo(*img.shape[:2], cx, cy, 150 * PPM, 55 * PPM, kw.get("angulo", 23.0)) > 0.5


def test_o_segmentador_por_omissao_e_o_delta_e(rng):
    seg = segmentadores.criar(CFG)
    assert isinstance(seg, SegmentadorDeltaE) and CFG.localizacao.segmentador == "delta_e"
    img, verdade = _verdade(rng)
    r = seg.segmentar(img, PPM)
    assert r.backend == "delta_e" and r.mascara.dtype == bool and 0.5 < r.score <= 1 and r.tempo_ms > 0
    assert segmentadores.iou(r.mascara, verdade) > 0.97, "a máscara reduzida, comparada na resolução total"


def test_contorno_subpixel_com_o_mapa_de_cor(rng):
    """Com o mapa CIELAB (o do SAM 3) as medidas saem tão certas como com o ΔE, mesmo com uma
    máscara grosseira (2 px a mais em cada lado)."""
    img, verdade = _verdade(rng, cor=CORES["azul"])
    grosseira = cv2.dilate(verdade.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    saco = Localizador(CFG, segmentador=_Fixo(grosseira)).localizar(img)
    assert abs(saco.comprimento_mm - 150) < 0.3 and abs(saco.largura_mm - 55) < 0.3, \
        (saco.comprimento_mm, saco.largura_mm)


def test_qualidade_do_contorno(rng):
    """Um contorno limpo segue as retas dos lados (RMS < 0,5 px, quase nada rejeitado); um vinco
    colado a um lado aparece nos rejeitados desse lado, e só desse."""
    img, verdade = _verdade(rng, angulo=0.0)
    diagnostico = Diagnostico()
    Localizador(CFG).localizar(img, diagnostico=diagnostico)
    assert diagnostico.medidas["contorno_rms_px"] < 0.5 and diagnostico.medidas["rejeitados_max_pct"] < 10
    ys, xs = np.nonzero(verdade)
    meio = (xs.min() + xs.max()) // 2
    com_vinco = verdade.copy()
    com_vinco[ys.min() - 15: ys.min(), meio - 60: meio + 60] = True  # 3 × 24 mm colado ao lado de cima
    diagnostico = Diagnostico()
    Localizador(CFG, segmentador=_Fixo(com_vinco)).localizar(img, diagnostico=diagnostico)
    assert diagnostico.medidas["rejeitados_cima_pct"] > 10
    assert max(diagnostico.medidas[f"rejeitados_{lado}_pct"] for lado in ("esquerda", "direita", "baixo")) < 5


def test_contorno_ideal_e_o_retangulo_medido_com_os_cantos_do_produto():
    from modulos.m2_localizacao.medicao import retangulo_arredondado
    ideal = retangulo_arredondado((300.0, 200.0), 30.0, 400.0, 150.0, 20.0)
    assert np.allclose(ideal.mean(axis=0), (300, 200), atol=0.5)
    area = cv2.contourArea(ideal.astype(np.float32))
    assert abs(area - (400 * 150 - (4 - math.pi) * 20 ** 2)) < 0.01 * area, "retângulo com cantos de raio 20"
    _, (lado1, lado2), _ = cv2.minAreaRect(ideal.astype(np.float32))
    assert sorted((lado1, lado2)) == pytest.approx([150, 400], abs=0.5)
    assert len(retangulo_arredondado((0, 0), 0, 100, 50, 0)) == 48, "raio 0: cantos vivos"


def test_contorno_que_nao_fechou_e_erro_e_nao_medidas(rng):
    """Uma parte do saco igual ao fundo deixa uma "baía" na região (a solidez cai): ERRO, não medidas."""
    img, verdade = _verdade(rng, angulo=0.0)
    ys, xs = np.nonzero(verdade)
    altura, largura = ys.max() - ys.min(), xs.max() - xs.min()
    em_c = verdade.copy()
    em_c[ys.min() + altura // 4: ys.max() - altura // 4, xs.min(): xs.min() + largura * 2 // 5] = False
    diagnostico = Diagnostico()
    with pytest.raises(SegmentacaoIncerta, match="contorno incompleto"):
        Localizador(CFG, segmentador=_Fixo(em_c)).localizar(img, diagnostico=diagnostico)
    assert diagnostico.medidas["solidez"] < 0.85
    saco = Localizador(CFG, segmentador=_Fixo(verdade)).localizar(img)  # o saco inteiro passa
    assert abs(saco.comprimento_mm - 150) < 0.3


def test_sam3_escolhe_a_maior_mascara_confiante_e_completa():
    def retangulo(x0, y0, x1, y1):
        m = np.zeros((100, 120), bool)
        m[y0:y1, x0:x1] = True
        return m
    pequena, grande, na_borda, incerta = (retangulo(10, 10, 30, 30), retangulo(20, 20, 100, 80),
                                         retangulo(0, 10, 110, 90), retangulo(5, 5, 115, 95))
    mascaras, scores = np.array([pequena, grande, na_borda, incerta]), np.array([0.95, 0.8, 0.9, 0.3])
    mascara, score = seg_sam3.escolher(mascaras, scores, 0.5, rejeitar_borda=True)
    assert np.array_equal(mascara, grande) and score == 0.8
    assert np.array_equal(seg_sam3.escolher(mascaras, scores, 0.5, rejeitar_borda=False)[0], na_borda)
    assert seg_sam3.escolher(mascaras, scores, 0.99, rejeitar_borda=True) is None
    assert seg_sam3.escolher(np.zeros((0, 100, 120), bool), np.zeros(0), 0.5, True) is None


def test_sam3_com_fake_mede_e_sem_confianca_da_erro(rng, monkeypatch):
    img, verdade = _verdade(rng, cor=CORES["verde"])
    cfg = config_teste(geral={"px_por_mm": PPM}, localizacao={"segmentador": "sam3"})
    monkeypatch.setattr(seg_sam3, "mascaras_por_texto", lambda im, texto: (verdade[None], np.array([0.92])))
    r = m.Mestre(cfg, apenas=[]).inspecionar(img)
    medidas = r.relatorio("localizacao").medidas
    assert r.relatorio("localizacao").estado is Estado.OK and medidas["score_segmentacao"] == 0.92
    assert abs(medidas["comprimento_mm"] - 150) < 0.3 and abs(medidas["largura_mm"] - 55) < 0.3, medidas

    monkeypatch.setattr(seg_sam3, "mascaras_por_texto", lambda im, texto: (verdade[None], np.array([0.2])))
    r = m.Mestre(cfg, apenas=[]).inspecionar(img)
    assert r.estado is Estado.ERRO and "segmentação incerta" in r.mensagem, r.mensagem
    monkeypatch.setattr(seg_sam3, "mascaras_por_texto", lambda im, texto: (np.zeros((0, *img.shape[:2]), bool),
                                                                            np.zeros(0)))
    assert m.Mestre(cfg, apenas=[]).inspecionar(img).estado is Estado.ERRO, "sem máscara: nunca OK"


def test_sam3_sem_fecho_nao_tapa_entalhes():
    saco = np.zeros((200, 300), bool)
    saco[50:150, 50:250] = True
    saco[50:80, 140:146] = False  # entalhe de 6 px de largura, 30 de fundo
    assert not seg_sam3.limpar(saco, 0.0)[60, 143], "sem fecho o entalhe fica"
    assert seg_sam3.limpar(saco, 15.0)[60, 143], "um fecho largo tapa-o"


def test_fecho_tapa_falhas_mas_nao_cola_manchas_do_fundo():
    """Na bancada, manchas do fundo a poucos px da aresta ficavam coladas ao saco pelo fecho (bossas de ~3 mm)."""
    from modulos.m2_localizacao import segmentacao
    mascara = np.zeros((200, 300), np.uint8)
    mascara[50:150, 50:250] = 255
    mascara[50:62, 140:146] = 0  # falha na aresta de cima: 6 px de largura, 12 de fundo
    mascara[90:100, 34:42] = 255  # mancha do fundo (0,4% da área do saco) a 8 px do lado esquerdo
    limpa = segmentacao.maior_regiao(segmentacao.limpar(mascara, 21.0, 5.0))
    assert limpa[55, 143], "a falha fica tapada"
    assert not limpa[95, 38] and not limpa[95, 46], "a mancha e a ponte até ela ficam de fora"
    assert limpa[95, 52], "o saco fica todo"


@pytest.mark.parametrize("campo, valor", [("segmentador", "yolo"), ("sam3_score_min", 1.5), ("sam3_prompt", " "),
                                          ("sam3_fecho_mm", -1.0), ("duplo_iou_min", 0.0),
                                          ("aprendido_score_min", 1.5), ("aprendido_modelo", " ")])
def test_opcoes_do_segmentador_sao_validadas(campo, valor):
    with pytest.raises(ValueError, match=campo):
        config_teste(localizacao={campo: valor})


def test_duplo_aceita_quando_coincidem_e_manda_rever_quando_nao(rng, tmp_path):
    img, verdade = _verdade(rng)
    torta = np.roll(verdade, 40, axis=1)
    duplo = segmentadores.SegmentadorDuplo(_Fixo(verdade, nome="a"), _Fixo(verdade, 0.9, "b"), 0.95, tmp_path / "rev")
    r = duplo.segmentar(img, PPM)
    assert r.medidas["iou_duplo"] == 1.0 and r.score == 0.9 and r.backend == "b+a"
    saco = Localizador(CFG, segmentador=duplo).localizar(img)
    assert abs(saco.comprimento_mm - 150) < 0.3

    duplo = segmentadores.SegmentadorDuplo(_Fixo(verdade, nome="a"), _Fixo(torta, nome="b"), 0.95, tmp_path / "rev")
    with pytest.raises(SegmentacaoIncerta, match="duvidosa"):
        duplo.segmentar(img, PPM)
    assert len(list((tmp_path / "rev").glob("*.png"))) == 1, "a foto fica para rever"


@pytest.mark.skipif(not seg_sam3.disponivel(), reason="SAM 3 não instalado (ver o README)")
def test_sam3_real_numa_foto():
    import torch
    from pathlib import Path
    from nucleo.imagens import ler_imagem
    if not torch.cuda.is_available():
        pytest.skip("SAM 3 sem GPU: lento demais para os testes")
    from nucleo.imagens import listar_imagens
    fotos = listar_imagens("dataset") if Path("dataset").is_dir() else []
    if not fotos:
        pytest.skip("sem imagens em dataset/")
    try:
        mascaras, scores = seg_sam3.mascaras_por_texto(ler_imagem(fotos[0]), "plastic bag")
    except Exception as erro:  # sem acesso aos pesos no Hugging Face
        pytest.skip(f"SAM 3 não carregou: {erro}")
    assert mascaras.shape[0] == len(scores) and (len(scores) == 0 or mascaras.shape[1:] == ler_imagem(fotos[0]).shape[:2])


# --- Segmentador aprendido (o modelo das fotos anotadas) ---------------------------------------

from modulos.m2_localizacao.segmentadores.aprendido import Modelo, reduzir  # noqa: E402


def _riscas(rng, angulo: float, cx: float, cy: float):
    """Saco vermelho de 150 × 55 mm (5 px/mm) num fundo às riscas de duas cores, e a sua máscara."""
    altura, largura = 1000, 1250
    risca = ((np.arange(largura) // 40) % 2)[None, :, None] == 0
    img = np.where(risca, np.float32((110, 115, 105)), np.float32((150, 95, 50))) + rng.normal(0, 3, (altura, largura, 3))
    a = alfa_retangulo(altura, largura, cx, cy, 150 * PPM, 55 * PPM, angulo)[..., None]
    img = img * (1 - a) + np.float32(CORES["vermelho"]) * a
    return np.clip(img, 0, 255).astype(np.uint8), a[..., 0] > 0.5


@pytest.fixture
def modelo_riscas(rng, tmp_path):
    """Um modelo que aprendeu uma foto anotada do fundo às riscas (como o anotar.py faz)."""
    img, saco = _riscas(rng, 23.0, 640, 480)
    pequena = reduzir(img)
    modelo = Modelo()
    modelo.aprender(pequena, cv2.resize(saco.astype(np.uint8), pequena.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0)
    modelo.guardar(tmp_path)
    return tmp_path / "modelo.npz"


def test_aprendido_mede_o_saco_noutra_foto(rng, modelo_riscas):
    img, _ = _riscas(rng, -30.0, 600, 520)
    cfg = config_teste(geral={"px_por_mm": PPM}, localizacao={"segmentador": "aprendido",
                                                              "aprendido_modelo": str(modelo_riscas)})
    loc = m.Mestre(cfg, apenas=[]).inspecionar(img).relatorio("localizacao")
    assert loc.estado is Estado.OK, loc.mensagem
    medidas = loc.medidas
    assert abs(medidas["comprimento_mm"] - 150) < 0.4 and abs(medidas["largura_mm"] - 55) < 0.4, medidas
    assert diferenca_angulo(medidas["angulo_graus"], -30.0) < 0.3 and medidas["score_segmentacao"] > 0.9


def test_aprendido_incerto_da_erro_e_sem_modelo_diz_como_criar(rng, modelo_riscas, tmp_path):
    img, _ = _riscas(rng, 10.0, 620, 500)
    cfg = config_teste(geral={"px_por_mm": PPM}, localizacao={"segmentador": "aprendido", "aprendido_score_min": 1.0,
                                                              "aprendido_modelo": str(modelo_riscas)})
    r = m.Mestre(cfg, apenas=[]).inspecionar(img)
    assert r.estado is Estado.ERRO and "segmentação incerta" in r.mensagem, r.mensagem
    sem = config_teste(localizacao={"segmentador": "aprendido", "aprendido_modelo": str(tmp_path / "nao.npz")})
    with pytest.raises(FileNotFoundError, match="ferramentas.anotar"):
        m.Mestre(sem)


# --- Controlo da medição (qualidade.py): a imagem deixou medir bem? --------------------------


def _ponta_direita_sem_aresta(rng):
    """Um saco certo e uma máscara 4 mm para fora na ponta direita (como uma sombra colada, ou um fundo da cor
    do saco): ali não há aresta perto da máscara e a medida fica onde a máscara a deixou (C ≈ 154 mm)."""
    img, verdade = _verdade(rng, angulo=0.0)
    ys, xs = np.nonzero(verdade)
    larga = verdade.copy()
    larga[ys.min(): ys.max() + 1, xs.max(): xs.max() + 4 * int(PPM)] = True
    return img, larga


def test_saco_limpo_passa_o_controlo_da_medicao(rng):
    img, _ = _verdade(rng, angulo=0.0)
    diagnostico = Diagnostico()
    Localizador(CFG).localizar(img, diagnostico=diagnostico)
    q = diagnostico.medidas
    assert q["aresta_min_pct"] > 95 and q["reflexos_max_pct"] == 0 and q["contraste"] > 0.5, q
    assert "luz_pct" not in q, "sem a foto do fundo vazio não há luz de referência"
    assert not qualidade.problemas(q, CFG.localizacao, PPM)


def test_ponta_sem_aresta_e_medicao_nao_fiavel_com_o_lado(rng):
    """Longe da aresta, num fundo liso, o máximo do gradiente cai num sítio qualquer da janela: o ponto
    parece "válido", mas a transição é fraca. Só conta como aresta encontrada a que é forte."""
    img, larga = _ponta_direita_sem_aresta(rng)
    diagnostico = Diagnostico()
    with pytest.raises(MedicaoNaoFiavel, match="ponta direita: aresta encontrada em só") as erro:
        Localizador(CFG, segmentador=_Fixo(larga)).localizar(img, diagnostico=diagnostico)
    q = diagnostico.medidas
    assert q["aresta_direita_pct"] < 30 and min(q[f"aresta_{lado}_pct"] for lado in ("esquerda", "cima", "baixo")) > 95
    assert abs(erro.value.saco.comprimento_mm - 154) < 0.5, "leva o saco medido, com a medida errada que evitou"
    assert len(erro.value.problemas) == 1


def test_limite_zero_nao_verifica(rng):
    img, larga = _ponta_direita_sem_aresta(rng)
    sem = config_teste(geral={"px_por_mm": PPM}, localizacao={"qualidade_aresta_min_pct": 0.0})
    saco = Localizador(sem, segmentador=_Fixo(larga)).localizar(img)
    assert abs(saco.comprimento_mm - 154) < 0.5, "sem o controlo, a medida errada passava"


def test_medicao_nao_fiavel_e_erro_no_mestre_so_com_o_motivo(rng):
    img, larga = _ponta_direita_sem_aresta(rng)
    mestre = m.Mestre(CFG, apenas=["dimensoes"])
    mestre.localizador = Localizador(CFG, segmentador=_Fixo(larga))
    r = mestre.inspecionar(img)
    loc = r.relatorio("localizacao")
    assert r.estado is Estado.ERRO and r.saco is None and loc.estado is Estado.ERRO
    assert r.mensagem.startswith("localizacao: medição não fiável: ponta direita"), r.mensagem
    assert not loc.detalhe and "escravo.py" not in r.mensagem, "não é uma falha do código: sem traceback"
    assert loc.medidas["aresta_direita_pct"] < 30, "os valores ficam no relatório (resultado.json)"


def test_luz_face_a_foto_do_fundo_vazio(rng, tmp_path):
    """Com a foto do fundo vazio, a luz do fundo compara-se com a dela: 20 % abaixo passa, 40 % não."""
    img, _ = cena_saco(rng, angulo=0.0)
    guardar_imagem(tmp_path / "fundo.png", fundo(1000, 1250, rng).clip(0, 255).astype(np.uint8))
    localizador = Localizador(_com_referencia(tmp_path / "fundo.png"))
    diagnostico = Diagnostico()
    localizador.localizar(img, diagnostico=diagnostico)
    assert abs(diagnostico.medidas["luz_pct"]) < 2
    diagnostico = Diagnostico()
    localizador.localizar((img * 0.8).astype(np.uint8), diagnostico=diagnostico)
    assert diagnostico.medidas["luz_pct"] == pytest.approx(-20, abs=2)
    with pytest.raises(MedicaoNaoFiavel, match=r"luz 4\d % abaixo da da foto do fundo vazio"):
        localizador.localizar((img * 0.6).astype(np.uint8))


@pytest.mark.parametrize("campo, valor", [("qualidade_aresta_min_pct", 101.0), ("qualidade_reflexos_max_pct", -1.0),
                                          ("qualidade_residuo_max_mm", -0.1), ("qualidade_contraste_min", 1.0),
                                          ("qualidade_luz_max_pct", -5.0)])
def test_limites_do_controlo_sao_validados(campo, valor):
    with pytest.raises(ValueError, match=campo):
        config_teste(localizacao={campo: valor})


def test_ferramenta_propoe_limites_para_la_do_pior_saco_bom(rng, tmp_path, capsys):
    from ferramentas import qualidade as ferramenta

    sinal = {s.parametro: s for s in ferramenta.SINAIS}
    valores = np.array([80.0, 82.0, 90.0, 60.0])  # mediana 81, pior 60
    assert ferramenta.propor(valores, sinal["qualidade_aresta_min_pct"]) == 49.0, "60 − 0,5 × 21 = 49,5 → 49"
    assert ferramenta.propor(np.array([0.10, 0.13, 0.15]), sinal["qualidade_residuo_max_mm"]) == 0.16

    for i, angulo in enumerate((0.0, 10.0, -20.0)):
        guardar_imagem(tmp_path / "ok" / f"saco_{i}.png", cena_saco(rng, angulo=angulo)[0])
    perfil = tmp_path / "perfil.json"
    perfil.write_text(json.dumps({"geral": {"px_por_mm": PPM}}), encoding="utf-8")
    assert ferramenta.main([str(tmp_path / "ok"), "--config", str(perfil), "--saida", str(tmp_path / "q.csv")]) == 0
    saida = capsys.readouterr().out
    assert "Sacos bons medidos: 3" in saida and "pelo menos uns 50" in saida
    bloco = json.loads(saida[saida.index("{"): saida.rindex("}") + 1])["localizacao"]
    assert set(bloco) == {s.parametro for s in ferramenta.SINAIS} - {"qualidade_luz_max_pct"}, "sem foto do fundo, sem luz"
    linhas = (tmp_path / "q.csv").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 4 and "aresta_min_pct" in linhas[0]


# --- Espaço métrico canónico ---------------------------------------------------------------

def test_imagem_de_outra_escala_e_reamostrada_para_a_do_perfil(rng):
    """Uma câmara calibrada a 10 px/mm, num perfil a 5 px/mm: tudo é feito a 5 px/mm."""
    from nucleo.contrato import Captura, Passagem
    img, _ = cena_saco(rng, comprimento=150, largura=55, ppm=10.0, tamanho=(2000, 2500))
    mestre = m.Mestre(CFG, apenas=[])
    r = mestre.inspecionar(img, px_por_mm=10.0)
    medidas = r.relatorio("localizacao").medidas
    assert abs(medidas["comprimento_mm"] - 150) < 0.3 and abs(medidas["largura_mm"] - 55) < 0.3, medidas
    assert r.imagem.shape[:2] == (1000, 1250), "a imagem analisada está à escala do perfil"
    assert mestre.inspecionar(img).estado is Estado.SEM_SACO, "sem a escala, 300 mm: fora dos limites"
    captura = Captura(img, "camara#1", 0.0, px_por_mm=10.0, fonte="spinnaker")
    r = mestre.inspecionar_passagem(Passagem(1, [captura], [0.0], 1, 0.0, 0.0, "trigger"))
    assert abs(r.relatorio("localizacao").medidas["comprimento_mm"] - 150) < 0.3
    assert m.no_espaco_metrico(img, 5.0, 5.0) is img and m.no_espaco_metrico(img, None, 5.0) is img
