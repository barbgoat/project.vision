"""Especificações por produto e por OF: camadas da configuração, base de dados, procura, registo e resumo."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from mestre import Mestre
from modulos.m7_saida import Saida
from nucleo import dashboard
from nucleo.contrato import Captura, Defeito, Estado, Relatorio, Resultado, TipoDefeito
from nucleo.especificacoes import (
    ErroRepositorio, Especificacao, Inspecao, OrdemFabrico, Repositorio, ajustes_entre, compor, efetiva,
    repor_medidas, resolver, resumir,
)
from nucleo.imagens import guardar_imagem
from testes.apoio import COMPRIMENTO, FORMAS, LARGURA, cena_forma, config_teste, correr, execucao, ler_csv, montagem

CFG = config_teste()
IMG = np.zeros((20, 30, 3), np.uint8)


def _produto(nome: str = "PreVital", **campos) -> Especificacao:
    """O saco dos testes (150 × 55 mm, ± 3 mm), branco e com verniz, com os `campos` dados por cima."""
    return Especificacao(**{"produto": nome, "comprimento_nominal_mm": COMPRIMENTO, "largura_nominal_mm": LARGURA,
                            "tolerancia_comprimento_mm": 3.0, "tolerancia_largura_mm": 3.0, "cor": "branco",
                            "verniz": True, "autor": "admin", **campos})


@pytest.fixture
def repo(tmp_path):
    return Repositorio(tmp_path / "bd" / "especificacoes.sqlite")


# --- Camadas da configuração ------------------------------------------------------------

def test_camadas_montagem_produto_of():
    produto = _produto(comprimento_nominal_mm=120.0, tolerancia_comprimento_mm=2.0)
    of = OrdemFabrico("OF1", "PreVital", {"tolerancia_comprimento_mm": 4.0, "cor": "creme"})
    cfg = compor(CFG, produto, of)
    assert cfg.dimensoes.comprimento_nominal_mm == 120.0, "do produto"
    assert cfg.dimensoes.tolerancia_comprimento_mm == 4.0, "a OF por cima do produto"
    assert compor(CFG, produto).dimensoes.tolerancia_comprimento_mm == 2.0
    assert cfg.dimensoes.forma_raio_canto_mm == CFG.dimensoes.forma_raio_canto_mm, "os cantos são da montagem"
    assert (cfg.geral, cfg.localizacao, cfg.cor) == (CFG.geral, CFG.localizacao, CFG.cor), "a montagem fica"
    assert compor(CFG) == CFG
    assert efetiva(produto, of).cor == "creme" and ajustes_entre(produto, efetiva(produto, of)) == of.ajustes
    assert repor_medidas(cfg, CFG) == CFG


def test_a_of_so_muda_o_produto():
    with pytest.raises(ValueError, match="A OF 7 só pode mudar .*não: px_por_mm"):
        compor(CFG, _produto(), OrdemFabrico("7", "PreVital", {"px_por_mm": 9.0}))


def test_a_composicao_e_validada_pela_config():
    with pytest.raises(ValueError, match="(?s)'PreVital'.*tolerancia_comprimento_mm tem de ser"):
        compor(CFG, _produto(tolerancia_comprimento_mm=-1.0))
    with pytest.raises(ValueError, match="fora dos limites de localizacao"):  # o M2 rejeitaria todos os sacos
        compor(CFG, _produto(comprimento_nominal_mm=400.0))
    with pytest.raises(ValueError, match="'PreVital' \\(OF 7\\)"):
        compor(CFG, _produto(), OrdemFabrico("7", "PreVital", {"largura_nominal_mm": "cinquenta"}))


# --- Base de dados ---------------------------------------------------------------------

def test_guardar_e_procurar_sem_distinguir_maiusculas(repo):
    repo.guardar(_produto(cor=" branco "))
    p = repo.procurar_produto("  prevital ")
    assert p == _produto(data_hora=p.data_hora), "volta igual da base de dados (a cor sem espaços)"
    assert repo.procurar_produto("outro") is None and repo.procurar_of("1") is None

    of = repo.guardar(OrdemFabrico(" OF1 ", "prevital", autor="Ana (nº 7)"))
    assert (of.numero, of.produto) == ("OF1", "PreVital"), "o nome como está no produto"
    assert repo.procurar_of("of1") == of


def test_datas_pelo_relogio_do_pc_com_o_dia(repo):
    antes = datetime.now()
    p = repo.guardar(_produto())
    assert antes.replace(microsecond=0) <= datetime.fromisoformat(p.data_hora) <= datetime.now()
    repo.registar(_resultado(Estado.OK, datetime(2026, 10, 29, 23, 59, 30).timestamp()), "s", 1)
    with closing(sqlite3.connect(repo.caminho)) as bd:
        assert bd.execute("SELECT dia FROM produtos").fetchone()[0] == p.data_hora[:10]
        assert bd.execute("SELECT data_hora, dia FROM inspecoes").fetchone() == \
            ("2026-10-29 23:59:30.000", "2026-10-29")


def test_base_de_dados_antiga_ganha_a_coluna_dia(tmp_path):
    caminho = tmp_path / "antiga.sqlite"
    with closing(sqlite3.connect(caminho)) as bd:
        bd.execute("CREATE TABLE produtos (id INTEGER PRIMARY KEY, produto TEXT, dados TEXT, autor TEXT, "
                   "data_hora TEXT)")
        bd.execute("CREATE TABLE inspecoes (id INTEGER PRIMARY KEY, data_hora TEXT, sessao TEXT, saco INTEGER, "
                   "of TEXT, produto TEXT, estado TEXT, decisao TEXT DEFAULT '', tipos TEXT, comprimento_mm REAL, "
                   "largura_mm REAL)")
        bd.execute("INSERT INTO inspecoes (data_hora, of, estado) VALUES ('2026-10-29 10:00:00', 'OF1', 'OK')")
        bd.commit()
    repo = Repositorio(caminho)
    repo.guardar(_produto())
    assert repo.historico("OF1")[0].pedido == {}, "os sacos antigos ficam sem pedido"


def test_cada_gravacao_e_uma_versao_nova(repo):
    repo.guardar(_produto(tolerancia_comprimento_mm=2.0))
    repo.guardar(_produto(tolerancia_comprimento_mm=5.0))
    assert repo.procurar_produto("PreVital").tolerancia_comprimento_mm == 5.0
    assert [p.produto for p in repo.produtos()] == ["PreVital"]
    with closing(sqlite3.connect(repo.caminho)) as bd:
        assert bd.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 2, "a versão antiga fica"


def test_listas_e_procura(repo):
    for nome in ("PreVital", "Bolachas"):
        repo.guardar(_produto(nome))
    repo.guardar(OrdemFabrico("2026-01", "PreVital"))
    repo.guardar(OrdemFabrico("2026-02", "Bolachas"))
    assert [p.produto for p in repo.produtos()] == ["Bolachas", "PreVital"]
    assert [p.produto for p in repo.produtos("vit")] == ["PreVital"]
    assert [o.numero for o in repo.ordens()] == ["2026-02", "2026-01"], "as mais recentes primeiro"
    assert [o.numero for o in repo.ordens("bolach")] == ["2026-02"]
    assert repo.ultima_of("prevital").numero == "2026-01"


def test_guardar_valida(repo, tmp_path):
    with pytest.raises(ValueError, match="nome do produto"):
        repo.guardar(Especificacao("  "))
    with pytest.raises(ValueError, match="não existe"):
        repo.guardar(OrdemFabrico("1", "Desconhecido"))
    repo.guardar(_produto())
    with pytest.raises(ValueError, match="só pode mudar"):
        repo.guardar(OrdemFabrico("1", "PreVital", {"forma_raio_canto_mm": 4.0}))
    (tmp_path / "ficheiro").write_text("")
    with pytest.raises(ErroRepositorio):  # p.ex. sem acesso à pasta (ou, mais tarde, sem rede)
        Repositorio(tmp_path / "ficheiro" / "bd.sqlite")


# --- Procura por OF e por nome ---------------------------------------------------------

def test_resolver_por_of_e_por_produto(repo):
    with pytest.raises(ValueError, match="Indica"):
        resolver(repo, "", " ")
    e = resolver(repo, "OF1", "Novo")
    assert e.produto is None and e.of.numero == "OF1" and e.nova, "nenhum dos dois: criar a especificação"
    assert resolver(repo, "", "Novo").of is None

    repo.guardar(_produto())
    e = resolver(repo, "", "prevital")
    assert e.produto.produto == "PreVital" and e.of is None and not e.nova
    e = resolver(repo, "OF1", "PreVital")
    assert e.nova and e.of == OrdemFabrico("OF1", "PreVital"), "OF nova de um produto conhecido"

    repo.guardar(OrdemFabrico("OF1", "PreVital", {"tolerancia_largura_mm": 5.0}))
    e = resolver(repo, "of1", "")
    assert not e.nova and e.of.numero == "OF1" and e.produto.produto == "PreVital"
    e = resolver(repo, "OF2", "PreVital")
    assert e.nova and e.of.ajustes == {"tolerancia_largura_mm": 5.0}, "as últimas usadas"
    with pytest.raises(ValueError, match="é do produto 'PreVital'"):
        resolver(repo, "OF1", "Outro")


# --- Registo e inspeções ---------------------------------------------------------------

def test_registo_tem_a_of_e_o_produto(tmp_path):
    cfg = compor(CFG, _produto())
    imagem = cena_forma(np.random.default_rng(3), FORMAS["comprido_158"])
    resultado = Mestre(cfg, of="OF1", produto="PreVital").inspecionar(imagem)
    resultado.captura = Captura(imagem, "saco.png", 1_700_000_000.0)
    sem_of = Mestre(cfg).inspecionar(imagem)
    with Saida(cfg, tmp_path, ["localizacao", "dimensoes"]) as saida:
        saida.registar(resultado)
        saida.registar(sem_of)
    com, sem = ler_csv(saida.pasta / "registo.csv")
    assert (com["of"], com["produto"], com["estado"]) == ("OF1", "PreVital", "NOK")
    assert (sem["of"], sem["produto"]) == ("", "")
    defeitos = ler_csv(saida.pasta / "defeitos.csv")
    assert {(d["of"], d["produto"]) for d in defeitos} == {("OF1", "PreVital"), ("", "")}, "os dois sacos são NOK"


def _resultado(estado: Estado, instante: float, of: str = "OF1", tipos: tuple[TipoDefeito, ...] = (),
               saco=None) -> Resultado:
    relatorio = Relatorio("dimensoes", estado, [Defeito(t) for t in tipos])
    return Resultado(IMG, estado, saco, [relatorio], 1.0, captura=Captura(IMG, "x.png", instante), of=of,
                     produto="PreVital")


def test_cada_saco_guarda_o_medido_e_o_pedido(repo):
    saco = SimpleNamespace(comprimento_mm=151.2, largura_mm=55.4)
    repo.registar(_resultado(Estado.OK, 1_700_000_000.0, saco=saco), "s", 1, compor(CFG, _produto()))
    repo.registar(_resultado(Estado.ERRO, 1_700_000_001.0), "s", 2)
    com, sem = repo.historico("OF1")
    assert (com.comprimento_mm, com.largura_mm) == (151.2, 55.4)
    assert com.pedido == {"comprimento_nominal_mm": COMPRIMENTO, "largura_nominal_mm": LARGURA,
                          "tolerancia_comprimento_mm": 3.0, "tolerancia_largura_mm": 3.0}
    assert com.fora("comprimento_mm") is False and sem.fora("comprimento_mm") is None


def test_inspecoes_e_decisoes_na_base_de_dados(repo):
    repo.registar(_resultado(Estado.OK, 1_700_000_000.0), "sessao1", 1)
    repo.registar(_resultado(Estado.NOK, 1_700_000_001.0, tipos=(TipoDefeito.DIMENSAO,)), "sessao1", 2)
    repo.registar(_resultado(Estado.OK, 1_700_000_002.0, of="OF2"), "sessao1", 3)
    repo.marcar_decisao("sessao1", 2, "descartado")
    historico = repo.historico("of1")
    assert [(i.estado, i.decisao, i.final) for i in historico] == [("OK", "", "OK"), ("NOK", "descartado", "descartado")]
    assert historico[1].tipos == {"dimensao": 1} and historico[0].produto == "PreVital"


def test_resumo_da_of():
    def inspecao(hora, estado, decisao="", tipos=None):
        return Inspecao(f"2026-10-29 {hora}", "OF1", "P", estado, decisao, tipos or {}, None, None)
    inspecoes = [inspecao("10:05:00", "OK"), inspecao("10:40:00", "NOK", "NOK", {"dimensao": 2}),
                 inspecao("11:10:00", "ERRO"), inspecao("11:15:00", "NOK", "descartado", {"risco": 1}),
                 inspecao("11:20:00.500", "OK")]
    r = resumir(inspecoes)
    assert (r.ok, r.nok, r.descartados, r.total) == (2, 2, 1, 5)
    assert r.taxa_nok == 0.5, "os descartados não contam"
    assert r.tipos == {"dimensao": 2}, "sem os descartados"
    assert r.evolucao == [("2026-10-29 10:00", 1, 1), ("2026-10-29 11:00", 1, 1)]
    assert [p for p, _, _ in resumir(inspecoes, 30).evolucao] == \
        ["2026-10-29 10:00", "2026-10-29 10:30", "2026-10-29 11:00"]
    assert resumir([]).taxa_nok == 0.0 and resumir(inspecoes).medidas == {}, "sem medidas nem pedido"


def test_resumo_confere_cada_medida_com_o_pedido_do_seu_momento():
    def inspecao(comprimento, tolerancia, decisao=""):
        pedido = {"comprimento_nominal_mm": 150.0, "tolerancia_comprimento_mm": tolerancia,
                  "largura_nominal_mm": 55.0, "tolerancia_largura_mm": 3.0}
        return Inspecao("2026-10-29 10:00:00", "OF1", "P", "OK", decisao, {}, comprimento, 55.0, pedido)
    r = resumir([inspecao(151.0, 2.0), inspecao(153.0, 2.0), inspecao(153.0, 4.0), inspecao(170.0, 4.0, "descartado")])
    c = r.medidas["comprimento_mm"]
    assert (c.nominal, c.tolerancia, c.n, c.fora) == (150.0, 4.0, 3, 1), "153 fora com ± 2, dentro com ± 4"
    assert (c.minimo, c.maximo) == (151.0, 153.0) and c.media == pytest.approx(152.333, abs=1e-3)
    assert r.medidas["largura_mm"].fora == 0
    assert dashboard.linhas_medidas(r)[0] == ["comprimento", "150 ± 4 mm", "152.3 ± 0.9 mm", "151.0 a 153.0 mm",
                                              "1 de 3"]


def test_main_com_of_usa_a_especificacao_e_regista(tmp_path):
    rng = np.random.default_rng(5)
    for nome in ("certo", "comprido_158"):
        guardar_imagem(tmp_path / "fotos" / f"{nome}.png", cena_forma(rng, FORMAS[nome]))
    (tmp_path / "perfil.json").write_text(json.dumps(montagem()), encoding="utf-8")
    repo = Repositorio(tmp_path / "res" / "especificacoes.sqlite")
    repo.guardar(_produto(tolerancia_comprimento_mm=9.0))  # com 9 mm de tolerância os 158 mm passam

    argumentos = ["main.py", "--pasta", tmp_path / "fotos", "--config", tmp_path / "perfil.json",
                  "--saida", tmp_path / "res"]
    p = correr(*argumentos, "--of", "OF7", "--produto", "PreVital")
    assert p.returncode == 0, p.stderr
    linhas = ler_csv(execucao(tmp_path / "res") / "registo.csv")
    assert [(l["of"], l["produto"], l["estado"]) for l in linhas] == [("OF7", "PreVital", "OK")] * 2
    assert repo.procurar_of("OF7").autor == "main.py", "a OF nova fica associada ao produto"
    historico = repo.historico("OF7")
    assert len(historico) == 2 and historico[0].pedido["tolerancia_comprimento_mm"] == 9.0
    c = resumir(historico).medidas["comprimento_mm"]
    assert (c.nominal, c.n, c.fora) == (COMPRIMENTO, 2, 0) and 157 < c.maximo < 159, "o 158 dentro de ± 9"

    p = correr(*argumentos, "--produto", "Desconhecido")
    assert p.returncode == 2 and "não tem especificação" in p.stderr


# --- Dashboard (dados, sem interface) -----------------------------------------------------

def test_dashboard_cria_e_muda_produtos_e_ofs(repo):
    produto, of = dashboard.criar_produto(repo, CFG, _produto(" PreVital "), numero_of="OF1")
    assert produto.produto == "PreVital" and of.numero == "OF1" and of.ajustes == {}
    with pytest.raises(ValueError, match="Já existe um produto"):
        dashboard.criar_produto(repo, CFG, _produto("prevital"))
    with pytest.raises(ValueError, match="A OF OF1 já existe"):
        dashboard.criar_produto(repo, CFG, _produto("Outro"), numero_of="OF1")
    with pytest.raises(ValueError, match="fora dos limites"):  # validado com a montagem antes de se guardar
        dashboard.criar_produto(repo, CFG, _produto("Enorme", comprimento_nominal_mm=400.0))
    assert repo.procurar_produto("Enorme") is None

    of = dashboard.guardar_of(repo, CFG, "OF1", produto, _produto(tolerancia_comprimento_mm=4.0), "Ana (nº 7)")
    assert of.ajustes == {"tolerancia_comprimento_mm": 4.0} and of.autor == "Ana (nº 7)"
    assert dashboard.descrever_ajustes(of) == "tolerancia_comprimento_mm = 4.0"
    dashboard.guardar_produto(repo, CFG, _produto(verniz=False))
    assert not repo.procurar_produto("PreVital").verniz

    escolha = dashboard.criar_of(repo, resolver(repo, "OF2", "PreVital"), "Rui (nº 3)")
    assert not escolha.nova and escolha.of.data_hora, "a OF ficou guardada"
    assert repo.procurar_of("OF2").autor == "Rui (nº 3)" and repo.procurar_of("OF2").ajustes == of.ajustes


def test_dashboard_lista_as_ofs_com_as_contagens(repo):
    repo.guardar(_produto())
    repo.guardar(OrdemFabrico("OF1", "PreVital"))
    repo.registar(_resultado(Estado.OK, 1_700_000_000.0), "s", 1)
    repo.registar(_resultado(Estado.NOK, 1_700_000_060.0, tipos=(TipoDefeito.RISCO,)), "s", 2)
    (linha,) = dashboard.ordens(repo, "prev")
    assert linha.colunas()[0] == repo.procurar_of("OF1").data_hora[:16], "a data primeiro"
    assert linha.colunas()[1:7] == ["OF1", "PreVital", 2, 1, 1, "50.0 %"]
    assert len(linha.colunas()) == len(dashboard.COLUNAS_OF)
    assert dashboard.resumo_of(repo, "OF1").tipos == {"risco": 1}
    colunas = dashboard.colunas_produto(repo.procurar_produto("PreVital"))
    assert len(colunas) == len(dashboard.COLUNAS_PRODUTOS)
    assert colunas[1:6] == ["PreVital", "150 × 55", "± 3 / ± 3", "branco", "sim"]
