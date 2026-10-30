"""Dashboard fora da BIapp: o estado em JSON, os dias, o servidor SSE e o cliente."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from nucleo import dashboard
from nucleo.contrato import Estado, TipoDefeito
from nucleo.especificacoes import OrdemFabrico, Repositorio, carimbo
from nucleo.servidor_dashboard import Servidor, eventos, interpretar
from testes.apoio import correr
from testes.test_especificacoes import _produto, _resultado

AGORA = 1_761_740_000.0  # 2025-10-29, um instante qualquer


@pytest.fixture
def repo(tmp_path):
    r = Repositorio(tmp_path / "especificacoes.sqlite")
    r.guardar(_produto())
    r.guardar(OrdemFabrico("OF1", "PreVital"))
    return r


def test_estado_tem_tudo_e_passa_a_json(repo):
    repo.registar(_resultado(Estado.OK, AGORA), "s", 1)
    repo.registar(_resultado(Estado.NOK, AGORA + 60, tipos=(TipoDefeito.DIMENSAO,)), "s", 2)
    e = json.loads(json.dumps(dashboard.estado(repo)))
    (of,) = e["ordens"]
    assert of["numero"] == "OF1" and of["colunas"][1:7] == ["OF1", "PreVital", 2, 1, 1, "50.0 %"]
    assert of["tipos"] == [["dimensao", 1]] and of["evolucao"] and "2 sacos" in of["resumo"]
    assert e["produtos"][0]["produto"] == "PreVital"
    dia = carimbo(AGORA)[1]
    assert [d["colunas"] for d in e["dias"]] == [[dia, 2, 1, 1, 0, "50.0 %"]]
    assert e["hoje"]["dia"] == carimbo()[1] and e["gerado"].startswith(carimbo()[1])


def test_historico_por_dia_e_versao(repo):
    v0 = repo.versao()
    repo.registar(_resultado(Estado.OK, AGORA), "s", 1)
    repo.registar(_resultado(Estado.NOK, AGORA + 86_400), "s", 2)  # no dia seguinte
    v1 = repo.versao()
    repo.marcar_decisao("s", 2, "descartado")
    assert len({v0, v1, repo.versao()}) == 3, "muda com cada saco e com cada decisão"
    assert repo.dias(10) == [carimbo(AGORA + 86_400)[1], carimbo(AGORA)[1]], "o mais recente primeiro"
    assert [i.estado for i in repo.historico(dia=carimbo(AGORA)[1])] == ["OK"]
    assert len(repo.historico(of="OF1")) == len(repo.historico()) == 2


def test_limpar_so_as_inspecoes_ou_tudo(repo):
    repo.registar(_resultado(Estado.OK, AGORA), "s", 1)
    antes = repo.versao()
    assert repo.limpar() == 1 and repo.versao() != antes
    assert repo.historico() == [] and repo.procurar_of("OF1") and repo.procurar_produto("PreVital"), "ficam"
    repo.registar(_resultado(Estado.OK, AGORA), "s", 1)
    assert repo.limpar(tudo=True) == 3, "1 inspeção, 1 OF, 1 produto"
    e = dashboard.estado(repo)
    assert (e["ordens"], e["produtos"], e["dias"]) == ([], [], [])


def test_interpretar_eventos_sse():
    linhas = [b"retry: 3000\n", b"\n", b": ping\n", b"\n", b"event: estado\n", b"id: 1\n", b'data: {"a":\n',
              b"data: 1}\n", b"\n", b"data: sem nome\r\n", b"\r\n"]
    assert list(interpretar(linhas)) == [("estado", '{"a":\n1}'), ("message", "sem nome")]


def test_servidor_envia_o_estado_por_sse_e_quando_muda(repo):
    servidor = Servidor(repo, "127.0.0.1", 0, 0.05).iniciar()  # porta 0: uma livre qualquer
    try:
        with urllib.request.urlopen(f"{servidor.url}/estado", timeout=10) as resposta:
            assert resposta.headers["Content-Type"].startswith("application/json")
            assert json.load(resposta)["ordens"][0]["colunas"][3] == 0
        recebidos = eventos(servidor.url, tempo_max_s=10)
        evento, dados = next(recebidos)
        assert evento == "estado" and json.loads(dados)["ordens"][0]["colunas"][3] == 0, "o estado ao ligar"
        repo.registar(_resultado(Estado.OK, AGORA), "s", 1)  # a BIapp regista um saco
        evento, dados = next(recebidos)
        assert evento == "estado" and json.loads(dados)["ordens"][0]["colunas"][3] == 1, "logo a seguir, sozinho"
        repo.limpar()  # o botão "Limpar dados"
        evento, dados = next(recebidos)
        assert json.loads(dados)["ordens"][0]["colunas"][3] == 0, "o estado limpo chega também sozinho"
        recebidos.close()
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(f"{servidor.url}/outra", timeout=10)
    finally:
        servidor.parar()


def test_dois_servidores_nao_partilham_a_porta(repo):
    servidor = Servidor(repo, "127.0.0.1", 0, 1.0).iniciar()
    try:
        with pytest.raises(OSError):
            Servidor(repo, "127.0.0.1", servidor.server_address[1], 1.0)
    finally:
        servidor.parar()


def test_cliente_sem_servidor_da_erro_de_ligacao():
    with socket.socket() as s:  # uma porta onde ninguém está à escuta
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    with pytest.raises(OSError):
        next(eventos(f"http://127.0.0.1:{porta}", tempo_max_s=2))


def test_ajuda_do_biapp_dashboard():
    p = correr("biapp_dashboard.py", "--help")
    assert p.returncode == 0 and "--so-servidor" in p.stdout and "--atalho" in p.stdout
