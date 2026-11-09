"""O que um OK garante: os módulos que o verificaram, a referência dos sacos OK e a avaliação no dataset."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

import mestre as m
from ferramentas import avaliar, referencia as ferramenta
from nucleo import dashboard
from nucleo.contrato import Atipica, Captura, Estado, Relatorio, Resultado
from nucleo.especificacoes import Repositorio, resumir
from nucleo.imagens import guardar_imagem
from nucleo.referencia import MEDIDAS, Distribuicao, Referencia, medidas_do_saco, nome_ficheiro
from testes.apoio import cena_saco, config_teste, ler_csv, montagem

IMG = np.zeros((40, 60, 3), np.uint8)


def _referencia(cfg, **medidas: tuple[float, float]) -> Referencia:
    """Uma referência à mão: medida → (média, desvio-padrão), com 30 sacos."""
    return Referencia("PreVital", 30, {n: Distribuicao(30, media, dp, media - 3 * dp, media + 3 * dp)
                                       for n, (media, dp) in medidas.items()},
                      {"comprimento_nominal_mm": cfg.dimensoes.comprimento_nominal_mm,
                       "largura_nominal_mm": cfg.dimensoes.largura_nominal_mm}, cfg.geral.px_por_mm)


# --- Ponto 1: o que ficou por verificar --------------------------------------------------

@pytest.fixture(scope="module")
def saco():
    return cena_saco(np.random.default_rng(4))[0]


def test_ok_diz_que_modulos_o_verificaram(saco):
    r = m.Mestre(config_teste()).inspecionar(saco.copy())
    assert r.estado is Estado.OK
    assert r.verificado_por == ["dimensoes"]
    assert r.por_verificar == ["cor", "relevo", "manchas"] and r.desligados == ()
    assert r.aviso == "não verificado: cor, relevo, manchas"


def test_desligados_tambem_ficam_por_verificar(saco):
    cfg = config_teste(cor={"ativo": False})
    r = m.Mestre(cfg).inspecionar(saco.copy())
    assert r.desligados == ("cor",) and r.nao_verificado == ["relevo", "manchas", "cor"]


def test_exigir_todos_nao_aprova_um_ok_incompleto(saco):
    r = m.Mestre(config_teste(geral={"exigir_todos": True})).inspecionar(saco.copy())
    assert r.estado is Estado.ERRO and "não verificado por cor, relevo, manchas" in r.mensagem
    # Desligar é uma escolha: com só as dimensões ativas, o OK é completo.
    so_dimensoes = config_teste(geral={"exigir_todos": True}, cor={"ativo": False}, relevo={"ativo": False},
                                manchas={"ativo": False})
    assert m.Mestre(so_dimensoes).inspecionar(saco.copy()).estado is Estado.OK


def test_sem_saco_nao_diz_nada_por_verificar():
    r = m.Mestre(config_teste()).inspecionar(IMG.copy())
    assert r.estado is Estado.SEM_SACO and r.nao_verificado == [] and r.aviso == ""


# --- Ponto 2: a referência dos sacos OK ----------------------------------------------------

def test_medidas_do_saco_so_dos_que_concluiram_e_sem_o_sentido():
    relatorios = [
        Relatorio("dimensoes", Estado.OK, medidas={"comprimento_mm": 150.2, "esquadria_esquerda_graus": 0.3,
                                                   "esquadria_direita_graus": -0.8, "paralelismo_graus": -0.4}),
        Relatorio("cor", Estado.POR_IMPLEMENTAR, medidas={"largura_mm": 1.0}),
    ]
    assert medidas_do_saco(relatorios) == {"comprimento_mm": 150.2, "esquadria_max_graus": 0.8,
                                           "paralelismo_graus": 0.4}


def test_comparar_so_aponta_o_que_fica_longe_dos_bons():
    cfg = config_teste()
    medicoes = [{"comprimento_mm": 150 + d, "falta_mm2": f} for d, f in
                zip(np.linspace(-0.2, 0.2, 25), np.linspace(0, 4, 25))]
    ref = Referencia.de_medicoes("PreVital", medicoes, cfg)
    c = ref.medidas["comprimento_mm"]
    assert ref.sacos == 25 and c.n == 25 and abs(c.media - 150) < 1e-9 and c.minimo == pytest.approx(149.8)
    limite = c.limite("comprimento_mm", 3.0)
    assert limite == pytest.approx(3 * c.desvio_padrao + MEDIDAS["comprimento_mm"].resolucao)
    assert ref.comparar({"comprimento_mm": 150 + 0.9 * limite}, 3.0, 20) == ()
    (alta,) = ref.comparar({"comprimento_mm": 150 + 1.1 * limite}, 3.0, 20)
    assert alta.medida == "comprimento_mm" and "sacos OK: 150.00" in alta.texto
    assert len(ref.comparar({"comprimento_mm": 150 - 1.1 * limite}, 3.0, 20)) == 1, "nos dois sentidos"
    assert ref.comparar({"falta_mm2": 0.0}, 3.0, 20) == (), "ter menos falta do que os bons não é aviso"
    assert ref.comparar({"comprimento_mm": 160.0}, 3.0, 26) == (), "poucos sacos nessa medida: não compara"


def test_referencia_guardada_so_vale_para_a_mesma_montagem(tmp_path):
    cfg = config_teste(referencia={"pasta": str(tmp_path)})
    assert Referencia.para(cfg, "PreVital") is None, "sem ficheiro"
    ref = _referencia(cfg, comprimento_mm=(150.0, 0.1))
    ref.guardar(Referencia.caminho(cfg, "PreVital"))
    assert Referencia.carregar(Referencia.caminho(cfg, "PreVital")) == ref
    assert Referencia.para(cfg, "PreVital") == ref
    assert Referencia.para(config_teste(referencia={"pasta": str(tmp_path), "ativo": False}), "PreVital") is None
    outro_nominal = config_teste(referencia={"pasta": str(tmp_path)}, dimensoes={"comprimento_nominal_mm": 160.0})
    assert "nominal" in ref.problema(outro_nominal) and Referencia.para(outro_nominal, "PreVital") is None
    recalibrada = config_teste(referencia={"pasta": str(tmp_path)}, geral={"px_por_mm": 5.2})
    assert "px_por_mm" in ref.problema(recalibrada) and Referencia.para(recalibrada, "PreVital") is None
    Referencia.caminho(cfg, "Estragado").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="não é uma referência"):
        Referencia.para(cfg, "Estragado")
    assert nome_ficheiro(" Garnier: 1/2 ") == "Garnier_1_2" and nome_ficheiro("") == "sem_produto"


def test_mestre_aponta_o_saco_atipico_sem_mudar_o_estado(saco, tmp_path):
    cfg = config_teste(referencia={"pasta": str(tmp_path)})
    medido = m.Mestre(cfg, produto="PreVital").inspecionar(saco.copy()).saco.comprimento_mm
    _referencia(cfg, comprimento_mm=(medido + 1.0, 0.1), largura_mm=(55.0, 2.0)).guardar(
        Referencia.caminho(cfg, "PreVital"))
    r = m.Mestre(cfg, produto="PreVital").inspecionar(saco.copy())
    assert r.estado is Estado.OK, "é um aviso"
    assert [a.medida for a in r.atipicas] == ["comprimento_mm"]
    assert "atípico: comprimento_mm" in r.aviso
    assert m.Mestre(cfg, produto="Outro").inspecionar(saco.copy()).atipicas == (), "cada produto a sua"


# --- Registos: CSV, base de dados e dashboard -------------------------------------------------

def _resultado_ok(nao_verificado=("cor",), atipicas=()) -> Resultado:
    relatorios = [Relatorio("localizacao", Estado.OK), Relatorio("dimensoes", Estado.OK),
                  *(Relatorio(x, Estado.POR_IMPLEMENTAR) for x in nao_verificado)]
    return Resultado(IMG, Estado.OK, SimpleNamespace(comprimento_mm=150.0, largura_mm=55.0), relatorios, 1.0,
                     captura=Captura(IMG, "x.png", 1_700_000_000.0), of="OF1", produto="PreVital",
                     atipicas=atipicas)


def test_base_de_dados_e_dashboard_contam_os_ok_incompletos_e_atipicos(tmp_path):
    repo = Repositorio(tmp_path / "bd.sqlite")
    atipica = Atipica("largura_mm", 56.0, 55.0, 0.1, 0.4)
    repo.registar(_resultado_ok(), "s", 1)
    repo.registar(_resultado_ok(("cor", "relevo"), (atipica,)), "s", 2)
    repo.registar(_resultado_ok(()), "s", 3)
    primeiro, segundo, _ = repo.historico("OF1")
    assert primeiro.nao_verificado == ("cor",) and segundo.atipicas == ("largura_mm",)
    r = resumir(repo.historico("OF1"))
    assert (r.ok, r.ok_incompletos, r.ok_atipicos) == (3, 2, 1)
    assert r.nao_verificado == {"cor": 2, "relevo": 1} and r.atipicas == {"largura_mm": 1}
    texto = dashboard.observacoes_ok(r)
    assert "OK sem verificar: cor 2, relevo 1" in texto and "OK atípicos face aos sacos OK: 1 (largura_mm 1)" in texto


def test_registo_csv_tem_o_que_ficou_por_verificar(tmp_path):
    from modulos.m7_saida import Saida
    cfg = config_teste()
    atipica = Atipica("largura_mm", 56.0, 55.0, 0.1, 0.4)
    with Saida(cfg, tmp_path, ["localizacao", "dimensoes", "cor"]) as saida:
        saida.registar(_resultado_ok(("cor",), (atipica,)))
    (linha,) = ler_csv(saida.pasta / "registo.csv")
    assert linha["nao_verificado"] == "cor" and linha["atipico"].startswith("largura_mm 56.00")


# --- As ferramentas -----------------------------------------------------------------------

def _perfil(tmp_path, **secoes) -> str:
    caminho = tmp_path / "perfil.json"
    secoes["referencia"] = {"pasta": str(tmp_path / "refs"), "sacos_min": 3, **secoes.get("referencia", {})}
    caminho.write_text(json.dumps(montagem(**secoes)), encoding="utf-8")
    return str(caminho)


def _imagens(pasta, n: int, semente: int, **kw) -> None:
    rng = np.random.default_rng(semente)
    for i in range(n):
        guardar_imagem(pasta / f"saco_{semente}_{i}.png", cena_saco(rng, angulo=10.0 + 15 * i, **kw)[0])


def test_ferramenta_referencia_guarda_e_mostra(tmp_path, capsys):
    _imagens(tmp_path / "ok", 3, 1)
    perfil = _perfil(tmp_path)
    comum = ["--config", perfil, "--produto", "PreVital", "--resultados", str(tmp_path / "res")]
    assert ferramenta.main([str(tmp_path / "ok"), *comum]) == 0
    ref = Referencia.carregar(tmp_path / "refs" / "PreVital.json")
    assert ref.sacos == 3 and abs(ref.medidas["comprimento_mm"].media - 150) < 1
    assert len(ler_csv(tmp_path / "res" / "referencia" / "PreVital.csv")) == 3
    assert ferramenta.main([*comum, "--ver"]) == 0 and "comprimento_mm" in capsys.readouterr().out
    # Com menos sacos do que referencia.sacos_min não guarda
    poucos = _perfil(tmp_path, referencia={"pasta": str(tmp_path / "refs2"), "sacos_min": 10})
    assert ferramenta.main([str(tmp_path / "ok"), "--config", poucos, "--resultados", str(tmp_path / "res")]) == 1
    assert not (tmp_path / "refs2").exists()


def test_avaliar_conta_falsas_rejeicoes_e_escapados(tmp_path, capsys):
    dataset = tmp_path / "dataset"
    _imagens(dataset / "ok", 2, 2)
    _imagens(dataset / "dimensao", 1, 3, comprimento=158.0)  # NOK: 8 mm a mais
    _imagens(dataset / "dimensao", 1, 6, comprimento=150.5)  # "defeito" que o M3 não vê: escapa
    guardar_imagem(dataset / "fundo.png", IMG)  # fora das pastas das classes
    saida = tmp_path / "avaliacao"
    assert avaliar.main(["--dataset", str(dataset), "--config", _perfil(tmp_path), "--saida", str(saida),
                         "--resultados", str(tmp_path / "res"), "--guardar-erros"]) == 0
    texto = capsys.readouterr().out
    assert "Falsas rejeições: 0 de 2" in texto and "Defeitos escapados: 1 de 2" in texto
    linhas = {(x["classe"], x["sistema"]) for x in ler_csv(saida / "avaliacao_sacos.csv")}
    assert linhas == {("ok", "OK"), ("dimensao", "NOK"), ("dimensao", "OK")}
    (dimensao,) = ler_csv(saida / "avaliacao_classes.csv")
    assert (dimensao["responsavel"], dimensao["apanhados_pelo_responsavel"], dimensao["escapados"]) == \
        ("dimensoes", "1", "1")
    modulos = {x["modulo"]: x for x in ler_csv(saida / "avaliacao_modulos.csv")}
    assert modulos["cor"]["POR_IMPLEMENTAR"] == "4"
    assert len(list((saida / "escapados").iterdir())) == 1


def test_culpados_de_uma_falsa_rejeicao():
    a = avaliar.Avaliado("x.png", "ok", "NOK", {"localizacao": "OK", "dimensoes": "NOK", "cor": "ERRO"}, {}, 1.0)
    assert a.falsa_rejeicao and a.culpados == ["dimensoes"]
    sem_saco = avaliar.Avaliado("y.png", "ok", "SEM_SACO", {"localizacao": "SEM_SACO"}, {}, 1.0)
    assert sem_saco.culpados == ["localizacao"]


def test_intervalo_de_confianca_das_taxas():
    assert avaliar.intervalo_wilson(0, 380)[1] == pytest.approx(0.01, abs=1e-4), "0 em 380: até 1 %"
    baixo, alto = avaliar.intervalo_wilson(2, 120)
    assert baixo < 2 / 120 < alto
    assert avaliar.taxa(0, 0) == "sem sacos"
