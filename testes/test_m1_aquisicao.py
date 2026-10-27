"""M1 Aquisição: fontes (pasta, vídeo), disparo (foto/botão, sensor, barreira virtual) e aquisição em paralelo."""

from __future__ import annotations

import dataclasses
import json
import math
import time

import cv2
import numpy as np
import pytest

import main as programa
import mestre as m
from modulos.m1_aquisicao import AquisicaoEmParalelo, FonteVideo, criar_disparo, criar_fonte
from modulos.m1_aquisicao.disparo import DisparoFoto, DisparoSensor, DisparoVirtual
from modulos.m1_aquisicao.fontes import FonteImagem
from nucleo.contrato import Captura, Estado, Passagem
from nucleo.imagens import guardar_imagem, ler_imagem
from testes.apoio import COMPRIMENTO, LARGURA, Linha, com_acentos, config_teste, montagem

PPM, FPS, VEL = Linha.PPM, Linha.FPS, Linha.VELOCIDADE
CFG_VIRTUAL = config_teste(geral={"px_por_mm": PPM}, disparo={"modo": "virtual", "fps": FPS})
CFG_SENSOR = config_teste(geral={"px_por_mm": PPM}, disparo={"modo": "sensor", "fps": FPS})


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


# --- Fontes -------------------------------------------------------------------------------

IMAGEM = np.zeros((40, 60, 3), np.uint8)
IMAGEM[:, :30] = (255, 0, 0)


def test_ler_e_guardar_imagem_com_acentos(tmp_path):
    caminho = com_acentos(tmp_path) / "ok" / "saco ã 1.png"
    guardar_imagem(caminho, IMAGEM)
    assert np.array_equal(ler_imagem(caminho), IMAGEM)


def test_fonte_pasta_le_subpastas_e_salta_imagens_corrompidas(tmp_path):
    pasta = com_acentos(tmp_path)
    guardar_imagem(pasta / "ok" / "saco ã 1.png", IMAGEM)
    guardar_imagem(pasta / "rugas" / "saco é 2.png", IMAGEM)
    (pasta / "rugas" / "corrompida.jpg").write_bytes(b"xx")
    with criar_fonte("pasta", config_teste(), pasta=pasta) as fonte:
        n, capturas = len(fonte), list(fonte)
    assert n == 3 and len(capturas) == 2
    assert all(np.array_equal(c.imagem, IMAGEM) for c in capturas)


def test_fonte_video_com_acentos(tmp_path):
    temporario = tmp_path / "v.mp4"  # o VideoWriter não aceita acentos
    escritor = cv2.VideoWriter(str(temporario), cv2.VideoWriter_fourcc(*"mp4v"), 24, (64, 48))
    for k in range(10):
        escritor.write(np.full((48, 64, 3), 20 * k, np.uint8))
    escritor.release()
    video = com_acentos(tmp_path) / "vídeo linha ç.mp4"
    temporario.rename(video)
    fonte = FonteVideo(video)
    try:
        assert sum(1 for _ in fonte) == 10
    finally:
        fonte.fechar()


@pytest.mark.parametrize("tipo", ["webcam", "spinnaker"])
def test_fontes_por_implementar(tipo):
    with pytest.raises(NotImplementedError):
        criar_fonte(tipo, config_teste())


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
    disparo = DisparoFoto(config_teste())
    assert [p.candidatas[0].origem for p in disparo.passagens(capturas)] == ["cam#0", "cam#4"]
    assert disparo.ignoradas == 5


def test_fotos_de_uma_pasta_nunca_sao_ignoradas():
    fotos = [Captura(IMG, f"foto{i}.jpg", 100 + i * 0.001) for i in range(5)]
    disparo = DisparoFoto(config_teste())
    assert len(list(disparo.passagens(fotos))) == 5 and disparo.ignoradas == 0


def test_dois_toques_afastados_contam_os_dois():
    toques = [Captura(IMG, "a", 100.0, sensor=True), Captura(IMG, "b", 100.5, sensor=True)]
    assert len(list(DisparoFoto(config_teste()).passagens(toques))) == 2


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
    cfg = config_teste(geral={"px_por_mm": PPM}, disparo={"modo": "virtual", "fps": FPS, "fila_max": 1})
    recebidas, descartadas = _receber(CamaraSimulada(gravacao[0]), cfg, 0.9)  # 0,9 s por saco, para 2 sacos/s
    assert len(recebidas) == 6, "nunca se perde um saco em silêncio"
    assert descartadas > 0 and any(p.descartada for p in recebidas)


def test_paralelo_erro_na_thread_chega_ao_programa(gravacao):
    with pytest.raises(RuntimeError, match="câmara desligada"):
        _receber(CamaraSimulada(gravacao[0], avaria_em=20), CFG_VIRTUAL, 0)


class CamaraSemSacos(FonteImagem):
    """Câmara com trigger sem nenhum saco a passar: ler() fica à espera, em ciclos de timeout."""

    ao_vivo = True

    def __init__(self):
        self.fechada = False

    def ler(self):
        while not self.parada:
            time.sleep(0.05)  # GetNextImage(timeout) sem imagem: tentar outra vez
        return None

    def fechar(self):
        self.fechada = True


def test_paralelo_sair_sem_sacos_a_passar_liberta_a_camara():
    fonte = CamaraSemSacos()
    inicio = time.perf_counter()
    with AquisicaoEmParalelo(fonte, criar_disparo(CFG_VIRTUAL), 1):
        time.sleep(0.2)  # o operador sai (q) sem nenhum saco ter passado
    assert fonte.fechada and time.perf_counter() - inicio < 2


def test_main_camara_que_desliga_a_meio_mostra_o_resumo(gravacao, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(programa, "criar_fonte", lambda *a, **k: CamaraSimulada(gravacao[0], avaria_em=60))
    perfil = tmp_path / "montagem.json"
    perfil.write_text(json.dumps(montagem(geral={"px_por_mm": PPM}, disparo={"fps": FPS})), encoding="utf-8")
    codigo = programa.main(["--fonte", "webcam", "--disparo", "virtual", "--config", str(perfil),
                            "--saida", str(tmp_path / "resultados")])
    consola = capsys.readouterr().out
    assert codigo == 1, "a câmara falhou: não pode acabar como se estivesse tudo bem"
    assert "saco 1 (" in consola and "ms saída)" in consola, "uma linha por saco, com o tempo do registo"
    assert "Sacos inspecionados:" in consola, "o resumo do que já foi feito aparece na mesma"
