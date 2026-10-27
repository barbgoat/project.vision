"""M3 Dimensões: comprimento, largura e forma, em sacos sintéticos medidos pelo M2 (5 px/mm)."""

from __future__ import annotations

import numpy as np
import pytest

import mestre as m
from nucleo.config import Config
from nucleo.contrato import Estado, TipoDefeito
from testes.sinteticos import CORES, FORMAS, cena_forma

CFG = Config.de_dict({"geral": {"px_por_mm": 5.0}})


@pytest.fixture(scope="module")
def mestre():
    return m.Mestre(CFG, apenas=["dimensoes"])


@pytest.fixture(scope="module")
def resultados(mestre):
    """Cada saco com defeito, rodado 25°, inspecionado uma vez."""
    rng = np.random.default_rng(11)
    return {nome: mestre.inspecionar(cena_forma(rng, poligono, angulo=25), guardar_intermedios=True)
            for nome, poligono in FORMAS.items() if nome != "certo"}


def _descricoes(resultado):
    return [d.descricao for d in resultado.relatorio("dimensoes").defeitos]


@pytest.mark.parametrize("cor, angulo", zip(CORES.values(), [0, 20, -35, 45, 89, -60, 10, 75]), ids=CORES.keys())
def test_sacos_certos_sem_falsos_alarmes(mestre, cor, angulo):
    img = cena_forma(np.random.default_rng(11), FORMAS["certo"], angulo=angulo, cor=cor)
    r = mestre.inspecionar(img)
    assert r.estado is Estado.OK and not r.relatorio("dimensoes").defeitos, _descricoes(r)


@pytest.mark.parametrize("caso, texto", [("comprido_158", "comprimento 158"), ("estreito_50", "largura 50"),
                                         ("canto_dobrado", "canto dobrado"), ("aba", "material a mais num lado"),
                                         ("rasgao_lado", "num lado (rasgão")])
def test_um_defeito_com_a_causa(resultados, caso, texto):
    r = resultados[caso]
    descricoes = _descricoes(r)
    assert r.estado is Estado.NOK and len(descricoes) == 1 and texto in descricoes[0], descricoes
    assert r.relatorio("dimensoes").defeitos[0].tipo is TipoDefeito.DIMENSAO


def test_canto_dobrado_no_sitio_certo(resultados):
    d = resultados["canto_dobrado"].relatorio("dimensoes").defeitos[0]
    assert abs(abs(d.x_mm) - 70) < 3 and abs(abs(d.y_mm) - 22.5) < 3, (d.x_mm, d.y_mm)
    assert 60 < d.area_mm2 < 115


def test_aba_no_sitio_certo(resultados):
    d = resultados["aba"].relatorio("dimensoes").defeitos[0]
    assert abs(d.x_mm) < 3 and abs(abs(d.y_mm) - 29.5) < 3, (d.x_mm, d.y_mm)


def test_corte_torto_reconhecido(resultados):
    descricoes = _descricoes(resultados["corte_torto"])
    assert resultados["corte_torto"].estado is Estado.NOK
    assert any("corte torto?" in d for d in descricoes) and any("comprimento 144" in d for d in descricoes), descricoes


def test_dois_defeitos_no_mesmo_saco_ficam_separados(resultados):
    descricoes = _descricoes(resultados["canto_e_aba"])
    assert len(descricoes) == 2
    assert any("canto dobrado" in d for d in descricoes) and any("material a mais" in d for d in descricoes)


def test_medidas_e_imagem_intermedia(resultados):
    relatorio = resultados["canto_e_aba"].relatorio("dimensoes")
    for medida in ("comprimento_mm", "largura_mm", "retangularidade", "falta_mm2", "sobra_mm2"):
        assert medida in relatorio.medidas
    assert "forma" in relatorio.intermedios
