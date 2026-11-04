"""M3 Dimensões: comprimento, largura e forma, em sacos sintéticos medidos pelo M2 (5 px/mm)."""

from __future__ import annotations

import numpy as np
import pytest

import mestre as m
from nucleo.contrato import Estado, TipoDefeito
from testes.apoio import CORES, FORMAS, cena_forma, config_teste, cortar_canto, retangulo_arredondado

CFG = config_teste(geral={"px_por_mm": 5.0})


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
    nominal = relatorio.intermedios["nominal"]  # o medido (verde) face ao nominal (azul) e à tolerância
    b, g, r = (nominal[..., i].astype(int) for i in range(3))
    medido = (g > 150) & (b < 90) & (r < 90)  # verde
    tolerancia = (b > 200) & (g > 160) & (r > 100) & (r < 190)  # azul claro (aqui o nominal fica por baixo do verde)
    assert medido.any() and tolerancia.any()



# Cantos arredondados (raio 10 mm). Com o raio no config, a forma certa é a do produto
# e a folga e a área mínima podem ser apertadas sem falsos alarmes.
ESTRITO = {"forma_folga_mm": 0.5, "forma_area_min_mm2": 10.0}
CFG_SEM_RAIO = config_teste(geral={"px_por_mm": 5.0}, dimensoes=ESTRITO)
CFG_RAIO = config_teste(geral={"px_por_mm": 5.0}, dimensoes={**ESTRITO, "forma_raio_canto_mm": 10.0})


def _forma(cfg, poligono, angulo=25):
    r = m.Mestre(cfg, apenas=["dimensoes"]).inspecionar(cena_forma(np.random.default_rng(11), poligono, angulo=angulo))
    return r.estado, _descricoes(r)


def test_cantos_redondos_sem_raio_dao_falsos_alarmes():
    estado, descricoes = _forma(CFG_SEM_RAIO, retangulo_arredondado(10.0))
    assert estado is Estado.NOK and len(descricoes) == 4 and all("falta material num canto" in d for d in descricoes)


@pytest.mark.parametrize("angulo", [0, 20, -35, 45, 89])
def test_cantos_redondos_com_raio_sem_falsos_alarmes(angulo):
    estado, descricoes = _forma(CFG_RAIO, retangulo_arredondado(10.0), angulo)
    assert estado is Estado.OK, descricoes


def test_canto_dobrado_num_saco_de_cantos_redondos():
    estado, descricoes = _forma(CFG_RAIO, cortar_canto(retangulo_arredondado(10.0), corte=15.0))
    assert estado is Estado.NOK and len(descricoes) == 1 and "canto dobrado" in descricoes[0], descricoes


def test_cantos_vivos_quando_deviam_ser_redondos():
    estado, descricoes = _forma(CFG_RAIO, FORMAS["certo"])
    assert estado is Estado.NOK and len(descricoes) == 4 and all("material a mais num canto" in d for d in descricoes)


# Geometria: esquadria das pontas e paralelismo dos lados (ângulos, independentes da escala).
def _c4(dx=0.0):
    return 75.0 + dx


def _l4(dy=0.0):
    return 27.5 + dy


TORTO_4MM = [(-_c4(), -_l4()), (_c4(), -_l4()), (_c4(-4), _l4()), (-_c4(), _l4())]  # 4,16°: pequeno demais para a área
TRAPEZIO = [(-_c4(), -_l4()), (_c4(), -_l4(-3.9)), (_c4(), _l4(-3.9)), (-_c4(), _l4())]  # lados a 2,98°


@pytest.mark.parametrize("angulo", [0, 25, -60])
def test_saco_certo_em_esquadria(mestre, angulo):
    md = mestre.inspecionar(cena_forma(np.random.default_rng(11), FORMAS["certo"], angulo=angulo)).relatorio("dimensoes").medidas
    for chave in ("esquadria_esquerda_graus", "esquadria_direita_graus", "paralelismo_graus", "canto_desvio_max_graus"):
        assert abs(md[chave]) < 0.2, (chave, md[chave])


@pytest.mark.parametrize("angulo", [0, 25, -60])
def test_corte_torto_pequeno_apanhado_pelo_angulo(mestre, angulo):
    r = mestre.inspecionar(cena_forma(np.random.default_rng(11), TORTO_4MM, angulo=angulo)).relatorio("dimensoes")
    descricoes = [d.descricao for d in r.defeitos]
    assert abs(abs(r.medidas["esquadria_direita_graus"]) - 4.16) < 0.15, r.medidas
    assert len(descricoes) == 1 and "ponta direita fora de esquadria" in descricoes[0], descricoes


def test_corte_torto_grande_e_um_so_defeito_de_forma(resultados):
    descricoes = _descricoes(resultados["corte_torto"])
    assert sum("corte torto?" in d for d in descricoes) == 1 and any("esquadria" in d for d in descricoes), descricoes


def test_lados_nao_paralelos(mestre):
    r = mestre.inspecionar(cena_forma(np.random.default_rng(11), TRAPEZIO, angulo=20)).relatorio("dimensoes")
    assert abs(r.medidas["paralelismo_graus"] - 2.98) < 0.15, r.medidas
    assert any("não paralelos" in d.descricao for d in r.defeitos)


def test_canto_dobrado_nao_mexe_na_esquadria(resultados):
    md = resultados["canto_dobrado"].relatorio("dimensoes").medidas
    assert abs(md["esquadria_direita_graus"]) < 0.5 and abs(md["paralelismo_graus"]) < 0.5, md


# Pontas com cantos diferentes (4 e 11 mm), com o saco virado para qualquer lado.
CFG_DOIS_RAIOS = config_teste(geral={"px_por_mm": 5.0},
                              dimensoes={**ESTRITO, "forma_raio_canto_mm": 4.0, "forma_raio_canto_outra_ponta_mm": 11.0})


@pytest.mark.parametrize("angulo", [10, 190])
def test_pontas_com_raios_diferentes_sem_falsos_alarmes(angulo):
    poligono = retangulo_arredondado(11.0, raio_esquerda=4.0)
    assert _forma(CFG_RAIO, poligono, angulo)[0] is Estado.NOK  # um só raio não serve
    estado, descricoes = _forma(CFG_DOIS_RAIOS, poligono, angulo)
    assert estado is Estado.OK, descricoes


def test_entalhe_estreito_apanhado_pela_profundidade(mestre):
    # 5 × 3 mm num lado: 7 mm2 depois da folga, longe dos 25 mm2, mas 3 mm de fundo
    entalhe = [(-_c4(), -_l4()), (_c4(), -_l4()), (_c4(), _l4()), (2.5, _l4()), (2.5, _l4(-3)), (-2.5, _l4(-3)),
               (-2.5, _l4()), (-_c4(), _l4())]
    r = mestre.inspecionar(cena_forma(np.random.default_rng(11), entalhe, angulo=25)).relatorio("dimensoes")
    descricoes = [d.descricao for d in r.defeitos]
    assert len(descricoes) == 1 and "num lado" in descricoes[0] and r.defeitos[0].area_mm2 < 25, descricoes
    assert abs(r.defeitos[0].intensidade - 3.0) < 0.4, r.defeitos[0].intensidade


def test_severidade_e_contorno_em_mm_dos_defeitos():
    """Severidade = valor / limiar; o contorno da forma vem em mm, dentro do saco."""
    comprido = m.Mestre(CFG, apenas=["dimensoes"]).inspecionar(cena_forma(np.random.default_rng(3), FORMAS["comprido_158"]))
    (d,) = [d for d in comprido.defeitos if d.descricao.startswith("comprimento")]
    assert d.severidade == pytest.approx(abs(d.intensidade) / CFG.dimensoes.tolerancia_comprimento_mm) and d.severidade > 1
    dobrado = m.Mestre(CFG, apenas=["dimensoes"]).inspecionar(cena_forma(np.random.default_rng(3), FORMAS["canto_dobrado"]))
    forma = [d for d in dobrado.defeitos if d.contorno is not None]
    assert forma and all(d.severidade >= 1 and d.contorno.shape[1] == 2 for d in forma)
    assert all(np.abs(d.contorno[:, 0]).max() <= 150 / 2 + 2 for d in forma), "em mm, no referencial do saco"
