"""M7 Saída: registos CSV, imagem anotada e depuração (pasta por saco com cada etapa)."""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

import mestre as m
from modulos.m7_saida import Depuracao, Saida, desenhar, nome_curto
from modulos.m7_saida import depuracao as modulo_depuracao
from nucleo import defines as D
from nucleo.config import Config
from nucleo.contrato import Captura, Defeito, Escravo, Estado, Relatorio, TipoDefeito
from nucleo.imagens import ler_imagem
from testes.apoio import FORMAS, cena_forma, com_acentos, ler_csv

PPM = 5.0
CFG = Config.de_dict({"geral": {"px_por_mm": PPM}})


class ComDefeitos(Escravo):
    nome, pergunta = "relevo", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return Relatorio.concluir(self.nome, [Defeito(TipoDefeito.RISCO, 1, 2, 20, 0.4),
                                              Defeito(TipoDefeito.RUGAS, 30, 0, 25, 15)])


class Rebenta(Escravo):
    nome, pergunta = "cor", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return [1, 2][5]


@pytest.fixture(scope="module")
def imagem():
    return cena_forma(np.random.default_rng(5), FORMAS["canto_e_aba"], angulo=-15)


@pytest.fixture(scope="module")
def resultado(imagem):
    """Saco com 2 defeitos de forma (M3 real), 2 de relevo (falso) e um módulo que rebenta."""
    mestre = m.Mestre(CFG, apenas=["dimensoes"])
    mestre.escravos += [ComDefeitos(CFG), Rebenta(CFG)]
    r = mestre.inspecionar(imagem.copy(), guardar_intermedios=True)
    r.captura = Captura(imagem, "pasta/rugas/saco é 2.png", 1_700_000_000.0)
    return r


def test_registo_csv(resultado, tmp_path):
    pasta = com_acentos(tmp_path)
    with Saida(CFG, pasta, ["localizacao", "dimensoes", "relevo", "cor"]) as saida:
        caminho = saida.registar(resultado)
    assert (saida.pasta / "registo.csv").read_bytes().startswith(b"\xef\xbb\xbf"), "BOM, para o Excel"
    (linha,) = ler_csv(saida.pasta / "registo.csv")
    assert linha["estado"] == "NOK"
    assert (linha["dimensoes"], linha["relevo"], linha["cor"]) == ("NOK(2)", "NOK(2)", "ERRO")
    assert linha["tipos"] == "dimensao:2,risco:1,rugas:1"
    assert "," in linha["t_relevo_ms"], "vírgula decimal"
    assert "cor: IndexError" in linha["mensagens"]

    defeitos = ler_csv(saida.pasta / "defeitos.csv")
    assert [d["numero"] for d in defeitos] == ["1", "2", "3", "4"]
    risco = next(d for d in defeitos if d["tipo"] == "risco")
    assert risco["modulo"] == "relevo" and risco["x_mm"] == "1,00"

    assert caminho is not None and caminho.exists() and caminho.name.startswith("000001_saco_é_2_NOK")


def test_nomes_de_ficheiro_a_partir_da_origem():
    assert nome_curto("spinnaker:12345#17") == "spinnaker_12345_17"
    assert nome_curto("webcam0#3") == "webcam0_3"


def test_desenhar_devolve_uma_copia_anotada(resultado):
    antes = resultado.imagem.copy()
    anotada = desenhar(resultado, PPM)
    assert anotada.shape == antes.shape and not np.array_equal(anotada, antes)
    assert np.array_equal(resultado.imagem, antes), "a imagem original fica intacta"


def test_depuracao_pasta_do_saco(resultado, tmp_path):
    pasta = Depuracao(CFG, tmp_path, "nok").processar(resultado, 12)
    ficheiros = sorted(f.name for f in pasta.iterdir())
    assert ficheiros == ["00_original.png", "01_anotada.jpg", "02_localizacao_diferenca.png",
                         "03_localizacao_segmentacao.png", "04_localizacao_recorte.png", "05_dimensoes_forma.png",
                         "painel.jpg", "resultado.json"]
    assert pasta.name.startswith("saco_0012_")
    assert np.array_equal(ler_imagem(pasta / "00_original.png"), resultado.imagem), "original sem perdas"
    painel = ler_imagem(pasta / "painel.jpg")
    assert painel.shape[1] == D.SAIDA_PAINEL_COLUNAS * D.SAIDA_PAINEL_LARGURA_ETAPA_PX

    dados = json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))
    modulos = {x["modulo"]: x for x in dados["modulos"]}
    assert dados["estado"] == "NOK", "NOK prevalece sobre ERRO"
    assert len(modulos["dimensoes"]["defeitos"]) == 2
    assert "limiar_delta_e" in modulos["localizacao"]["medidas"]
    assert "Traceback" in modulos["cor"]["traceback"] and "IndexError" in modulos["cor"]["traceback"]
    assert dados["configuracao_alterada"] == [f"geral.px_por_mm = 5.0 (define GERAL_PX_POR_MM = {D.GERAL_PX_POR_MM})"]
    assert dados["saco_medido"]["comprimento_mm"] > 149


def test_depuracao_so_dos_sacos_pedidos(resultado, tmp_path):
    ok = m.Mestre(CFG, apenas=["dimensoes"]).inspecionar(
        cena_forma(np.random.default_rng(5), FORMAS["certo"]), guardar_intermedios=True)
    assert ok.estado is Estado.OK
    assert Depuracao(CFG, tmp_path, "nok").processar(ok) is None
    assert Depuracao(CFG, tmp_path, "todas").processar(ok) is not None


def test_se_a_depuracao_falhar_a_inspecao_continua(resultado, tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_depuracao, "para_ver", lambda imagem: 1 / 0)
    assert Depuracao(CFG, tmp_path, "todas").processar(resultado) is None


def test_saida_com_depuracao_aponta_para_a_pasta(resultado, tmp_path):
    with Saida(CFG, tmp_path, ["localizacao", "dimensoes"], depurar="nok") as saida:
        saida.registar(resultado)
    (linha,) = ler_csv(saida.pasta / "registo.csv")
    assert linha["depuracao"].startswith("depuracao")
    assert (saida.pasta / linha["depuracao"] / "painel.jpg").exists()


def test_registo_tem_a_data_da_captura(resultado, tmp_path):
    with Saida(CFG, tmp_path, ["localizacao"]) as saida:
        saida.registar(resultado)
    (linha,) = ler_csv(saida.pasta / "registo.csv")
    assert linha["data_hora"].startswith(time.strftime("%Y-%m-%d", time.localtime(1_700_000_000.0)))
