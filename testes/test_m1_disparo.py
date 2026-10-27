"""M1 Aquisição, disparo: uma passagem por saco (foto/botão, sensor, barreira virtual) e aquisição em paralelo."""

from __future__ import annotations

import dataclasses
import math
import time

import numpy as np
import pytest

import mestre as m
from modulos.m1_aquisicao import AquisicaoEmParalelo, criar_disparo
from modulos.m1_aquisicao.disparo import DisparoFoto, DisparoSensor, DisparoVirtual
from modulos.m1_aquisicao.fontes import FonteImagem
from nucleo.config import Config
from nucleo.contrato import Captura, Estado, Passagem
from testes.sinteticos import COMPRIMENTO, LARGURA, Linha

PPM, FPS, VEL = Linha.PPM, Linha.FPS, Linha.VELOCIDADE
CFG_VIRTUAL = Config.de_dict({"geral": {"px_por_mm": PPM}, "disparo": {"modo": "virtual", "fps": FPS}})
CFG_SENSOR = Config.de_dict({"geral": {"px_por_mm": PPM}, "disparo": {"modo": "sensor", "fps": FPS}})


@pytest.fixture(scope="module")
def linha():
    return Linha(np.random.default_rng(3))


@pytest.fixture(scope="module")
def gravacao(linha):
    """6 sacos de cores diferentes, 100 mm entre eles; a ponta do seguinte já à vista."""
    imagens, sensor = linha.gerar()
    return linha.capturas(imagens), sensor


@pytest.fixture(scope="module")
def passagens_virtuais(gravacao):
    return list(DisparoVirtual(CFG_VIRTUAL).passagens(gravacao[0]))


# --- Barreira virtual ---------------------------------------------------------------------

def test_virtual_uma_passagem_por_saco(passagens_virtuais):
    assert len(passagens_virtuais) == 6
    esperado = (COMPRIMENTO + CFG_VIRTUAL.disparo.virtual_faixa_mm) / VEL * 1000
    assert all(abs(p.duracao_ms - esperado) < 120 for p in passagens_virtuais), \
        "duração ≈ (comprimento + faixa) / velocidade"


def test_virtual_primeira_candidata_centrada(passagens_virtuais):
    assert all(p.pontuacoes[0] is not None and p.pontuacoes[0] < 0.1 for p in passagens_virtuais)


def test_m2_encontra_o_saco_inteiro_na_primeira_candidata(passagens_virtuais):
    localizador = m.Localizador(CFG_VIRTUAL)
    for p in passagens_virtuais:
        saco = localizador.localizar(p.candidatas[0].imagem)
        assert abs(saco.comprimento_mm - COMPRIMENTO) < 1.5 and abs(saco.largura_mm - LARGURA) < 1.5


def test_virtual_sacos_encostados_nao_sao_aprovados(linha):
    imagens, _ = linha.gerar(n_sacos=2, encostados=True)
    passagens = list(DisparoVirtual(CFG_VIRTUAL).passagens(linha.capturas(imagens, prefixo="c")))
    assert len(passagens) == 1, "inseparáveis por uma barreira (como com um sensor físico)"
    r = m.Mestre(CFG_VIRTUAL, apenas=[]).inspecionar_passagem(passagens[0])
    assert r.estado is Estado.ERRO and ("fora dos limites" in r.mensagem or "nenhuma" in r.mensagem)


def test_virtual_saco_parado_da_aviso(linha):
    vazio = [Captura(linha.imagem([]), f"v#{i}", 1000 - (10 - i) / FPS) for i in range(10)]
    parado = [Captura(linha.imagem([(125, 100, 0, Linha.CORES[0])]), f"p#{i}", 1000 + i / FPS) for i in range(80)]
    passagens = list(DisparoVirtual(CFG_VIRTUAL).passagens(vazio + parado))
    assert passagens and "mais de" in passagens[0].aviso


def test_virtual_falha_de_imagens_a_meio(gravacao):
    capturas = gravacao[0]
    com_falha = capturas[:40] + [dataclasses.replace(c, instante=c.instante + 1.0) for c in capturas[40:]]
    passagens = list(DisparoVirtual(CFG_VIRTUAL).passagens(com_falha))
    assert any("faltaram imagens" in p.aviso for p in passagens) or len(passagens) == 6


# --- Sensor -------------------------------------------------------------------------------

def test_sensor_janela(gravacao):
    capturas, sensor = gravacao
    com_sensor = [dataclasses.replace(c, sensor=s) for c, s in zip(capturas, sensor)]
    passagens = list(DisparoSensor(CFG_SENSOR).passagens(com_sensor))
    assert len(passagens) == 6
    assert all(abs(p.duracao_ms - COMPRIMENTO / VEL * 1000) < 80 for p in passagens)
    assert all(p.pontuacoes[0] is not None and p.pontuacoes[0] < 0.1 for p in passagens)


def test_sensor_rajada(linha):
    rajadas = []
    for n in range(6):
        t0 = 1001 + n * 0.5
        for k in range(5):
            img = linha.imagem([(Linha.CAMPO[0] / 2 + (k - 2) * VEL / FPS, 100, 0, Linha.CORES[n])])
            rajadas.append(Captura(img, f"rajada{n + 1}#{k + 1}", t0 + k / FPS, sensor=True))
    passagens = list(DisparoSensor(CFG_SENSOR).passagens(rajadas))
    assert len(passagens) == 6 and all(p.num_imagens == 5 for p in passagens)
    assert all(p.candidatas[0].origem.endswith("#3") for p in passagens), \
        "sem fundo conhecido, a 1.ª candidata é a do meio da rajada"


def test_sensor_sem_sensor_na_fonte_da_erro_claro(gravacao):
    with pytest.raises(ValueError, match="virtual"):
        list(DisparoSensor(CFG_SENSOR).passagens(gravacao[0][:3]))


# --- Foto e botão -------------------------------------------------------------------------

IMG = np.zeros((4, 4, 3), np.uint8)


def test_botao_com_saltos_conta_uma_vez():
    # Dois toques no botão (t = 0 s e t = 2 s), cada um com saltos nos primeiros 15 ms.
    instantes = [0.000, 0.004, 0.009, 0.015, 2.000, 2.003, 2.011]
    capturas = [Captura(IMG, f"cam#{i}", 100 + t, sensor=True) for i, t in enumerate(instantes)]
    disparo = DisparoFoto(Config())
    assert [p.candidatas[0].origem for p in disparo.passagens(capturas)] == ["cam#0", "cam#4"]
    assert disparo.ignoradas == 5


def test_fotos_de_uma_pasta_nunca_sao_ignoradas():
    fotos = [Captura(IMG, f"foto{i}.jpg", 100 + i * 0.001) for i in range(5)]
    disparo = DisparoFoto(Config())
    assert len(list(disparo.passagens(fotos))) == 5 and disparo.ignoradas == 0


def test_dois_toques_afastados_contam_os_dois():
    toques = [Captura(IMG, "a", 100.0, sensor=True), Captura(IMG, "b", 100.5, sensor=True)]
    assert len(list(DisparoFoto(Config()).passagens(toques))) == 2


# --- Mestre: uma decisão por saco ---------------------------------------------------------

def test_mestre_analisa_a_primeira_candidata(passagens_virtuais):
    p = passagens_virtuais[2]
    r = m.Mestre(CFG_VIRTUAL, apenas=[]).inspecionar_passagem(p)
    assert r.relatorio("localizacao").estado is Estado.OK
    assert r.captura is p.candidatas[0] and r.imagens_analisadas == 1 and r.passagem is p


def test_mestre_nenhuma_candidata_com_o_saco_inteiro_da_erro(gravacao):
    capturas = gravacao[0]
    cortadas = Passagem(99, [capturas[k] for k in (18, 19, 20)], [math.inf] * 3, 3, 0, 0.1, "virtual")
    r = m.Mestre(CFG_VIRTUAL, apenas=[]).inspecionar_passagem(cortadas)
    assert r.estado is Estado.ERRO and r.imagens_analisadas == 3 and "nenhuma das 3" in r.mensagem


def test_mestre_passagem_descartada_da_erro_sem_imagem():
    descartada = Passagem(100, [], [], 12, 0, 0.3, "sensor", aviso="não analisada: teste", descartada=True)
    r = m.Mestre(CFG_VIRTUAL, apenas=[]).inspecionar_passagem(descartada)
    assert r.estado is Estado.ERRO and r.imagem is None and "teste" in r.mensagem


# --- Aquisição em paralelo ----------------------------------------------------------------

class CamaraSimulada(FonteImagem):
    """Entrega as imagens ao ritmo real, sem esperar pela análise (como uma câmara)."""

    ao_vivo = True

    def __init__(self, capturas, avaria_em: int | None = None):
        self.capturas, self.i, self.avaria_em = capturas, 0, avaria_em

    def ler(self):
        if self.i == self.avaria_em:
            raise RuntimeError("câmara desligada")
        if self.i >= len(self.capturas):
            return None
        time.sleep(1 / FPS)
        c = self.capturas[self.i]
        self.i += 1
        return dataclasses.replace(c, instante=time.time())

    def fechar(self):
        self.i = len(self.capturas)


def _receber(fonte, cfg, segundos_por_saco):
    recebidas = []
    with AquisicaoEmParalelo(fonte, criar_disparo(cfg), cfg.disparo.fila_max) as aquisicao:
        for p in aquisicao:
            recebidas.append(p)
            time.sleep(segundos_por_saco)
    return recebidas, aquisicao.descartadas


def test_paralelo_analise_a_tempo_nao_perde_nada(gravacao):
    recebidas, descartadas = _receber(CamaraSimulada(gravacao[0]), CFG_VIRTUAL, 0.15)
    assert len(recebidas) == 6 and descartadas == 0


def test_paralelo_analise_lenta_descarta_mas_regista(gravacao):
    cfg = Config.de_dict({"geral": {"px_por_mm": PPM}, "disparo": {"modo": "virtual", "fps": FPS, "fila_max": 1}})
    recebidas, descartadas = _receber(CamaraSimulada(gravacao[0]), cfg, 0.9)  # 0,9 s por saco, para 2 sacos/s
    assert len(recebidas) == 6, "nunca se perde um saco em silêncio"
    assert descartadas > 0 and any(p.descartada for p in recebidas)


def test_paralelo_erro_na_thread_chega_ao_programa(gravacao):
    with pytest.raises(RuntimeError, match="câmara desligada"):
        _receber(CamaraSimulada(gravacao[0], avaria_em=20), CFG_VIRTUAL, 0)
