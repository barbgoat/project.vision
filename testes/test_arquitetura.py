"""Regras da arquitetura Mestre–Escravos, configuração e isolamento de falhas no Mestre."""

from __future__ import annotations

import ast
import dataclasses
import json
import py_compile
import re
import time

import cv2
import numpy as np
import pytest

import mestre as m
from ferramentas import avaliar
from nucleo import config
from nucleo import defines as D
from nucleo.config import RAIZ, Config, carregar_configuracao, nome_define
from nucleo.contrato import CLASSES_DATASET, Defeito, Escravo, Estado, Relatorio, Saco, TipoDefeito
from nucleo.imagens import guardar_imagem
from testes.apoio import MONTAGEM, cena_saco, com_acentos, config_teste

PERFIS = sorted((RAIZ / "configuracoes").glob("*.json"))


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
    defines_secao = {n for n in dir(D) if n.isupper() and not n.startswith("M")}
    assert not sem_define
    assert not diferentes
    assert not sorted(defines_secao - nomes_param), "defines de secção sem parâmetro"


def test_valores_medir_fixados_nos_testes():
    """Os [MEDIR] vão mudar com a montagem real: os testes fixam-nos todos em MONTAGEM (testes/apoio.py)."""
    medir, pendente = set(), False
    for linha in (RAIZ / "nucleo" / "defines.py").read_text(encoding="utf-8").splitlines():
        pendente = pendente or linha.startswith("# [MEDIR]")  # [MEDIR] no comentário por cima do define
        if define := re.match(r"([A-Z0-9_]+): Final", linha):
            if pendente or "[MEDIR]" in linha:
                medir.add(define[1])
            pendente = False
        elif not linha.startswith("#"):
            pendente = False
    fixados = {nome_define(secao, nome) for secao, valores in MONTAGEM.items() for nome in valores}
    assert medir, "nenhum [MEDIR] encontrado em defines.py"
    assert not sorted(medir - fixados), "acrescentar estes valores a MONTAGEM em testes/apoio.py"


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


def test_gralha_num_parametro_diz_a_secao():
    with pytest.raises(ValueError, match=r"relevo\.crista_limar_alto"):
        Config.de_dict({"relevo": {"crista_limar_alto": 0.1}})
    with pytest.raises(ValueError, match="relevos"):
        Config.de_dict({"relevos": {}})


def test_varios_erros_aparecem_juntos():
    with pytest.raises(ValueError) as erro:
        Config.de_dict({"geral": {"px_por_mm": 0}, "relevo": {"timeout_ms": -1.0},
                        "dimensoes": {"comprimento_nominal_mm": 500.0}})
    for parametro in ("geral.px_por_mm", "relevo.timeout_ms", "dimensoes.comprimento_nominal_mm"):
        assert parametro in str(erro.value)


def test_tipos_convertidos_e_secoes_em_falta_por_omissao():
    c = Config.de_dict({"geral": {"px_por_mm": 8}})
    assert c.geral.px_por_mm == 8.0 and type(c.geral.px_por_mm) is float
    assert c.relevo == Config().relevo


def test_replace_de_uma_secao_revalida_tudo():
    cfg = Config()
    with pytest.raises(ValueError):
        dataclasses.replace(cfg, relevo=dataclasses.replace(cfg.relevo, timeout_ms=-1.0))


def test_validacao_cruzada_camara():
    with pytest.raises(ValueError, match="fps"):
        Config.de_dict({"disparo": {"fps": D.CAMARA_FPS_MAX + 1}})
    with pytest.raises(ValueError, match="exposicao_us"):
        Config.de_dict({"aquisicao": {"exposicao_us": 1e6 / D.DISPARO_FPS + 1000}})


def test_guardar_e_carregar_com_acentos(tmp_path):
    pasta = com_acentos(tmp_path)
    caminho = pasta / "configuração ç.json"
    caminho.write_text(json.dumps({"relevo": {"ativo": True}}), encoding="utf-8-sig")
    assert Config.carregar(caminho).relevo.ativo is True

    c = Config.de_dict({"geral": {"px_por_mm": 12.4}, "relevo": {"ativo": True}})
    caminho = pasta / "telemóvel.json"
    c.guardar(caminho)
    assert json.loads(caminho.read_text(encoding="utf-8")) == {"geral": {"px_por_mm": 12.4}, "relevo": {"ativo": True}}
    assert Config.carregar(caminho) == c
    assert c.descrever_alteracoes() == [
        f"geral.px_por_mm = 12.4 (define GERAL_PX_POR_MM = {D.GERAL_PX_POR_MM})",
        "relevo.ativo = True (define RELEVO_ATIVO = False)",
    ]
    c.guardar(pasta / "completo.json", completo=True)
    completo = json.loads((pasta / "completo.json").read_text(encoding="utf-8"))
    assert len(completo["relevo"]) == len(dataclasses.fields(Config().relevo))


@pytest.mark.parametrize("perfil", PERFIS, ids=[p.stem for p in PERFIS])
def test_perfis_de_configuracao_carregam(perfil):
    cfg = carregar_configuracao(perfil)
    assert cfg.alteracoes(), "um perfil sem alterações não serve para nada"


def test_ha_perfis():
    assert {p.stem for p in PERFIS} >= {"bancada_botao", "bancada_virtual", "linha"}


# --- Ferramentas e dataset ---------------------------------------------------------------

def test_classes_do_avaliar_sao_as_pastas_do_dataset():
    """O avaliar lê as pastas que a BIapp cria ao "Guardar no dataset" (uma por classe)."""
    assert sorted(avaliar.CLASSES) == sorted(CLASSES_DATASET)


def test_parametros_da_afinacao_estao_na_grelha():
    for secao, lista in config.AFINACAO.items():
        s_cfg = getattr(Config(), secao)
        for nome, minimo, maximo, passo in lista:
            v = getattr(s_cfg, nome)
            assert minimo <= v <= maximo, f"{secao}.{nome}"
            assert abs((v - minimo) / passo - round((v - minimo) / passo)) < 1e-9, f"{secao}.{nome}"


# --- Mestre ------------------------------------------------------------------------------

IMG = np.zeros((40, 60, 3), np.uint8)


def test_ordem_e_escolha_dos_modulos():
    cfg = Config()
    assert m.Mestre(cfg).nomes == ["localizacao", "dimensoes"], "o M5 só entra na inspeção quando se liga"
    with pytest.raises(ValueError):
        m.Mestre(cfg, apenas=["xpto"])
    com_relevo = dataclasses.replace(cfg, relevo=dataclasses.replace(cfg.relevo, ativo=True))
    assert m.Mestre(com_relevo).nomes == ["localizacao", "dimensoes", "relevo"]
    assert m.Mestre(cfg, apenas=["relevo"]).nomes == ["localizacao", "relevo"], "o Verificar rugas da BIapp"


def test_imagem_sem_saco_da_sem_saco_com_motivo():
    r = m.Mestre(config_teste()).inspecionar(IMG.copy())
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

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        return Relatorio.concluir(self.nome, [], {"comprimento_mm": saco.comprimento_mm})


class ComDefeitos(Escravo):
    nome, pergunta = "relevo", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        return Relatorio.concluir(self.nome, [Defeito(TipoDefeito.RISCO, 1, 2, 20, 0.4),
                                              Defeito(TipoDefeito.RUGAS, 30, 0, 25, 15)])


class Rebenta(Escravo):
    nome, pergunta = "cor", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        return 1 / 0


class EscreveNaImagem(Escravo):
    nome, pergunta = "manchas", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        saco.imagem[0, 0] = 255  # proibido
        return Relatorio.concluir(self.nome, [])


class MudaOSaco(Escravo):
    nome, pergunta = "manchas", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        saco.comprimento_mm = 1.0  # proibido
        return Relatorio.concluir(self.nome, [])


class MudaAConfig(Escravo):
    nome, pergunta = "manchas", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        cfg.geral.px_por_mm = 1.0  # proibido: os outros escravos usam a mesma configuração
        return Relatorio.concluir(self.nome, [])


class FalhaOContrato(Escravo):
    nome, pergunta = "manchas", "?"

    def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
        return "não sou um Relatorio"


def mestre_com(*classes, politica="rejeitar"):
    cfg = config_teste(geral={"em_caso_de_erro": politica})
    mestre = m.Mestre(cfg, apenas=[])
    mestre.escravos = [k() for k in classes]
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
                                           (MudaAConfig, "FrozenInstanceError"), (FalhaOContrato, "contrato")])
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


def test_escravos_reais_m3_decide_e_m5_desligado_por_omissao(saco_falso):
    r = m.Mestre(config_teste()).inspecionar(IMG.copy())
    estados = {x.modulo: x.estado for x in r.relatorios}
    assert estados["dimensoes"] in (Estado.OK, Estado.NOK)
    assert "relevo" not in estados and "relevo" in r.desligados
    assert r.estado is estados["dimensoes"]


def test_validar_com_medidas_reais(tmp_path):
    from ferramentas import validar
    rng = np.random.default_rng(3)
    pasta = tmp_path / "teste"
    linhas = ["ficheiro;saco;tipo_saco;fundo;comprimento_mm;largura_mm"]
    for nome, saco, angulo, (c, l) in (("a1.png", "A", 23.0, (150, 55)), ("a2.png", "A", -10.0, (150, 55)),
                                      ("b1.png", "B", 40.0, (120, 50))):
        img, _ = cena_saco(rng, comprimento=c, largura=l, angulo=angulo)
        guardar_imagem(pasta / "imagens" / nome, img)
        linhas.append(f"{nome};{saco};{'grande' if saco == 'A' else 'pequeno'};cinza;{c};{l}")
    (pasta / "ground_truth.csv").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    (tmp_path / "perfil.json").write_text(json.dumps(MONTAGEM | {"geral": {"px_por_mm": 5.0}}), encoding="utf-8")

    saida = tmp_path / "res" / "validacao.csv"
    assert validar.main(["--pasta", str(pasta), "--config", str(tmp_path / "perfil.json"), "--saida", str(saida)]) == 0
    medicoes = validar.medir(pasta, carregar_configuracao(tmp_path / "perfil.json"))
    assert all(m.estado == "OK" and abs(m.erro_comprimento_mm) < 0.5 and abs(m.erro_largura_mm) < 0.5
               for m in medicoes), medicoes
    (geral,) = validar.resumir(medicoes)
    assert geral["fotos"] == 3 and geral["taxa_erro_ou_sem_saco"] == 0 and geral["comprimento_erro_max_mm"] < 0.5
    assert len(validar.resumir(medicoes, "tipo_saco")) == 2
    (repet,) = validar.repetibilidade(medicoes)
    assert repet["saco"] == "A" and repet["comprimento_dp_mm"] < 0.3
    assert saida.exists() and saida.with_name("validacao_resumo.csv").exists()


def test_cada_define_interno_e_usado_no_codigo():
    """Os defines internos (M1_, M2_, ...) existem para tirar números fixos do código: cada um tem de ser lido."""
    codigo = "\n".join(f.read_text(encoding="utf-8") for pasta in ("modulos", "nucleo", "ferramentas")
                       for f in (RAIZ / pasta).rglob("*.py") if f.name != "defines.py")
    codigo += (RAIZ / "mestre.py").read_text(encoding="utf-8")
    internos = [n for n in dir(D) if re.match(r"M\d_", n)]
    assert internos and not [n for n in internos if n not in codigo], "defines internos que ninguém usa"


def test_escravos_declaram_a_iluminacao_e_o_saco_recorta_outras():
    from nucleo.contrato import ILUMINACOES
    assert all(e.iluminacao in ILUMINACOES for e in m.ESCRAVOS)
    rng = np.random.default_rng(1)
    img, _ = cena_saco(rng, comprimento=150, largura=55)
    saco = m.Localizador(config_teste(geral={"px_por_mm": 5.0})).localizar(img)
    assert np.array_equal(saco.recortar(img), saco.imagem), "a mesma transformação do recorte"


def test_escravos_so_tem_parametros_em_mm():
    """Depois do M2 tudo está no espaço métrico: os parâmetros dos escravos são em mm, mm² ou graus."""
    cfg = Config()
    em_px = [f"{secao}.{nome}" for secao in ("dimensoes", "relevo")
             for nome in dataclasses.asdict(getattr(cfg, secao)) if "px" in nome]
    em_px += [n for n in dir(D) if re.match(r"M[3-5]_", n) and "PX" in n]
    assert not em_px


# --- Contrato único dos escravos ----------------------------------------------------------

def test_defeito_e_relatorio_sao_imutaveis():
    d = Defeito(TipoDefeito.RISCO, 1, 2, 20, 0.4)
    r = Relatorio.concluir("relevo", [d], {"num_linhas": 1.0})
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.descricao = "outra"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.estado = Estado.OK
    with pytest.raises(TypeError):
        r.medidas["num_linhas"] = 2.0
    assert r.defeitos == (d,) and r.estado is Estado.NOK


def test_escravos_cumprem_o_protocolo_sem_rebentar():
    from nucleo.contrato import Escravo as Protocolo
    rng = np.random.default_rng(2)
    img, _ = cena_saco(rng, comprimento=150, largura=55)
    cfg = config_teste(geral={"px_por_mm": 5.0})
    saco = m.Localizador(cfg).localizar(img)
    from nucleo.contrato import Captura
    for classe in m.ESCRAVOS:
        escravo = classe()
        assert isinstance(escravo, Protocolo), classe.__name__
        r = escravo.inspecionar(saco, Captura(img, "x", 0.0), cfg)  # não levanta exceções
        assert isinstance(r, Relatorio) and r.modulo == classe.nome
        if classe.nome in ("cor", "manchas"):
            assert r.estado is Estado.POR_IMPLEMENTAR and r.mensagem.startswith("por implementar")
        if classe.nome == "relevo":
            assert r.estado is Estado.OK, f"um saco liso não tem rugas: {r.resumo} {r.mensagem}"


def test_escravos_recebem_a_captura_no_espaco_metrico(saco_falso):
    vistas = []

    class Espia(Escravo):
        nome, pergunta = "manchas", "?"

        def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
            vistas.append((captura.px_por_mm, captura.imagem.shape, captura.imagem_para("rasante") is captura.imagem))
            return Relatorio.concluir(self.nome, [])

    mestre = mestre_com(Espia)
    mestre.inspecionar(np.zeros((80, 120, 3), np.uint8), px_por_mm=10.0)  # câmara a 10 px/mm, perfil a 5
    assert vistas == [(5.0, (40, 60, 3), True)]


def test_contornos_dos_defeitos_em_mm_ida_e_volta():
    rng = np.random.default_rng(4)
    img, _ = cena_saco(rng, comprimento=150, largura=55, angulo=31.0)
    saco = m.Localizador(config_teste(geral={"px_por_mm": 5.0})).localizar(img)
    na_imagem = saco.contorno.reshape(-1, 2).astype(float)
    no_recorte = cv2.transform(na_imagem.reshape(-1, 1, 2), saco.matriz).reshape(-1, 2)
    em_mm = saco.px_para_mm(no_recorte, 5.0)
    assert np.abs(em_mm[:, 0]).max() < 150 / 2 + 1 and np.abs(em_mm[:, 1]).max() < 55 / 2 + 1
    assert np.allclose(saco.mm_para_imagem(em_mm, 5.0), na_imagem, atol=1e-6)


# --- Tempo real: tempo limite e paralelo -----------------------------------------------------

def _lento(nome_modulo, segundos):
    class Lento(Escravo):
        nome, pergunta = nome_modulo, "?"

        def inspecionar(self, saco, captura, cfg, guardar_intermedios=False):
            time.sleep(segundos)
            return Relatorio.concluir(self.nome, [])
    return Lento


@pytest.mark.parametrize("paralelo", [False, True])
def test_escravo_que_passa_o_tempo_limite_da_erro(saco_falso, paralelo):
    cfg = config_teste(geral={"paralelo": paralelo}, relevo={"timeout_ms": 50.0})
    mestre = m.Mestre(cfg, apenas=[])
    mestre.escravos = [Bom(), _lento("relevo", 0.4)()]
    inicio = time.perf_counter()
    r = mestre.inspecionar(IMG.copy())
    assert time.perf_counter() - inicio < 0.3, "o Mestre não espera pelo módulo atrasado"
    assert r.relatorio("relevo").estado is Estado.ERRO and "tempo limite de 50 ms" in r.relatorio("relevo").mensagem
    assert r.relatorio("dimensoes").estado is Estado.OK and r.estado is Estado.ERRO


def test_escravos_em_paralelo_poupam_tempo_e_mantem_a_ordem(saco_falso):
    mestre = m.Mestre(config_teste(geral={"paralelo": True}), apenas=[])
    mestre.escravos = [_lento("relevo", 0.25)(), _lento("manchas", 0.25)(), Bom()]
    inicio = time.perf_counter()
    r = mestre.inspecionar(IMG.copy())
    assert time.perf_counter() - inicio < 0.45, "os dois lentos correram ao mesmo tempo"
    assert [x.modulo for x in r.relatorios] == ["localizacao", "relevo", "manchas", "dimensoes"]
    assert r.estado is Estado.OK and r.relatorio("relevo").tempo_ms >= 240


def test_paralelo_da_o_mesmo_que_em_sequencia():
    img = cena_saco(np.random.default_rng(7), comprimento=158, largura=55)[0]
    sequencial = m.Mestre(config_teste(geral={"px_por_mm": 5.0})).inspecionar(img.copy())
    paralelo = m.Mestre(config_teste(geral={"px_por_mm": 5.0, "paralelo": True})).inspecionar(img.copy())
    assert sequencial.estado is paralelo.estado is Estado.NOK
    assert [d.descricao for d in sequencial.defeitos] == [d.descricao for d in paralelo.defeitos]
    assert dict(sequencial.relatorio("dimensoes").medidas) == dict(paralelo.relatorio("dimensoes").medidas)


def test_timeout_negativo_e_recusado():
    with pytest.raises(ValueError, match="relevo.timeout_ms"):
        config_teste(relevo={"timeout_ms": -1.0})


def test_desempenho_a_resolucao_da_camara_cabe_no_orcamento():
    """Um saco a 2448×2048 (a Blackfly), a 8 px/mm: M2 + M3 (M4–M6 por implementar) dentro dos 500 ms."""
    cfg = config_teste(geral={"px_por_mm": 8.0})
    img, _ = cena_saco(np.random.default_rng(1), comprimento=150, largura=55, ppm=8.0, tamanho=(2048, 2448))
    mestre = m.Mestre(cfg)
    tempos = []
    for _ in range(3):
        inicio = time.perf_counter()
        r = mestre.inspecionar(img.copy())
        tempos.append((time.perf_counter() - inicio) * 1000)
    assert r.estado is Estado.OK and abs(r.saco.comprimento_mm - 150) < 0.3
    assert sorted(tempos)[1] < cfg.geral.orcamento_ms, f"mediana {sorted(tempos)[1]:.0f} ms"


def test_ruga_e_a_causa_do_desvio_de_dimensao():
    """Com relevo e dimensão no mesmo saco, a dimensão fica como consequência: está na lista, mas o
    tipo do saco é o da ruga. Sem relevo, nada muda."""
    from nucleo.contrato import Defeito, Relatorio, Resultado, TipoDefeito
    dim = Relatorio("dimensoes", Estado.NOK, (Defeito(TipoDefeito.DIMENSAO, descricao="ponta torta (pode ser ruga/relevo)",
                                                      pode_ser_relevo=True),))
    rel = Relatorio("relevo", Estado.NOK, (Defeito(TipoDefeito.RUGAS),))
    so_dim = m.ligar_causas([dim])
    assert so_dim[0] is dim
    ligados = m.ligar_causas([dim, rel])
    d = ligados[0].defeitos[0]
    assert d.causa == "rugas" and d.descricao == "ponta torta (consequência de rugas)"
    r = Resultado(IMG, Estado.NOK, None, ligados, 1.0)
    assert r.contagem_tipos == {"rugas": 1} and len(r.defeitos) == 2
