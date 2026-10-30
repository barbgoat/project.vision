"""Regras da arquitetura Mestre–Escravos, configuração e isolamento de falhas no Mestre."""

from __future__ import annotations

import ast
import dataclasses
import json
import py_compile
import re

import numpy as np
import pytest

import mestre as m
from ferramentas import afinar, avaliar
from nucleo import config
from nucleo import defines as D
from nucleo.config import RAIZ, Config, carregar_configuracao, nome_define
from nucleo.contrato import Defeito, Escravo, Estado, Relatorio, Saco, TipoDefeito
from nucleo.imagens import guardar_imagem
from testes.apoio import MONTAGEM, alfa_retangulo, cena_saco, com_acentos, config_teste

PERFIS = sorted((RAIZ / "configuracoes").glob("*.json"))
MONTAGENS = sorted(p for p in (RAIZ / "dataset").iterdir()  # sem as pastas das ferramentas
                   if p.is_dir() and p.name not in ("segmentacao", "teste", "revisao"))


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


class MudaAConfig(Escravo):
    nome, pergunta = "manchas", "?"

    def analisar(self, saco, guardar_intermedios=False):
        self.cfg.geral.px_por_mm = 1.0  # proibido: os outros escravos usam a mesma configuração
        return Relatorio.concluir(self.nome, [])


class FalhaOContrato(Escravo):
    nome, pergunta = "manchas", "?"

    def analisar(self, saco, guardar_intermedios=False):
        return "não sou um Relatorio"


def mestre_com(*classes, politica="rejeitar"):
    cfg = config_teste(geral={"em_caso_de_erro": politica})
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


def test_escravos_reais_m3_decide_enquanto_m4_m6_por_implementar(saco_falso):
    r = m.Mestre(config_teste()).inspecionar(IMG.copy())
    estados = {x.modulo: x.estado for x in r.relatorios}
    assert estados["dimensoes"] in (Estado.OK, Estado.NOK)
    assert all(estados[k] is Estado.POR_IMPLEMENTAR for k in ("cor", "relevo", "manchas"))
    assert r.estado is estados["dimensoes"]
    assert "m5_relevo/preprocessamento.py" in r.relatorio("relevo").mensagem


def test_anotar_sem_janela(tmp_path):
    """A proposta do anotador apanha o saco num fundo com textura e guarda máscara e polígono."""
    from ferramentas import anotar
    rng = np.random.default_rng(2)
    img, (cx, cy) = cena_saco(rng, comprimento=150, largura=55, textura=True)
    verdade = alfa_retangulo(*img.shape[:2], cx, cy, 150 * 5.0, 55 * 5.0, 23.0) > 0.5
    saco = anotar.mascara(anotar.proposta(img))
    assert (saco & verdade).sum() / (saco | verdade).sum() > 0.95
    assert anotar.mascara(anotar.com_retangulo(img, 0, 0, 10, 10)).sum() < verdade.sum() / 10

    foto = tmp_path / "fotos" / "sub" / "saco.png"
    guardar_imagem(foto, img)
    anotar.guardar(foto, "sub__saco", tmp_path / "seg", saco)
    linha = (tmp_path / "seg" / "etiquetas" / "sub__saco.txt").read_text(encoding="utf-8").split()
    assert linha[0] == "0" and len(linha) % 2 == 1 and all(0 <= float(v) <= 1 for v in linha[1:])
    assert (tmp_path / "seg" / "imagens" / "sub__saco.png").exists()
    assert anotar.fotos(tmp_path / "fotos", tmp_path / "seg") == [], "as já anotadas são saltadas"


def test_anotar_aprende_com_as_fotos_guardadas(tmp_path):
    """O modelo treinado numa foto anotada dá a proposta noutra; fica guardado e é recarregado."""
    from ferramentas import anotar
    rng = np.random.default_rng(4)
    fotos = [cena_saco(rng, comprimento=150, largura=55, textura=True, angulo=a) for a in (23.0, -40.0)]
    verdades = [alfa_retangulo(*img.shape[:2], cx, cy, 750, 275, a) > 0.5
                for (img, (cx, cy)), a in zip(fotos, (23.0, -40.0))]
    modelo = anotar.Modelo()
    assert not modelo.treinado
    modelo.aprender(fotos[0][0], verdades[0])
    modelo.guardar(tmp_path)
    modelo = anotar.Modelo.carregar(tmp_path)
    assert modelo.treinado and modelo.fotos == 1
    assert modelo.probabilidade(fotos[1][0])[verdades[1]].mean() > 0.9
    saco = anotar.mascara(anotar.proposta(fotos[1][0], modelo))
    assert (saco & verdades[1]).sum() / (saco | verdades[1]).sum() > 0.95


def test_anotar_pontos_e_zoom():
    """Pontos clicados (com zoom) fecham um polígono que é a máscara, à resolução da foto."""
    import cv2
    from ferramentas import anotar
    rng = np.random.default_rng(6)
    img, (cx, cy) = cena_saco(rng, comprimento=150, largura=55, textura=True, tamanho=(2000, 2500), ppm=10.0)
    an = anotar.Anotacao(img)
    an.ampliar(400, 300, 2.0)
    assert an.zoom == 2.0 and np.allclose(an._para_ecra(an._para_original(400, 300)), (400, 300))
    an.modo = 3
    cantos = cv2.boxPoints(((cx, cy), (1500, 550), 23.0))
    an.zoom, an.origem = 1.0, np.zeros(2)
    for p in cantos:
        an.rato(cv2.EVENT_LBUTTONDOWN, *an._para_ecra(p), 0)
    an.rato(cv2.EVENT_LBUTTONDOWN, *an._para_ecra(cantos[0]), 0)  # clique no 1.º ponto: fecha
    assert an.poligono is not None and not an.pontos
    saco, verdade = an.mascara_final(), alfa_retangulo(*img.shape[:2], cx, cy, 1500, 550, 23.0) > 0.5
    assert saco.shape == img.shape[:2] and (saco & verdade).sum() / (saco | verdade).sum() > 0.99
    assert anotar._roda(120 << 16) > 0 > anotar._roda((-(120 << 16)) & 0xFFFFFFFF)


def test_anotar_metadados_coco_e_edicao(tmp_path):
    """Cada foto guardada fica nos metadados e no COCO; o contorno vira pontos que se arrastam."""
    import cv2
    from ferramentas import anotar
    rng = np.random.default_rng(8)
    img, (cx, cy) = cena_saco(rng, comprimento=150, largura=55, textura=True)
    verdade = alfa_retangulo(*img.shape[:2], cx, cy, 750, 275, 23.0) > 0.5
    foto = tmp_path / "fotos" / "saco_A" / "f1.png"
    guardar_imagem(foto, img)
    saida = tmp_path / "seg"
    anotar.guardar(foto, "saco_A__f1", saida, verdade, {"saco": "saco_A", "tipo_saco": "PreVital", "fundo": "cinza"})
    anotar.guardar(foto, "saco_A__f1", saida, verdade, {"saco": "saco_A", "tipo_saco": "PreVital", "fundo": "cinza"})
    assert anotar.ler_metadados(saida) == {"saco_A__f1": {"saco": "saco_A", "tipo_saco": "PreVital", "fundo": "cinza"}}
    coco = json.loads((saida / "anotacoes_coco.json").read_text(encoding="utf-8"))
    assert len(coco["images"]) == 1 and len(coco["annotations"]) == 1, "guardar outra vez substitui"
    (anotacao,) = coco["annotations"]
    assert abs(anotacao["area"] - verdade.sum()) < 1 and len(anotacao["segmentation"][0]) >= 8
    assert coco["images"][0]["fundo"] == "cinza" and coco["images"][0]["file_name"] == "imagens/saco_A__f1.png"
    assert anotar.exportar_coco(saida)["annotations"][0]["bbox"] == anotacao["bbox"]

    an = anotar.Anotacao(img)
    an.usar_mascara(verdade)
    assert an.mascara_final() is verdade, "a máscara do SAM fica à resolução total"
    an.editar()
    assert an.modo == 3 and an.poligono is not None and an.exata is None
    vertice = an.poligono[0].copy()
    an.rato(cv2.EVENT_LBUTTONDOWN, *an._para_ecra(vertice), 0)
    an.rato(cv2.EVENT_MOUSEMOVE, *an._para_ecra(vertice + (40, 0)), 0)
    an.rato(cv2.EVENT_LBUTTONUP, *an._para_ecra(vertice + (40, 0)), 0)
    assert np.allclose(an.poligono[0], vertice + (40, 0), atol=1.5), "o vértice foi arrastado"


def test_dividir_sem_repetir_sacos():
    from ferramentas.dividir_dataset import dividir
    fotos = {f"{s}_{k}": {"saco": s, "tipo_saco": "B" if s == "s9" else "A", "fundo": "inox" if k == 0 else "preto"}
             for s in (f"s{i}" for i in range(10)) for k in range(3)}
    divisao = dividir(fotos, semente=1)
    for saco in {m["saco"] for m in fotos.values()}:
        assert len({divisao[n] for n, m in fotos.items() if m["saco"] == saco}) == 1, saco
    assert {c: list(divisao.values()).count(c) for c in ("treino", "validacao", "teste")} == \
        {"treino": 21, "validacao": 6, "teste": 3}
    fora = dividir(fotos, fora_fundo="inox")
    assert {n for n, c in fora.items() if c == "teste"} == {n for n, m in fotos.items() if m["fundo"] == "inox"}
    assert set(fora.values()) == {"teste", "fora"}, "todos os sacos têm uma foto no inox: o resto fica de fora"
    por_tipo = dividir(fotos, fora_tipo="B")
    assert {n for n, c in por_tipo.items() if c == "teste"} == {"s9_0", "s9_1", "s9_2"}
    assert "fora" not in por_tipo.values()


def test_validar_com_medidas_reais(tmp_path):
    from ferramentas import validar
    rng = np.random.default_rng(3)
    pasta = tmp_path / "teste"
    linhas = ["ficheiro;saco;tipo_saco;fundo;comprimento_mm;largura_mm"]
    for nome, saco, angulo, (c, l) in (("a1.png", "A", 23.0, (150, 55)), ("a2.png", "A", -10.0, (150, 55)),
                                      ("b1.png", "B", 40.0, (120, 50))):
        img, (cx, cy) = cena_saco(rng, comprimento=c, largura=l, angulo=angulo)
        guardar_imagem(pasta / "imagens" / nome, img)
        linhas.append(f"{nome};{saco};{'grande' if saco == 'A' else 'pequeno'};cinza;{c};{l}")
        if nome == "a1.png":
            mascara = alfa_retangulo(*img.shape[:2], cx, cy, c * 5.0, l * 5.0, angulo) > 0.5
            guardar_imagem(pasta / "mascaras" / "a1.png", mascara.astype(np.uint8) * 255)
    (pasta / "ground_truth.csv").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    (tmp_path / "perfil.json").write_text(json.dumps(MONTAGEM | {"geral": {"px_por_mm": 5.0}}), encoding="utf-8")

    saida = tmp_path / "res" / "validacao.csv"
    assert validar.main(["--pasta", str(pasta), "--config", str(tmp_path / "perfil.json"), "--saida", str(saida)]) == 0
    medicoes = validar.medir(pasta, carregar_configuracao(tmp_path / "perfil.json"), "delta_e")
    assert all(m.estado == "OK" and abs(m.erro_comprimento_mm) < 0.5 and abs(m.erro_largura_mm) < 0.5
               for m in medicoes), medicoes
    assert medicoes[0].iou > 0.97 and medicoes[1].iou is None
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
