"""Regras da arquitetura Mestre–Escravos, configuração e isolamento de falhas no Mestre."""

from __future__ import annotations

import ast
import dataclasses
import json
import py_compile

import numpy as np
import pytest

import mestre as m
from ferramentas import afinar, avaliar
from nucleo import config
from nucleo import defines as D
from nucleo.config import RAIZ, Config, carregar_configuracao, nome_define
from nucleo.contrato import Defeito, Escravo, Estado, Relatorio, Saco, TipoDefeito
from testes.apoio import com_acentos

PERFIS = sorted((RAIZ / "configuracoes").glob("*.json"))
MONTAGENS = sorted(p for p in (RAIZ / "dataset").iterdir() if p.is_dir())


def _ficheiros_do_projeto():
    excluir = {".venv", "instaladores", "resultados"}
    return [p for p in RAIZ.rglob("*.py") if not excluir & set(p.relative_to(RAIZ).parts)]


# --- Código e regras ---------------------------------------------------------------------

def test_todos_os_ficheiros_compilam():
    for f in _ficheiros_do_projeto():
        py_compile.compile(str(f), doraise=True)


def test_modulos_so_importam_do_nucleo():
    violacoes = []
    for f in (RAIZ / "modulos").rglob("*.py"):
        proprio = f.relative_to(RAIZ / "modulos").parts[0]
        for no in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(no, ast.ImportFrom) and no.module:
                nomes = [no.module]
            elif isinstance(no, ast.Import):
                nomes = [a.name for a in no.names]
            else:
                continue
            for n in nomes:
                if n.startswith("modulos.") and n.split(".")[1] != proprio:
                    violacoes.append(f"{f.relative_to(RAIZ)} importa {n}")
                if n in ("mestre", "main") or n.startswith("ferramentas"):
                    violacoes.append(f"{f.relative_to(RAIZ)} importa {n}")
    assert not violacoes


# --- Defines e configuração --------------------------------------------------------------

def test_cada_parametro_tem_o_seu_define_com_o_mesmo_valor():
    cfg = Config()
    nomes_param, sem_define, diferentes = set(), [], []
    for secao, valores in dataclasses.asdict(cfg).items():
        for nome, valor in valores.items():
            nd = nome_define(secao, nome)
            nomes_param.add(nd)
            if not hasattr(D, nd):
                sem_define.append(nd)
            elif (list(getattr(D, nd)) if isinstance(valor, list) else getattr(D, nd)) != valor:
                diferentes.append(nd)
    # Os defines M2_, M3_... são afinações internas, sem parâmetro na configuração
    defines_secao = {n for n in dir(D) if n.isupper() and (not n.startswith("M") or n.startswith("MANCHAS_"))}
    assert not sem_define
    assert not diferentes
    assert not sorted(defines_secao - nomes_param), "defines de secção sem parâmetro"


def test_sem_config_json_valem_so_os_defines(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CAMINHO_POR_OMISSAO", tmp_path / "config.json")
    assert carregar_configuracao(tmp_path / "config.json") == Config()


def test_config_pedido_que_nao_existe_da_erro(tmp_path):
    with pytest.raises(FileNotFoundError):
        carregar_configuracao(tmp_path / "nao_existe.json")


def test_conversoes_mm_px():
    cfg, k = Config(), D.GERAL_PX_POR_MM
    assert cfg.mm_para_px(2) == 2 * k
    assert cfg.px_para_mm(10 * k) == 10
    assert cfg.mm2_para_px2(4) == 4 * k * k


def test_gralha_num_parametro_diz_a_secao():
    with pytest.raises(ValueError, match=r"relevo\.crista_limar_alto"):
        Config.de_dict({"relevo": {"crista_limar_alto": 0.1}})
    with pytest.raises(ValueError, match="relevos"):
        Config.de_dict({"relevos": {}})


def test_varios_erros_aparecem_juntos():
    with pytest.raises(ValueError) as erro:
        Config.de_dict({"geral": {"px_por_mm": 0}, "relevo": {"canal": "x"},
                        "dimensoes": {"comprimento_nominal_mm": 500.0}})
    for parametro in ("geral.px_por_mm", "relevo.canal", "dimensoes.comprimento_nominal_mm"):
        assert parametro in str(erro.value)


def test_tipos_convertidos_e_secoes_em_falta_por_omissao():
    c = Config.de_dict({"geral": {"px_por_mm": 8}, "cor": {"referencia_lab": [60, 1, 2]}})
    assert c.geral.px_por_mm == 8.0 and type(c.geral.px_por_mm) is float
    assert c.cor.referencia_lab == [60.0, 1.0, 2.0]
    assert c.relevo == Config().relevo


def test_replace_de_uma_secao_revalida_tudo():
    cfg = Config()
    with pytest.raises(ValueError):
        dataclasses.replace(cfg, relevo=dataclasses.replace(cfg.relevo, crista_limiar_baixo=0.9))


def test_validacao_cruzada_camara():
    with pytest.raises(ValueError, match="fps"):
        Config.de_dict({"disparo": {"fps": D.CAMARA_FPS_MAX + 1}})
    with pytest.raises(ValueError, match="exposicao_us"):
        Config.de_dict({"aquisicao": {"exposicao_us": 1e6 / D.DISPARO_FPS + 1000}})


def test_guardar_e_carregar_com_acentos(tmp_path):
    pasta = com_acentos(tmp_path)
    caminho = pasta / "configuração ç.json"
    caminho.write_text(json.dumps({"manchas": {"ativo": False}}), encoding="utf-8-sig")
    assert Config.carregar(caminho).manchas.ativo is False

    c = Config.de_dict({"geral": {"px_por_mm": 12.4}, "cor": {"ativo": False}})
    caminho = pasta / "telemóvel.json"
    c.guardar(caminho)
    assert json.loads(caminho.read_text(encoding="utf-8")) == {"geral": {"px_por_mm": 12.4}, "cor": {"ativo": False}}
    assert Config.carregar(caminho) == c
    assert c.descrever_alteracoes() == [
        f"geral.px_por_mm = 12.4 (define GERAL_PX_POR_MM = {D.GERAL_PX_POR_MM})",
        "cor.ativo = False (define COR_ATIVO = True)",
    ]
    c.guardar(pasta / "completo.json", completo=True)
    completo = json.loads((pasta / "completo.json").read_text(encoding="utf-8"))
    assert len(completo["relevo"]) == len(dataclasses.fields(Config().relevo))


@pytest.mark.parametrize("perfil", PERFIS, ids=[p.stem for p in PERFIS])
def test_perfis_de_configuracao_carregam(perfil):
    cfg = carregar_configuracao(perfil)
    assert cfg.alteracoes(), "um perfil sem alterações não serve para nada"


def test_ha_perfis():
    assert {p.stem for p in PERFIS} >= {"telemovel", "bancada_botao", "bancada_virtual", "linha"}


# --- Ferramentas e dataset ---------------------------------------------------------------

@pytest.mark.parametrize("montagem", MONTAGENS, ids=[p.name for p in MONTAGENS])
def test_classes_do_avaliar_sao_as_pastas_do_dataset(montagem):
    assert sorted(p.name for p in montagem.iterdir() if p.is_dir()) == sorted(avaliar.CLASSES)


def test_parametros_do_afinar_estao_na_grelha():
    for secao, lista in afinar.PARAMETROS.items():
        s_cfg = getattr(Config(), secao)
        for nome, minimo, maximo, passo in lista:
            v = getattr(s_cfg, nome)
            assert minimo <= v <= maximo, f"{secao}.{nome}"
            assert abs((v - minimo) / passo - round((v - minimo) / passo)) < 1e-9, f"{secao}.{nome}"


@pytest.mark.parametrize("ferramenta, argumentos", [(afinar, ["relevo", "x.png"]), (avaliar, [])])
def test_ferramentas_por_fazer(ferramenta, argumentos):
    with pytest.raises(NotImplementedError):
        ferramenta.main(argumentos)


# --- Mestre ------------------------------------------------------------------------------

IMG = np.zeros((40, 60, 3), np.uint8)


def test_ordem_e_escolha_dos_modulos():
    cfg = Config()
    assert m.Mestre(cfg).nomes == ["localizacao", "dimensoes", "cor", "relevo", "manchas"]
    with pytest.raises(ValueError):
        m.Mestre(cfg, apenas=["xpto"])
    sem_cor = dataclasses.replace(cfg, cor=dataclasses.replace(cfg.cor, ativo=False))
    assert "cor" not in m.Mestre(sem_cor).nomes
    assert m.Mestre(sem_cor, apenas=["cor"]).nomes == ["localizacao", "cor"]


def test_imagem_sem_saco_da_sem_saco_com_motivo():
    r = m.Mestre(Config()).inspecionar(IMG.copy())
    loc = r.relatorio("localizacao")
    assert r.estado is Estado.SEM_SACO and loc.estado is Estado.SEM_SACO and r.saco is None
    assert any(t in loc.mensagem for t in ("fora dos limites", "nada difere", "borda"))


def _saco_falso(*_):
    z = np.zeros((50, 80), np.uint8)
    return Saco(np.zeros((4, 1, 2), np.int32), (40.0, 25.0), 0.0, 150.0, 55.0, np.eye(2, 3),
                np.zeros((50, 80, 3), np.uint8), z.copy(), z.copy())


@pytest.fixture
def saco_falso(monkeypatch):
    """O M2 devolve sempre o mesmo saco: testa o Mestre sem depender da imagem."""
    monkeypatch.setattr(m.Localizador, "localizar", lambda self, img, *a: _saco_falso())


class Bom(Escravo):
    nome, pergunta = "dimensoes", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return Relatorio.concluir(self.nome, [], {"comprimento_mm": saco.comprimento_mm})


class ComDefeitos(Escravo):
    nome, pergunta = "relevo", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return Relatorio.concluir(self.nome, [Defeito(TipoDefeito.RISCO, 1, 2, 20, 0.4),
                                              Defeito(TipoDefeito.RUGAS, 30, 0, 25, 15)])


class Rebenta(Escravo):
    nome, pergunta = "cor", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return 1 / 0


class EscreveNaImagem(Escravo):
    nome, pergunta = "manchas", "?"

    def analisar(self, saco, guardar_intermedios=False):
        saco.imagem[0, 0] = 255  # proibido
        return Relatorio.concluir(self.nome, [])


class MudaOSaco(Escravo):
    nome, pergunta = "manchas", "?"

    def analisar(self, saco, guardar_intermedios=False):
        saco.comprimento_mm = 1.0  # proibido
        return Relatorio.concluir(self.nome, [])


class FalhaOContrato(Escravo):
    nome, pergunta = "manchas", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return "não sou um Relatorio"


def mestre_com(*classes, politica="rejeitar"):
    cfg = Config()
    cfg = dataclasses.replace(cfg, geral=dataclasses.replace(cfg.geral, em_caso_de_erro=politica))
    mestre = m.Mestre(cfg, apenas=[])
    mestre.escravos = [k(cfg) for k in classes]
    return mestre


def test_tudo_ok_da_ok(saco_falso):
    r = mestre_com(Bom).inspecionar(IMG.copy())
    assert r.estado is Estado.OK and r.ok and r.relatorio("dimensoes").tempo_ms >= 0
    assert r.relatorio("localizacao").medidas["comprimento_mm"] == 150.0


def test_defeitos_dao_nok_com_o_modulo(saco_falso):
    r = mestre_com(Bom, ComDefeitos).inspecionar(IMG.copy())
    assert r.estado is Estado.NOK and len(r.defeitos) == 2
    assert all(d.modulo == "relevo" for d in r.defeitos)
    assert r.relatorio("relevo").resumo == "NOK(2)"


def test_escravo_que_rebenta_da_erro_com_o_sitio(saco_falso):
    r = mestre_com(Bom, Rebenta).inspecionar(IMG.copy())
    cor = r.relatorio("cor")
    assert r.estado is Estado.ERRO and cor.estado is Estado.ERRO
    assert "ZeroDivisionError" in cor.mensagem and "testes/test_arquitetura.py" in cor.mensagem
    assert "Traceback" in cor.detalhe
    assert r.relatorio("dimensoes").estado is Estado.OK, "os outros continuam"


def test_politica_ignorar_e_nok_prevalece(saco_falso):
    assert mestre_com(Bom, Rebenta, politica="ignorar").inspecionar(IMG.copy()).estado is Estado.OK
    assert mestre_com(Rebenta, ComDefeitos).inspecionar(IMG.copy()).estado is Estado.NOK


@pytest.mark.parametrize("classe, texto", [(EscreveNaImagem, "read-only"), (MudaOSaco, "FrozenInstanceError"),
                                           (FalhaOContrato, "contrato")])
def test_violacoes_do_contrato_ficam_no_modulo(saco_falso, classe, texto):
    r = mestre_com(classe).inspecionar(IMG.copy())
    assert r.relatorio("manchas").estado is Estado.ERRO and texto in r.relatorio("manchas").mensagem


def test_nunca_aprova_sem_inspecao(saco_falso):
    r = mestre_com().inspecionar(IMG.copy())
    assert r.estado is Estado.ERRO and "nenhum escravo" in r.mensagem


def test_imagem_original_fica_so_de_leitura(saco_falso):
    img = IMG.copy()
    mestre_com(Bom).inspecionar(img)
    assert not img.flags.writeable


def test_sem_saco_nao_chama_mais_ninguem(monkeypatch):
    monkeypatch.setattr(m.Localizador, "localizar", lambda self, img, *a: None)
    r = mestre_com(Bom).inspecionar(IMG.copy())
    assert r.estado is Estado.SEM_SACO and len(r.relatorios) == 1


def test_escravos_reais_m3_decide_enquanto_m4_m6_por_implementar(saco_falso):
    r = m.Mestre(Config()).inspecionar(IMG.copy())
    estados = {x.modulo: x.estado for x in r.relatorios}
    assert estados["dimensoes"] in (Estado.OK, Estado.NOK)
    assert all(estados[k] is Estado.POR_IMPLEMENTAR for k in ("cor", "relevo", "manchas"))
    assert r.estado is estados["dimensoes"]
    assert "m5_relevo/preprocessamento.py" in r.relatorio("relevo").mensagem
