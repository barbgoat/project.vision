"""Ferramenta rever_sessao: a amostra de uma sessão da BIapp vai para o dataset (sem janela)."""

from __future__ import annotations

import dataclasses
from collections import Counter

import numpy as np
import pytest

from ferramentas import rever_sessao as rv
from modulos.m7_saida import Saida
from nucleo.contrato import Captura, Estado, Resultado
from testes.apoio import config_teste, ler_csv

IMG = np.full((60, 80, 3), 90, np.uint8)
ESTADOS = (Estado.OK, Estado.NOK, Estado.OK, Estado.SEM_SACO, Estado.OK)


@pytest.fixture
def sessao(tmp_path):
    """Uma sessão com 5 sacos (3 OK, 1 NOK, 1 SEM_SACO) e os originais todos guardados."""
    cfg = config_teste()
    cfg = dataclasses.replace(cfg, saida=dataclasses.replace(cfg.saida, guardar_originais="todas",
                                                             guardar_imagens="todas"))
    with Saida(cfg, tmp_path / "resultados", ["localizacao"]) as saida:
        for i, estado in enumerate(ESTADOS):
            saida.registar(Resultado(IMG, estado, None, [], 1.0, captura=Captura(IMG, f"cam#{i}", 1_700_000_000.0 + i)))
    return saida.pasta


def test_sacos_de_cada_pasta_pelo_registo(sessao):
    assert len(rv.sacos(sessao, "ok")) == 3 and len(rv.sacos(sessao, "nok")) == 1
    erro = rv.sacos(sessao, "erro")
    assert [s.estado for s in erro] == ["SEM_SACO"] and erro[0].original.parent.name == "erro"
    assert erro[0].anotada is not None and erro[0].anotada.is_file()
    next(sessao.rglob("*000001*_original.png")).unlink()  # o limite já o apagou
    assert len(rv.sacos(sessao, "ok")) == 2, "sem o original no disco, não se revê"


def test_amostra_ao_acaso_pela_ordem_e_repetivel(sessao):
    todos = rv.sacos(sessao, "ok")
    duas = rv.amostra(todos, 2, semente=3)
    assert len(duas) == 2 and duas == rv.amostra(todos, 2, semente=3)
    assert duas == [s for s in todos if s in duas], "pela ordem em que passaram"
    assert rv.amostra(todos, 0) == todos and rv.amostra(todos, 99) == todos


def test_classificar_copia_regista_e_nao_repete(sessao, tmp_path):
    dataset, registo = tmp_path / "dataset" / "linha", sessao.parent / "correcoes.csv"
    ok = rv.sacos(sessao, "ok")
    copia = rv.classificar(ok[0], "ok", dataset, registo)
    falso_ok = rv.classificar(ok[1], "rugas", dataset, registo)
    assert copia.parent == dataset / "ok" and falso_ok.parent == dataset / "rugas"
    assert copia.name.startswith(sessao.name) and copia.read_bytes() == ok[0].original.read_bytes()
    linhas = ler_csv(registo)
    assert [(linha["estado_sistema"], linha["classe_real"]) for linha in linhas] == [("OK", "ok"), ("OK", "rugas")]
    assert rv.ja_revistos(registo) == {str(ok[0].original), str(ok[1].original)}
    with pytest.raises(ValueError, match="desconhecida"):
        rv.classificar(ok[2], "outra", dataset, registo)


def test_resumo_conta_os_erros_do_sistema():
    assert "Falsos OK (defeitos que passaram): 1 em 4 (25.0 %)" in rv.resumo("ok", Counter(ok=3, rugas=1))
    assert "Falsos NOK (bons rejeitados): 2 em 2" in rv.resumo("nok", Counter(ok=2))
    assert rv.resumo("ok", Counter()) == "Nenhum saco decidido."


def test_vista_poe_original_e_anotada_lado_a_lado(sessao):
    saco = rv.sacos(sessao, "nok")[0]
    imagem = rv.vista(saco, "1/1")
    assert imagem.ndim == 3 and imagem.shape[1] == 2 * IMG.shape[1], "as duas, à mesma altura"


def test_sessao_sem_registo_da_erro(tmp_path):
    assert rv.main([str(tmp_path), "--dataset", str(tmp_path / "d")]) == 2


def test_tudo_ja_revisto_nao_abre_janela(sessao, tmp_path):
    dataset = tmp_path / "d"
    for saco in rv.sacos(sessao, "nok"):
        rv.classificar(saco, "dimensao", dataset, sessao.parent / "correcoes.csv")
    assert rv.main([str(sessao), "--dataset", str(dataset), "--pasta", "nok"]) == 0
