"""Interface gráfica: correções do veredito, editor de parâmetros e a janela a correr, sem ecrã."""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import threading
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # sem janelas no ecrã; tem de vir antes da QApplication

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import biapp_dashboard  # noqa: E402
import gui  # noqa: E402
from interface.avisos import AMARELO, VERDE, VERMELHO, AlarmeNOK, explicar, motivo, taxa_nok  # noqa: E402
from interface.editor import SECOES_CAMARA, SECOES_DEFINICOES, EditorParametros, ajudas  # noqa: E402
from interface.estilo import ICONE, LOGO, icone  # noqa: E402
from interface.janela import Janela  # noqa: E402
from interface.janelas import classe_proposta  # noqa: E402
from interface.login import JanelaLogin, assinatura, pode, verificar  # noqa: E402
from interface.ordens import DialogoEspecificacao, DialogoOrdem  # noqa: E402
from interface.paineis import UltimosSacos, VistaImagem  # noqa: E402
from interface import vista_dashboard  # noqa: E402
from interface.registos import acrescentar_zona, guardar_correcao, pasta_dataset, zonas  # noqa: E402
from nucleo.config import RAIZ, Config, nome_define  # noqa: E402
from nucleo.contrato import Captura, Defeito, Estado, Relatorio, Resultado, TipoDefeito  # noqa: E402
from nucleo.especificacoes import (  # noqa: E402
    MEDIDAS, Especificacao, OrdemFabrico, Repositorio, resolver,
)
from nucleo.imagens import guardar_imagem  # noqa: E402
from nucleo.servidor_dashboard import Servidor  # noqa: E402
from interface import ciclo  # noqa: E402
from testes.apoio import (  # noqa: E402
    COMPRIMENTO, CORES, FORMAS, LARGURA, CamaraSimulada, cena_forma, cena_saco, com_camara, config_teste, correr,
    execucao, ler_csv, montagem,
)
from testes.test_especificacoes import _produto  # noqa: E402

IMG = np.zeros((20, 30, 3), np.uint8)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def esperar(app, condicao, segundos: float = 60) -> None:
    """Deixa a janela trabalhar até a condição se cumprir."""
    fim = time.monotonic() + segundos
    while not condicao():
        assert time.monotonic() < fim, "a janela não chegou lá a tempo"
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def janela(app, tmp_path, monkeypatch):
    """A janela com a câmara simulada e o perfil dos testes. Em Trigger chegam três sacos (canto_dobrado e
    comprido_158 NOK, certo OK); Ao vivo, a câmara fica à espera de um saco que não vem."""
    rng = np.random.default_rng(5)
    sacos = [Captura(cena_forma(rng, FORMAS[nome], angulo=-15, cor=CORES["azul"]), f"{nome}.png", 0.0)
             for nome in ("canto_dobrado", "certo", "comprido_158")]
    com_camara(monkeypatch, ciclo, lambda cfg: CamaraSimulada(sacos, trigger=True) if cfg.disparo.modo == "trigger"
               else CamaraSimulada([], sem_fim=True))
    (tmp_path / "perfil.json").write_text(json.dumps(montagem(disparo={"anti_ressalto_ms": 0.0})), encoding="utf-8")
    args = gui.construir_parser().parse_args([
        "--config", str(tmp_path / "perfil.json"), "--modo", "trigger",
        "--saida", str(tmp_path / "res"), "--dataset", str(tmp_path / "dataset" / "linha"),
    ])
    j = Janela(args)
    j._ensaio_sem_of = True  # ensaios sem OF, salvo se o teste escolher uma
    yield j
    j.close()  # para o ciclo e espera por ele


def _campo(editor: EditorParametros, secao: str, nome: str):
    return next(c.widget for c in editor._campos if (c.secao, c.nome) == (secao, nome))


def _producao_ate_ao_fim(app, j: Janela, decisoes: tuple[str, ...] = ("NOK", "NOK"), arrancar: bool = True) -> None:
    """Corre os três sacos até ao fim, decidindo cada saco em revisão pela ordem de `decisoes`."""
    if arrancar:
        j.iniciar()
    pendentes = list(decisoes)
    while j.ciclo is not None:
        esperar(app, lambda: j.ciclo is None or j._em_revisao is not None)
        if j._em_revisao is not None:
            assert j._em_revisao.visto.anotada is not None, "a revisão mostra o saco"
            j._em_revisao._decidir(pendentes.pop(0))
    assert not pendentes, "houve menos revisões do que o esperado"
    esperar(app, lambda: j._base is not None and j._base.numero == 3)  # o último saco no ecrã


# --- Sem janela ------------------------------------------------------------------------------

def test_ajuda():
    p = correr("gui.py", "--help")
    assert p.returncode == 0 and "--modo" in p.stdout


def test_interface_nao_depende_das_ferramentas():
    """A interface e as ferramentas leem do nucleo/; nunca uma da outra."""
    for ficheiro in (RAIZ / "interface").glob("*.py"):
        for no in ast.walk(ast.parse(ficheiro.read_text(encoding="utf-8"))):
            modulos = [no.module or ""] if isinstance(no, ast.ImportFrom) else \
                [a.name for a in no.names] if isinstance(no, ast.Import) else []
            assert not any(m.startswith("ferramentas") for m in modulos), f"{ficheiro.name} importa {modulos}"


def test_correcao_copia_a_imagem_para_o_dataset_e_regista(tmp_path):
    r = Resultado(IMG, Estado.NOK, None, [], 1.0, captura=Captura(IMG, "linha.mp4#7", 1000.0))
    dataset, registo = tmp_path / "dataset" / "blackfly", tmp_path / "res" / "correcoes.csv"
    feito, copia = guardar_correcao(r, "ok", dataset, registo)
    copias = list((dataset / "ok").glob("*.png"))
    assert copias == [copia] and "linha" in copia.name and "blackfly/ok/" in feito
    guardar_correcao(r, "risco", dataset, registo)
    linhas = ler_csv(registo)
    assert [linha["classe_real"] for linha in linhas] == ["ok", "risco"]
    assert linhas[0]["estado_sistema"] == "NOK" and linhas[0]["origem"] == "linha.mp4#7"
    assert "﻿" not in registo.read_text(encoding="utf-8-sig"), "o BOM só no início do ficheiro"


def test_correcao_de_uma_imagem_do_dataset_so_regista(tmp_path):
    dataset = tmp_path / "dataset" / "linha"
    original = dataset / "ok" / "saco 1 ç.png"
    guardar_imagem(original, IMG)
    r = Resultado(IMG, Estado.NOK, None, [], 1.0, captura=Captura(IMG, str(original), 1000.0))
    feito, copia = guardar_correcao(r, "risco", dataset, tmp_path / "correcoes.csv")
    assert "já está no dataset" in feito and copia is None and list(dataset.rglob("*.png")) == [original]
    assert ler_csv(tmp_path / "correcoes.csv")[0]["imagem_dataset"] == ""


def test_corrigir_outra_vez_move_a_copia_em_vez_de_duplicar(tmp_path):
    r = Resultado(IMG, Estado.NOK, None, [], 1.0, captura=Captura(IMG, "linha.mp4#7", 1000.0))
    dataset, registo = tmp_path / "dataset" / "linha", tmp_path / "correcoes.csv"
    _, copia = guardar_correcao(r, "risco", dataset, registo)
    feito, mesma = guardar_correcao(r, "risco", dataset, registo, copia)
    assert mesma == copia and "ficou só registada" in feito
    feito, movida = guardar_correcao(r, "dobra", dataset, registo, copia)
    assert movida == dataset / "dobra" / copia.name and "movida" in feito
    assert list(dataset.rglob("*.png")) == [movida], "uma só cópia, na pasta da última classe"
    assert [linha["classe_real"] for linha in ler_csv(registo)] == ["risco", "risco", "dobra"]


def test_classe_proposta_pela_revisao():
    def com_tipos(*tipos: TipoDefeito) -> Resultado:
        estado = Estado.NOK if tipos else Estado.ERRO
        return Resultado(IMG, estado, None, [Relatorio("m", estado, tuple(Defeito(t) for t in tipos))], 1.0)
    assert classe_proposta(com_tipos(TipoDefeito.RUGAS, TipoDefeito.RUGAS)) == "rugas"
    assert classe_proposta(com_tipos(TipoDefeito.RUGAS, TipoDefeito.MANCHA)) == "varios"
    assert classe_proposta(com_tipos()) == "", "sem defeitos (ERRO, SEM_SACO) não se inventa uma classe"

    def dimensao(*forma: bool) -> Resultado:
        defeitos = tuple(Defeito(TipoDefeito.DIMENSAO, pode_ser_relevo=f) for f in forma)
        return Resultado(IMG, Estado.NOK, None, [Relatorio("dimensoes", Estado.NOK, defeitos)], 1.0)
    assert classe_proposta(dimensao(False)) == "dimensao", "só o tamanho: é uma medida"
    assert classe_proposta(dimensao(False, True)) == "rugas", "a forma torta propõe rugas (quem revê confirma)"


def test_zonas_do_defeito_em_labelme_e_mudam_de_pasta_com_a_imagem(tmp_path):
    r = Resultado(IMG, Estado.OK, None, [], 1.0, captura=Captura(IMG, "cam#1", 1000.0))
    dataset, registo = tmp_path / "dataset" / "linha", tmp_path / "correcoes.csv"
    _, copia = guardar_correcao(r, "rugas", dataset, registo)
    assert zonas(copia) == []
    assert acrescentar_zona(copia, "rugas", (10, 5, 30, 20), 80, 60) == 1
    assert acrescentar_zona(copia, "rugas", (40, 30, 8, 9), 80, 60) == 2
    dados = json.loads(copia.with_suffix(".json").read_text(encoding="utf-8"))
    assert dados["imagePath"] == copia.name and dados["imageWidth"] == 80 and dados["imageHeight"] == 60
    assert dados["shapes"][0] == {"label": "rugas", "points": [[10, 5], [40, 25]], "group_id": None,
                                  "shape_type": "rectangle", "flags": {}}
    _, movida = guardar_correcao(r, "dobra", dataset, registo, copia)
    assert zonas(movida) == [(10, 5, 30, 20), (40, 30, 8, 9)] and not copia.with_suffix(".json").exists()
    assert {f["label"] for f in json.loads(movida.with_suffix(".json").read_text(encoding="utf-8"))["shapes"]}         == {"dobra"}, "as zonas levam o rótulo novo"


def test_passagem_descartada_nao_se_corrige(tmp_path):
    r = Resultado(None, Estado.ERRO, None, [], 1.0)
    with pytest.raises(ValueError, match="descartada"):
        guardar_correcao(r, "ok", tmp_path / "dataset", tmp_path / "correcoes.csv")


def test_dataset_das_correcoes_segue_o_perfil():
    assert pasta_dataset(RAIZ / "configuracoes" / "linha.json") == RAIZ / "dataset" / "linha"


def test_cada_parametro_do_editor_tem_o_seu_define():
    textos = ajudas()
    for secao in SECOES_DEFINICOES + SECOES_CAMARA:
        for nome in dataclasses.asdict(getattr(Config(), secao)):
            assert nome_define(secao, nome) in textos
    assert "Limites LARGOS" in textos["LOCALIZACAO_LARGURA_MAX_MM"], "um comentário vale para o grupo por baixo"


# --- Login ---------------------------------------------------------------------------------

def test_login_so_com_a_palavra_passe_certa(app):
    assert verificar("admin", "admin") and verificar(" admin ", "admin")
    assert not verificar("admin", "Admin") and not verificar("admin", "") and not verificar("outro", "admin")
    login = JanelaLogin()
    login.ed_utilizador.setText("admin")
    login.ed_palavra_passe.setText("errada")
    login._entrar()
    assert login.result() != QDialog.DialogCode.Accepted and "errados" in login.erro.text()
    assert login.ed_palavra_passe.text() == "", "a palavra-passe errada é apagada"
    login.ed_palavra_passe.setText("admin")
    login._entrar()
    assert login.result() == QDialog.DialogCode.Accepted and login.utilizador == "admin"


def test_logotipo_e_icones_do_biapp(app):
    for caminho in (LOGO, ICONE):
        imagem = QPixmap(str(caminho))
        assert not imagem.isNull() and imagem.hasAlphaChannel(), f"{caminho.name}: com fundo transparente"
    for nome in ("definicoes", "conta", "camara"):
        imagem = icone(nome).pixmap(32, 32).toImage()
        assert imagem.pixelColor(16, 16).alpha() > 0 or imagem.pixelColor(16, 6).alpha() > 0, f"{nome} desenhado"


def test_zoom_da_imagem(app):
    vista = VistaImagem()
    vista.resize(400, 300)
    imagem = np.zeros((300, 400, 3), np.uint8)
    imagem[100:200, 150:250] = 255
    vista.mostrar(imagem)
    roda = QWheelEvent(QPointF(200, 150), QPointF(200, 150), QPoint(), QPoint(0, 240), Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)  # dois passos
    vista.wheelEvent(roda)
    assert vista._zoom == pytest.approx(1.25 ** 2)
    assert vista.grab().toImage().pixelColor(200, 150).red() == 255, "amplia à volta do cursor"
    vista.mostrar(imagem.copy())
    assert vista._zoom > 1, "outra imagem do mesmo tamanho fica com o mesmo zoom"
    vista.mouseDoubleClickEvent(None)
    assert vista._zoom == 1
    vista.wheelEvent(roda)
    vista.mostrar(np.zeros((10, 10, 3), np.uint8))
    assert vista._zoom == 1, "uma imagem de outro tamanho volta a ficar ajustada"


def test_desenhar_a_roi_na_imagem(app):
    vista = VistaImagem()
    vista.resize(400, 300)
    vista.mostrar(np.zeros((150, 200, 3), np.uint8))  # ajustada: 2 px do ecrã por px da imagem
    desenhadas = []
    vista.roi_desenhada.connect(lambda *roi: desenhadas.append(roi))
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    assert vista._zoom == 1 and not desenhadas, "sem desenhar_roi, carregar não desenha nada"
    vista.desenhar_roi = True
    QTest.mousePress(vista, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    QTest.mouseMove(vista, QPoint(500, 120))  # para lá da imagem: fica na borda dela
    QTest.mouseRelease(vista, Qt.MouseButton.LeftButton, pos=QPoint(500, 120))
    assert desenhadas == [(20, 10, 180, 50)]
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
    assert len(desenhadas) == 1, "um clique sem arrastar não é uma ROI"


def test_operador_so_ve_a_vista_operador(app, janela):
    janela._definir_utilizador("op")
    so_admin = (janela.acao_engenharia, janela.acao_historico, janela.acao_camara, janela.acao_definicoes,
                janela.acao_contas)  # entradas do menu ⚙
    assert not any(a.isVisible() for a in so_admin) and janela.btn_ao_vivo.isHidden() and not janela._engenharia()
    assert janela._modo() == "trigger", "o operador usa o modo do perfil"
    assert not janela.btn_iniciar.isHidden() and not janela.btn_menu.isHidden()
    janela._definir_utilizador("admin")
    assert all(a.isVisible() for a in so_admin) and not janela.btn_ao_vivo.isHidden()


def test_tamanhos_das_divisorias_ficam_lembrados(app, janela, tmp_path):
    memoria = QSettings(str(tmp_path / "biapp.ini"), QSettings.Format.IniFormat)
    primeira = Janela(janela.args, memoria=memoria)
    primeira._divisorias["divisoria_tabelas"].setSizes([300, 100])
    estado = primeira._divisorias["divisoria_tabelas"].saveState()
    primeira.close()
    segunda = Janela(janela.args, memoria=memoria)
    assert segunda._divisorias["divisoria_tabelas"].saveState() == estado
    segunda.close()


def test_definicoes_e_camara_mostram_a_mesma_configuracao(app, janela):
    _campo(janela.editor_camara, "aquisicao", "exposicao_us").setValue(200.0)
    _campo(janela.editor, "dimensoes", "tolerancia_comprimento_mm").setValue(4.0)
    cfg = janela._cfg_edicao
    assert cfg.aquisicao.exposicao_us == 200.0 and cfg.dimensoes.tolerancia_comprimento_mm == 4.0
    assert janela.editor.cfg() is cfg and janela.editor_camara.cfg() is cfg


# --- Editor de parâmetros ------------------------------------------------------------------

def test_editor_mostra_so_o_essencial_com_nomes_claros(app):
    editor = EditorParametros(SECOES_CAMARA)
    exposicao, lente = _campo(editor, "aquisicao", "exposicao_us"), _campo(editor, "lente", "focal_mm")
    assert not exposicao.isHidden() and lente.isHidden(), "a lente é avançada"
    rotulo = next(c.rotulo for c in editor._campos if c.nome == "exposicao_us")
    assert rotulo.text() == "Exposição (µs)" and "AQUISICAO_EXPOSICAO_US" in rotulo.toolTip()
    lista = editor.secao.view()
    assert lista.isRowHidden(editor.secao.findData("lente")), "uma secção sem nada essencial fica de fora"
    editor.todos.setChecked(True)
    assert not lente.isHidden() and not lista.isRowHidden(editor.secao.findData("lente"))


def test_editor_so_emite_configuracoes_validas(app):
    editor = EditorParametros()
    cfg = config_teste()
    editor.definir(cfg, perfil=cfg)
    recebidas: list[Config] = []
    editor.mudou.connect(recebidas.append)

    _campo(editor, "dimensoes", "tolerancia_comprimento_mm").setValue(4.5)
    assert recebidas[-1].dimensoes.tolerancia_comprimento_mm == 4.5 and not editor.erro.text()

    folga, profundidade = (_campo(editor, "dimensoes", "forma_folga_mm"),
                           _campo(editor, "dimensoes", "forma_profundidade_min_mm"))
    folga.setValue(profundidade.value() + 0.5)  # inválido: a folga não pode chegar à profundidade
    assert len(recebidas) == 1 and "forma_folga_mm" in editor.erro.text()
    profundidade.setValue(folga.value() + 1.0)  # corrigido: as duas mudanças entram juntas
    assert len(recebidas) == 2 and not editor.erro.text()
    assert recebidas[-1].dimensoes.forma_folga_mm == pytest.approx(folga.value())
    assert recebidas[-1].dimensoes.tolerancia_comprimento_mm == 4.5


# --- A janela a correr ---------------------------------------------------------------------

def test_producao_para_em_cada_saco_nao_ok_ate_decidir(app, janela, tmp_path):
    # canto_dobrado (NOK) → descartado; certo (OK) conta já; comprido_158 (NOK) → NOK
    _producao_ate_ao_fim(app, janela, ("descartado", "NOK"))
    assert janela.contadores_op.texto().startswith("2 sacos   OK 1   NOK 1   DESCARTADOS 1")
    assert janela.janela_historico.contadores.texto() == janela.contadores_op.texto()
    assert [janela.historico.item(i, 6).text() for i in range(3)] == ["NOK", "", "descartado"]
    pasta = execucao(tmp_path / "res")
    assert len(ler_csv(pasta / "registo.csv")) == 3, "regista como o main.py"
    assert [linha["decisao"] for linha in ler_csv(pasta / "revisoes.csv")] == ["descartado", "NOK"]
    assert janela.faixa_eng.text().startswith("NOK") and janela.defeitos.rowCount() >= 1
    assert janela.modulos.rowCount() == 2, "o M2 e o M3 (o M5 vem desligado)"
    nomes = [janela.etapas.item(i).text() for i in range(janela.etapas.count())]
    assert nomes == ["localizacao_segmentacao", "dimensoes_nominal"], "só as principais têm miniatura"
    menu = janela.btn_mais_etapas.menu()
    assert janela.btn_mais_etapas.isEnabled() and "original" in [a.text() for a in menu.actions()]
    next(a for a in menu.actions() if a.text() == "dimensoes_forma").trigger()
    assert "dimensoes_forma" in janela.titulo_imagem.text(), "+Etapas mostra-a em grande"

    janela._corrigir("dimensao")  # Engenharia: guardar no dataset
    assert len(list((tmp_path / "dataset" / "linha" / "dimensao").glob("*comprido_158.png"))) == 1


def test_revisao_manda_a_classe_real_para_o_dataset(app, janela, tmp_path):
    """Era OK → dataset/ok/; NOK → a pasta do defeito proposto. Na base de dados, o falso NOK conta
    como descartado (os números da produção não mudam); a correção na Engenharia move, não duplica."""
    repo = janela._repositorio()
    medidas = {c: getattr(janela._cfg_perfil.dimensoes, c) for c in MEDIDAS}
    repo.guardar(Especificacao("A", **medidas, autor="admin"))
    repo.guardar(OrdemFabrico("OF-A", "A", autor="admin"))
    assert janela._usar_ordem(resolver(repo, "OF-A", ""))
    janela.iniciar()
    esperar(app, lambda: janela._em_revisao is not None)
    proposta = janela._em_revisao.classe.currentData()
    assert proposta, "um NOK com defeitos traz a classe proposta"
    _producao_ate_ao_fim(app, janela, ("falso NOK", "NOK"), arrancar=False)

    assert janela.contadores_op.texto().startswith("2 sacos   OK 1   NOK 1   DESCARTADOS 1")
    assert [janela.historico.item(i, 6).text() for i in range(3)] == ["NOK", "", "falso NOK"]
    assert [i.decisao for i in repo.historico("OF-A")] == ["descartado", "", "NOK"]
    pasta = execucao(tmp_path / "res")
    assert [linha["decisao"] for linha in ler_csv(pasta / "revisoes.csv")] == ["falso NOK", "NOK"]
    dataset = tmp_path / "dataset" / "linha"
    assert len(list((dataset / "ok").glob("*canto_dobrado.png"))) == 1
    comprido = list(dataset.rglob("*comprido_158.png"))
    assert len(comprido) == 1 and comprido[0].parent.name == "dimensao"
    assert [linha["classe_real"] for linha in ler_csv(tmp_path / "res" / "correcoes.csv")] == ["ok", "dimensao"]

    janela._corrigir("varios")  # o saco no ecrã (comprido_158): Engenharia corrige a classe
    comprido = list(dataset.rglob("*comprido_158.png"))
    assert len(comprido) == 1 and comprido[0].parent.name == "varios", "movida, não duplicada"


def test_marcar_zonas_da_ruga_depois_de_guardar(app, janela, tmp_path):
    """Guardar no dataset como rugas entra no modo zonas: cada retângulo arrastado vai para o .json da
    imagem (em px da imagem guardada); Concluir sai e o arrastar volta a ser a ROI da câmara."""
    _producao_ate_ao_fim(app, janela)
    janela._corrigir("rugas")
    visto = janela._base
    assert janela._zonas_de == (visto, "rugas") and janela.imagem_eng.desenhar_roi
    assert not janela.btn_zonas_fim.isHidden()
    janela._roi_desenhada(10, 20, 40, 30)
    janela._roi_desenhada(50, 60, 12, 14)
    fx = visto.resultado.captura.imagem.shape[1] / visto.anotada.shape[1]
    assert zonas(visto.no_dataset) == [(round(10 * fx), round(20 * fx), round(40 * fx), round(30 * fx)),
                                       (round(50 * fx), round(60 * fx), round(12 * fx), round(14 * fx))]
    assert len(janela.imagem_eng.auxilios) == 2, "as zonas ficam desenhadas por cima"
    roi = janela._cfg_edicao.aquisicao.roi()
    janela._terminar_zonas()
    assert janela._zonas_de is None and not janela.imagem_eng.desenhar_roi and janela.btn_zonas_fim.isHidden()
    assert janela._cfg_edicao.aquisicao.roi() == roi, "as zonas não mexeram na ROI da câmara"
    janela._corrigir("ok")
    assert janela._zonas_de is None, "um saco OK não tem zonas"


def test_parar_durante_uma_revisao(app, janela):
    janela.iniciar()
    esperar(app, lambda: janela._em_revisao is not None)
    assert "EM REVISÃO" in janela.indicador.text()
    janela.parar()
    esperar(app, lambda: janela.ciclo is None and janela._em_revisao is None)


def test_explicar_porque_a_inspecao_parou():
    assert explicar("404057 requested and 0 written")[0].startswith("Disco cheio")
    texto, camara = explicar("Câmara Basler 1: não abriu; ... The device is controlled by another application.")
    assert "noutro programa" in texto and camara
    assert explicar("A câmara deixou de enviar imagens.")[1]
    texto, camara = explicar("ValueError: algo novo\nlinha 2")
    assert "algo novo" in texto and "linha 2" not in texto and not camara


def test_motivo_do_saco_e_alarme_de_nok():
    def resultado(estado, defeitos=(), mensagem=""):
        return Resultado(IMG, estado, None, [Relatorio("dimensoes", estado, tuple(defeitos))], 1.0, mensagem)
    largo = Defeito(TipoDefeito.DIMENSAO, descricao="largura 58.0 mm (55 +/- 1)")
    consequencia = Defeito(TipoDefeito.DIMENSAO, descricao="comprimento", causa="rugas")
    assert motivo(resultado(Estado.OK)) == ""
    assert motivo(resultado(Estado.NOK, [largo, consequencia])) == "largura 58.0 mm (55 +/- 1)"
    assert motivo(resultado(Estado.NOK, [largo] * 5)).endswith("; e mais 2")
    assert motivo(resultado(Estado.ERRO, mensagem="medição não fiável\ndetalhe")) == "medição não fiável"
    assert taxa_nok(0, 0) == "" and taxa_nok(7, 1) == "12,5 % NOK"
    alarme = AlarmeNOK(sacos=4, maximo=2)
    for nok in (True, False, True):
        alarme.contar(nok)
    assert alarme.ativo and alarme.texto().startswith("2 NOK nos últimos 3 sacos")
    alarme.contar(False)
    alarme.contar(False)  # o 1.º NOK sai da janela dos 4
    assert not alarme.ativo


def test_operador_ve_o_motivo_porque_parou_e_os_semaforos(app, janela, monkeypatch):
    janela._alarme = AlarmeNOK(sacos=20, maximo=2)
    assert janela.sem_camara.text().endswith("parada") and "GB livres" in janela.sem_disco.text()
    _producao_ate_ao_fim(app, janela, ("NOK", "NOK"))
    esperar(app, lambda: janela.avisos.texto("parou"))
    assert not janela.motivo_op.isHidden(), "o último saco é NOK: diz porquê"
    assert "comprimento" in janela.motivo_op.text()
    assert "% NOK" in janela.contadores_op.tempos.text()
    assert janela.avisos.texto("nok").startswith("2 NOK"), "dois NOK em três sacos com o máximo a 2"
    # A câmara simulada acaba depois dos três sacos: a faixa diz o que fazer e o semáforo fica vermelho.
    assert "deixou de enviar imagens" in janela.avisos.texto("parou")
    assert janela.sem_camara.cor == VERMELHO
    janela.iniciar()  # o Iniciar seguinte limpa o aviso de antes
    assert janela.avisos.texto("parou") == "" and janela.avisos.texto("nok") == ""
    esperar(app, lambda: janela._em_revisao is not None)
    janela.parar()
    esperar(app, lambda: janela.ciclo is None and janela._em_revisao is None)


def test_ultimos_sacos_guardam_so_os_ultimos_e_mudam_de_cor_com_a_decisao(app):
    faixa = UltimosSacos(quantos=3)
    escolhidos = []
    faixa.escolhido.connect(escolhidos.append)
    for numero, decisao in enumerate(("OK", "em revisão", "OK", "em revisão"), start=1):
        faixa.acrescentar(numero, decisao, "comprimento 158 mm" if decisao != "OK" else "")
    faixa.decidir(4, "NOK")
    assert faixa.decisoes() == [(2, "em revisão"), (3, "OK"), (4, "NOK")], "o saco 1 já saiu"
    botao = faixa._botoes[4]
    assert botao.toolTip() == "saco 4: NOK\ncomprimento 158 mm", "a decisão muda, o motivo fica"
    botao.click()
    assert escolhidos == [4]


def test_operador_reve_um_saco_e_engenharia_esconde_um_modulo(app, janela):
    _producao_ate_ao_fim(app, janela, ("descartado", "NOK"))
    assert janela.ultimos_op.decisoes() == [(1, "descartado"), (2, "OK"), (3, "NOK")]
    janela.ultimos_op._botoes[1].click()  # o 1.º saco volta ao ecrã
    assert janela._base.numero == 1 and janela.faixa_op.text().startswith("NOK")
    janela.ultimos_op._botoes[3].click()
    assert list(janela._caixas) == ["dimensoes"], "uma caixa por módulo com defeitos"
    antes = janela._base.anotada.copy()
    janela._caixas["dimensoes"].setChecked(False)
    assert janela._esconder == {"dimensoes"} and not np.array_equal(janela._base.anotada, antes)
    janela._caixas["dimensoes"].setChecked(True)
    assert np.array_equal(janela._base.anotada, antes), "volta a ser a mesma imagem"
    janela.historico._vistos.clear()  # as imagens já saíram da memória
    janela.ultimos_op._botoes[2].click()
    assert janela._base.numero == 3, "fica o que estava; a mensagem diz porquê"


def test_disco_quase_cheio_avisa_e_nao_deixa_iniciar(app, janela, monkeypatch):
    livres = {"gb": 1.0}
    monkeypatch.setattr("interface.janela.shutil.disk_usage", lambda _: type("U", (), {"free": livres["gb"] * 1e9}))
    janela._verificar()
    assert janela.sem_disco.cor == AMARELO and janela.avisos.texto("disco").startswith("Disco quase cheio")
    livres["gb"] = 0.1
    criticas = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: criticas.append(a[1]))
    janela.iniciar()
    assert criticas == ["Disco cheio"] and janela.ciclo is None, "não começa: o 1.º saco ia falhar ao gravar"
    assert janela.sem_disco.cor == VERMELHO
    livres["gb"] = 50.0
    janela._verificar()
    assert janela.sem_disco.cor == VERDE and janela.avisos.texto("disco") == ""


def test_ao_vivo_espera_pelos_sacos_e_para(app, janela):
    janela.btn_ao_vivo.setChecked(True)
    janela.iniciar()
    esperar(app, lambda: janela.ciclo is not None and janela.ciclo.pasta_resultados is not None)
    assert janela.ciclo.pedido.modo == "ao_vivo" and "A CORRER" in janela.indicador.text()
    janela.parar()
    esperar(app, lambda: janela.ciclo is None)
    assert janela.historico.rowCount() == 0, "sem sacos a passar, nada registado"


def test_trocar_de_modo_a_correr(app, janela):
    janela.btn_ao_vivo.setChecked(True)
    janela.iniciar()  # ao vivo, sem sacos a passar, nunca acaba sozinho
    esperar(app, lambda: janela.ciclo is not None and janela.ciclo.pasta_resultados is not None)
    janela.btn_trigger.click()  # recomeça em Trigger sem se carregar em Parar
    esperar(app, lambda: janela.ciclo is not None and janela.ciclo.pedido.modo == "trigger")
    _producao_ate_ao_fim(app, janela, arrancar=False)
    assert janela.historico.rowCount() == 3


class CamaraAfinar(CamaraSimulada):
    """Imagens sem fim e sem sacos, como a câmara a afinar; guarda as exposições pedidas com ajustar()."""

    def __init__(self, cfg: Config) -> None:
        super().__init__([])
        self.modo = cfg.disparo.modo
        self.exposicoes = [cfg.aquisicao.exposicao_us]

    def ler(self) -> Captura | None:
        time.sleep(0.01)
        return None if self.parada else Captura(IMG, "afinar.png", time.time(), fonte="simulada")

    def ajustar(self, aquisicao) -> bool:
        self.exposicoes.append(aquisicao.exposicao_us)
        return True


def test_afinar_segue_o_modo_sem_inspecionar_e_passa_a_inspecao(app, janela, tmp_path, monkeypatch):
    abertas: list[CamaraAfinar] = []
    inspecao = ciclo.criar_fonte  # a da janela (Trigger: os três sacos)

    def fabrica(cfg):
        if janela.ciclo is not None:  # a inspeção: o ciclo já existe quando abre a câmara
            return inspecao(cfg)
        abertas.append(CamaraAfinar(cfg))
        return abertas[-1]

    com_camara(monkeypatch, ciclo, fabrica)
    janela.btn_ao_vivo.click()  # parado: só escolhe o modo
    assert janela.afinacao is None
    janela.btn_afinar.click()
    esperar(app, lambda: janela.imagem_eng._original is not None and "img/s" in janela.lbl_medidas.text())
    assert "A AFINAR" in janela.indicador.text() and janela.lbl_medidas.text().startswith("brilho 0")
    assert abertas[-1].modo == "ao_vivo" and janela.editor_camara.secao.currentData() == "aquisicao"

    _campo(janela.editor_camara, "aquisicao", "exposicao_us").setValue(1234.0)
    esperar(app, lambda: abertas[-1].exposicoes[-1] == 1234.0)
    assert len(abertas) == 1, "a exposição muda sem reabrir a câmara"
    assert janela.btn_roi.isEnabled()
    janela.btn_roi.setChecked(True)
    assert janela.imagem_eng.desenhar_roi
    assert not janela.barra_afinacao.isHidden() and janela.lbl_passo.text().startswith("Arraste")
    janela.imagem_eng.roi_desenhada.emit(2, 3, 20, 10)  # como se a tivesse arrastado na imagem (30 × 20 px)
    assert janela._cfg_edicao.aquisicao.roi() == (2, 3, 20, 10)
    assert [nome for _, _, nome in janela.imagem_eng.auxilios] == ["ROI", "barreira"]
    assert not janela.btn_roi.isChecked() and janela.lbl_passo.text().startswith("2 · ")  # agora o fundo vazio
    assert _campo(janela.editor_camara, "aquisicao", "roi_largura_px").value() == 20
    app.processEvents()
    assert len(abertas) == 1, "a afinar vê-se a imagem inteira: a ROI não reabre a câmara"
    _campo(janela.editor_camara, "aquisicao", "timeout_ms").setValue(2500)
    esperar(app, lambda: len(abertas) == 2 and abertas[0].fechada)

    janela.btn_trigger.click()  # a afinar: passa logo a Trigger, uma imagem por sinal
    esperar(app, lambda: "sinal n.º" in janela.lbl_medidas.text())
    assert janela.afinacao.modo == "trigger" and abertas[-1].modo == "trigger" and abertas[-2].fechada
    assert janela.editor_camara.secao.currentData() == "disparo"
    assert [nome for _, _, nome in janela.imagem_eng.auxilios] == ["ROI"], "em Trigger não há barreira"
    assert not janela.barra_afinacao.isHidden(), "a ROI também se define a afinar em Trigger"
    _campo(janela.editor_camara, "aquisicao", "exposicao_us").setValue(2000.0)  # sai da espera do sinal
    esperar(app, lambda: abertas[-1].exposicoes[-1] == 2000.0)
    assert not (tmp_path / "res").exists(), "a afinar não se regista nada"

    _producao_ate_ao_fim(app, janela)  # Iniciar: a afinação para e a inspeção abre a câmara
    assert all(c.fechada for c in abertas) and janela.afinacao is None and not janela.btn_afinar.isChecked()
    assert janela.imagem_eng.auxilios == [] and janela.barra_afinacao.isHidden()
    assert janela.historico.rowCount() == 3


def test_afinar_com_contorno_mede_o_saco_na_roi(app, janela, monkeypatch):
    cena, (cx, cy) = cena_saco(np.random.default_rng(3), angulo=10.0)

    class CamaraSaco(CamaraAfinar):
        def ler(self) -> Captura | None:
            time.sleep(0.01)
            return None if self.parada else Captura(cena, "saco.png", time.time(), fonte="simulada")

    com_camara(monkeypatch, ciclo, CamaraSaco)
    janela.btn_ao_vivo.click()
    janela.btn_afinar.click()
    janela._definir_roi(150, 200, 1000, 600)  # o saco (≈ 790 × 400 px) inteiro lá dentro, com fundo à volta
    janela.btn_contorno.setChecked(True)
    esperar(app, lambda: len(janela._medicoes) >= 2)
    comprimentos, larguras = np.array(janela._medicoes).T
    assert np.allclose(comprimentos, COMPRIMENTO, atol=0.5) and np.allclose(larguras, LARGURA, atol=0.5)
    contorno, ideal = janela.imagem_eng.contorno, janela.imagem_eng.ideal
    assert abs(contorno[:, 0].mean() - cx) < 10 and abs(contorno[:, 1].mean() - cy) < 10, "na imagem inteira"
    assert np.allclose(ideal.mean(axis=0), (cx, cy), atol=2), "o contorno ideal, liso, no mesmo sítio"
    texto = janela.lbl_medidas.text()
    assert "RMS" in texto and "rejeitados" in texto and "σ C" in texto and "face ao nominal" in texto
    janela.btn_recomecar.click()
    assert janela._medicoes == [] and janela.imagem_eng.contorno is None and janela.imagem_eng.ideal is None
    janela.btn_contorno.setChecked(False)
    assert janela.afinacao.medir_contorno is False


def test_afinar_mostra_o_contorno_e_porque_a_inspecao_nao_confiaria_nele(app):
    """Com o controlo da medição a falhar, a afinação mede na mesma (é aí que se acerta a luz) e diz porquê."""
    from interface import janela as modulo_janela
    cena, _ = cena_saco(np.random.default_rng(3), angulo=10.0)
    cfg = config_teste(localizacao={"qualidade_contraste_min": 0.99})
    medicao = ciclo.Afinacao(cfg)._medir(cena)
    assert medicao.contorno is not None and not medicao.erro and abs(medicao.comprimento_mm - COMPRIMENTO) < 0.5
    assert medicao.nao_fiavel.startswith("medição não fiável: o saco quase não difere do fundo")
    texto = modulo_janela._texto_contorno(medicao, [(medicao.comprimento_mm, medicao.largura_mm)], cfg)
    assert "Na inspeção: ERRO, medição não fiável" in texto and "aresta" in texto and "reflexos" in texto


def test_fundo_vazio_e_calibrar_escala_a_afinar(app, janela, tmp_path, monkeypatch):
    com_camara(monkeypatch, ciclo, CamaraAfinar)
    janela._dataset = tmp_path / "dataset"
    janela.btn_ao_vivo.click()
    janela.btn_afinar.click()
    esperar(app, lambda: janela._imagem_afinacao is not None)
    janela.btn_fundo.click()  # sem saco à vista: a imagem inteira passa a ser o fundo do perfil
    assert (tmp_path / "dataset" / "fundo.png").is_file()
    assert janela._cfg_edicao.localizacao.fundo_referencia == str(tmp_path / "dataset" / "fundo.png")

    ppm = janela._cfg_edicao.geral.px_por_mm
    d = janela._cfg_edicao.dimensoes
    janela._medicoes[:] = [(d.comprimento_nominal_mm * 1.04, d.largura_nominal_mm * 1.04)] * 3  # 4 % maior
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    janela.btn_escala.click()
    assert janela._cfg_edicao.geral.px_por_mm == pytest.approx(ppm * 1.04, abs=1e-3)
    assert janela._medicoes == [], "com outra escala, a contagem recomeça"


def test_pre_visualizar_e_guardar_no_perfil(app, janela, tmp_path, monkeypatch):
    _producao_ate_ao_fim(app, janela)  # termina no comprido_158: NOK com a tolerância de 3 mm
    _campo(janela.editor, "dimensoes", "tolerancia_comprimento_mm").setValue(9.0)
    esperar(app, lambda: "PRÉ-VISUALIZAÇÃO" in janela.titulo_imagem.text())
    assert janela.faixa_eng.text().startswith("OK"), "com 9 mm de tolerância os 158 mm passam"

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    janela.editor.guardar.emit()
    perfil = json.loads((tmp_path / "perfil.json").read_text(encoding="utf-8"))
    assert perfil["dimensoes"]["tolerancia_comprimento_mm"] == 9.0
    assert Config.carregar(tmp_path / "perfil.json").dimensoes.tolerancia_comprimento_mm == 9.0


# --- Ordens de fabrico e produtos ------------------------------------------------------------

def test_mudar_de_of_a_meio_nao_afeta_o_saco_em_analise(app, janela, tmp_path):
    repo = janela._repositorio()
    medidas = {c: getattr(janela._cfg_perfil.dimensoes, c) for c in MEDIDAS}
    for produto, of in (("A", "OF-A"), ("B", "OF-B")):
        repo.guardar(Especificacao(produto, **medidas, autor="admin"))
        repo.guardar(OrdemFabrico(of, produto, autor="admin"))
    assert janela._usar_ordem(resolver(repo, "OF-A", ""))
    assert janela.btn_of.text() == "OF OF-A · A"
    janela.iniciar()
    esperar(app, lambda: janela._em_revisao is not None)  # o 1.º saco (canto_dobrado) à espera da decisão
    assert janela._usar_ordem(resolver(repo, "OF-B", ""))  # a meio: só entra no saco seguinte
    _producao_ate_ao_fim(app, janela, arrancar=False)
    linhas = ler_csv(execucao(tmp_path / "res") / "registo.csv")
    assert [linha["of"] for linha in linhas] == ["OF-A", "OF-B", "OF-B"]
    assert [i.decisao for i in repo.historico("OF-A")] == ["NOK"]
    assert [i.final for i in repo.historico("OF-B")] == ["OK", "NOK"]

    janela._abrir_dashboard()
    v = janela.janela_dashboard.vista
    assert v.tabela_of.rowCount() == 2 and v.tabela_produtos.rowCount() == 2 and v.tabela_dias.rowCount() == 1
    assert "OF OF-B" in v.resumo.text() and "taxa de NOK 50.0 %" in v.resumo.text()
    assert v.grafico._evolucao and not v.grafico.grab().isNull()

    v.tabela_of.selectRow(1)  # OF-A escolhida...
    repo.registar(Resultado(None, Estado.OK, None, [], 1.0, of="OF-A", produto="A"), "outra", 1)
    esperar(app, lambda: v.tabela_of.item(1, 3).text() == "2")  # ...chega um saco: atualiza-se sozinho
    assert v.of_escolhida() == "OF-A", "a OF escolhida mantém-se"
    v.procura_of.setText("of-b")
    assert v.tabela_of.rowCount() == 1 and v.of_escolhida() == "OF-B"


def test_dashboard_reiniciar_e_limpar_dados(app, janela, monkeypatch):
    repo = janela._repositorio()
    repo.guardar(_produto())
    repo.guardar(OrdemFabrico("OF1", "PreVital"))
    repo.registar(Resultado(None, Estado.OK, None, [], 1.0, of="OF1", produto="PreVital"), "s", 1)
    janela._abrir_dashboard()
    v = janela.janela_dashboard.vista
    assert not v.btn_limpar.isHidden(), "SAIDA_DASHBOARD_LIMPAR: em testes, o botão está lá"

    v.procura_of.setText("nada")
    v.separadores.setCurrentIndex(2)
    v.btn_reiniciar.click()
    assert v.procura_of.text() == "" and v.separadores.currentIndex() == 0 and v.tabela_of.rowCount() == 1

    monkeypatch.setattr(vista_dashboard, "perguntar_limpeza", lambda pai: None)  # Cancelar
    v.btn_limpar.click()
    assert len(repo.historico()) == 1, "cancelado: não apaga nada"
    monkeypatch.setattr(vista_dashboard, "perguntar_limpeza", lambda pai: False)  # Só as inspeções
    v.btn_limpar.click()
    assert repo.historico() == [] and v.tabela_of.item(0, 3).text() == "0" and v.tabela_dias.rowCount() == 0
    monkeypatch.setattr(vista_dashboard, "perguntar_limpeza", lambda pai: True)  # Tudo
    v.btn_limpar.click()
    assert v.tabela_of.rowCount() == 0 and v.tabela_produtos.rowCount() == 0

    v.permitir_limpar(False)  # na linha: SAIDA_DASHBOARD_LIMPAR = False
    assert v.btn_limpar.isHidden()


def test_janela_do_biapp_dashboard_mostra_o_que_chega_por_sse(app, tmp_path):
    repo = Repositorio(tmp_path / "bd.sqlite")
    repo.guardar(_produto())
    repo.guardar(OrdemFabrico("OF1", "PreVital"))
    servidor = Servidor(repo, "127.0.0.1", 0, 0.05).iniciar()
    recebidos, problemas = [], []
    try:
        threading.Thread(target=biapp_dashboard.receber, args=(servidor.url, recebidos.append, problemas.append),
                         daemon=True).start()
        esperar(app, lambda: recebidos, 10)
        assert recebidos[0]["ordens"][0]["numero"] == "OF1"
    finally:
        servidor.parar()
    esperar(app, lambda: problemas, 40)  # o servidor parou: o cliente avisa e volta a tentar


def test_permissoes_das_ofs(app, janela, monkeypatch):
    assert pode("admin", "dashboard") and not pode("operador", "dashboard") and pode("operador", "editar_of")
    assert assinatura("admin", "admin") == "admin", "o admin não tem de se identificar"
    with pytest.raises(ValueError, match="nome"):
        assinatura("operador", "operador", "Ana", "")
    assert assinatura("operador", "operador", " Ana ", "42") == "Ana (nº 42)"

    janela._definir_utilizador("op")
    assert not janela.acao_dashboard.isVisible() and not janela.btn_of.isHidden()
    pedidos = []
    monkeypatch.setattr(Janela, "_escolher_ordem", lambda self: pedidos.append(1) or False)
    janela.iniciar()
    assert pedidos == [1] and janela.ciclo is None, "o operador indica a OF a cada início"

    janela._definir_utilizador("admin")
    janela._ensaio_sem_of = False
    janela.iniciar()
    assert pedidos == [1, 1] and janela.ciclo is None, "o admin sem OF também é perguntado"


def test_resolucao_da_localizacao_no_produto_e_ao_trocar_de_of(app, janela):
    """O produto pode ter a sua resolução da localização (0 = a do perfil): entra com a OF desse produto e sai
    com ela. Mudada na janela de uma OF, grava uma versão nova do produto: fica para todas as OFs dele."""
    repo = janela._repositorio()
    medidas = {c: getattr(janela._cfg_perfil.dimensoes, c) for c in MEDIDAS}
    do_perfil = janela._cfg_perfil.localizacao.resolucao_px_por_mm
    for produto, of in (("Mate", "OF-M"), ("Brilho", "OF-B")):
        repo.guardar(Especificacao(produto, **medidas, autor="admin"))
        repo.guardar(OrdemFabrico(of, produto, autor="admin"))
    form = DialogoEspecificacao(janela, janela, "produto", "admin", produto=repo.procurar_produto("Mate"))
    assert form.isAncestorOf(form.resolucao) and form.resolucao.value() == 0
    assert form.resolucao.specialValueText() == f"do perfil ({do_perfil:g})"
    assert "20 = sem redução (brilhantes); 5 = mais rápido (mate)" in form.resolucao.toolTip()
    assert "equivalem a sem redução" in form.resolucao.toolTip()
    form.resolucao.setValue(5.0)
    form._guardar()
    assert form.result() == QDialog.DialogCode.Accepted, form.erro.text()
    mate = repo.procurar_produto("Mate")
    assert mate.resolucao_localizacao_px_por_mm == 5.0 and {c: getattr(mate, c) for c in MEDIDAS} == medidas

    assert janela._usar_ordem(resolver(repo, "OF-M", ""))
    assert janela._cfg_edicao.localizacao.resolucao_px_por_mm == 5.0, "a do produto da OF"
    assert janela._usar_ordem(resolver(repo, "OF-B", ""))
    assert janela._cfg_edicao.localizacao.resolucao_px_por_mm == do_perfil, "outro produto, sem o campo: a do perfil"
    assert janela._usar_ordem(resolver(repo, "OF-M", "")) and janela._usar_ordem(None)
    assert janela._cfg_edicao.localizacao.resolucao_px_por_mm == do_perfil, "sem OF: a do perfil"

    form = DialogoEspecificacao(janela, janela, "of", "admin", produto=mate, of=repo.procurar_of("OF-M"))
    assert form.isAncestorOf(form.resolucao) and form.resolucao.value() == 5.0, "a OF mostra a do produto"
    antes = repo.procurar_produto("Mate")
    form._guardar()
    assert repo.procurar_of("OF-M").ajustes == {}
    assert repo.procurar_produto("Mate") == antes, "sem mudar a resolução, o produto não ganha versão"

    # Mudada na OF: fica no produto (uma versão nova), não na OF, e vale também para outra OF do produto
    repo.guardar(OrdemFabrico("OF-M2", "Mate", {"tolerancia_largura_mm": 4.0}, autor="admin"))
    form = DialogoEspecificacao(janela, janela, "of", "operador", produto=mate, of=repo.procurar_of("OF-M2"))
    form.resolucao.setValue(4.0)
    form._guardar()
    assert form.result() == QDialog.DialogCode.Accepted, form.erro.text()
    mate = repo.procurar_produto("Mate")
    assert mate.resolucao_localizacao_px_por_mm == 4.0 and mate.autor == "operador"
    assert {c: getattr(mate, c) for c in MEDIDAS} == medidas, "as medidas do produto não mudam"
    assert repo.procurar_of("OF-M2").ajustes == {"tolerancia_largura_mm": 4.0}, "a OF guarda só os seus ajustes"
    assert janela._usar_ordem(resolver(repo, "OF-M", ""))
    assert janela._cfg_edicao.localizacao.resolucao_px_por_mm == 4.0, "a última definida, noutra OF do produto"
    form = DialogoEspecificacao(janela, janela, "of", "admin", produto=mate, of=repo.procurar_of("OF-M"))
    form.resolucao.setValue(0.0)
    form._guardar()
    assert repo.procurar_produto("Mate").resolucao_localizacao_px_por_mm is None, "0 = volta à do perfil"
    assert janela._usar_ordem(None)
    assert janela._cfg_edicao.localizacao.resolucao_px_por_mm == do_perfil, "sem OF: a do perfil"


def test_raio_dos_cantos_no_produto_e_ao_trocar_de_of(app, janela):
    """O produto pode ter os seus cantos (−1 = os do perfil): entram com a OF desse produto e saem com ela;
    a janela da OF não os mostra."""
    repo = janela._repositorio()
    medidas = {c: getattr(janela._cfg_perfil.dimensoes, c) for c in MEDIDAS}
    perfil = janela._cfg_perfil.dimensoes
    do_perfil = (perfil.forma_raio_canto_mm, perfil.forma_raio_canto_outra_ponta_mm)
    for produto, of in (("Cortados", "OF-C"), ("Redondos", "OF-R")):
        repo.guardar(Especificacao(produto, **medidas, autor="admin"))
        repo.guardar(OrdemFabrico(of, produto, autor="admin"))
    form = DialogoEspecificacao(janela, janela, "produto", "admin", produto=repo.procurar_produto("Cortados"))
    assert form.isAncestorOf(form.raio_canto) and form.raio_canto.value() == -1
    assert form.raio_canto.specialValueText().startswith("do perfil (") and not form.raio_outra_ponta.isEnabled()
    form.raio_canto.setValue(6.0)
    assert form.raio_outra_ponta.isEnabled() and form.raio_outra_ponta.specialValueText() == "igual"
    form.raio_outra_ponta.setValue(20.0)
    form._guardar()
    assert form.result() == QDialog.DialogCode.Accepted, form.erro.text()
    cortados = repo.procurar_produto("Cortados")
    assert (cortados.raio_canto_mm, cortados.raio_canto_outra_ponta_mm) == (6.0, 20.0)
    assert repo.procurar_produto("Redondos").raio_canto_mm is None

    def cantos():
        d = janela._cfg_edicao.dimensoes
        return d.forma_raio_canto_mm, d.forma_raio_canto_outra_ponta_mm
    assert janela._usar_ordem(resolver(repo, "OF-C", "")) and cantos() == (6.0, 20.0), "os do produto da OF"
    assert janela._usar_ordem(resolver(repo, "OF-R", "")) and cantos() == do_perfil, "outro produto: os do perfil"
    assert janela._usar_ordem(resolver(repo, "OF-C", "")) and janela._usar_ordem(None)
    assert cantos() == do_perfil, "sem OF: os do perfil"

    form = DialogoEspecificacao(janela, janela, "of", "admin", produto=cortados, of=repo.procurar_of("OF-C"))
    assert not form.isAncestorOf(form.raio_canto), "a OF não muda os cantos"
    form._guardar()
    assert repo.procurar_of("OF-C").ajustes == {} and repo.procurar_produto("Cortados").raio_canto_mm == 6.0


def test_criar_produto_e_of_no_formulario_e_no_dialogo(app, janela):
    repo = janela._repositorio()
    form = DialogoEspecificacao(janela, janela, "novo", "admin", of=OrdemFabrico("OF9", "Novo"), nome="Novo")
    assert form.medidas["comprimento_nominal_mm"].value() == 150.0, "começa nas medidas em uso"
    form.medidas["tolerancia_comprimento_mm"].setValue(9.0)
    form.ed_cor.setText("azul")
    form.verniz.setChecked(True)
    form.easy_open.setChecked(True)
    form._guardar()
    assert form.result() == QDialog.DialogCode.Accepted, form.erro.text()
    produto = repo.procurar_produto("novo")
    assert (produto.tolerancia_comprimento_mm, produto.cor, produto.verniz, produto.easy_open) == (9.0, "azul", True, True)
    assert repo.procurar_of("OF9").produto == "Novo"

    form = DialogoEspecificacao(janela, janela, "of", "Ana (nº 7)", produto=produto, of=repo.procurar_of("OF9"))
    assert form.ed_cor.text() == "azul"
    form.medidas["tolerancia_largura_mm"].setValue(4.0)
    assert form.easy_open.isChecked(), "a OF parte do produto"
    form.easy_open.setChecked(False)  # EO não, só nesta OF
    form._guardar()
    of = repo.procurar_of("OF9")
    assert of.ajustes == {"tolerancia_largura_mm": 4.0, "easy_open": False} and of.autor == "Ana (nº 7)"

    form = DialogoEspecificacao(janela, janela, "novo", "admin", nome="Enorme")
    form.medidas["comprimento_nominal_mm"].setValue(900.0)
    form._guardar()
    assert "fora dos limites" in form.erro.text() and repo.procurar_produto("Enorme") is None

    janela._definir_utilizador("op")
    dialogo = DialogoOrdem(janela, repo)
    dialogo.ed_of.setText("OF10")
    dialogo.cb_produto.setCurrentText("Novo")
    assert "OF nova" in dialogo.estado.text()
    dialogo._continuar()
    assert "nome" in dialogo.estado.text() and dialogo.result() != QDialog.DialogCode.Accepted
    dialogo.ed_nome.setText("Ana")
    dialogo.ed_numero.setText("7")
    dialogo._continuar()
    assert dialogo.result() == QDialog.DialogCode.Accepted
    of10 = repo.procurar_of("OF10")
    assert of10.autor == "Ana (nº 7)" and of10.ajustes == of.ajustes, "as últimas especificações usadas"
    assert janela._usar_ordem(dialogo.escolha)
    assert janela._cfg_edicao.dimensoes.tolerancia_largura_mm == 4.0
    assert janela._cfg_edicao.dimensoes.tolerancia_comprimento_mm == 9.0


# --- Verificar rugas (só o M2 e o M5, a pedido) ------------------------------------------------

def _esperar_verificacao(app, j: Janela) -> None:
    esperar(app, lambda: j._verificacao is None)


def test_verificar_rugas_numa_imagem_ensinar_ok_e_guardar_com_rugas(app, janela, tmp_path):
    from testes.test_m5_relevo import cena
    caminho = tmp_path / "saco impresso.png"
    guardar_imagem(caminho, cena(np.random.default_rng(21), impressao=True))
    janela._abrir_janela_rugas()
    rugas = janela.janela_rugas
    assert rugas.btn_ensinar.isVisibleTo(rugas), "o administrador ensina"
    janela._verificar_rugas_em(str(caminho))
    _esperar_verificacao(app, janela)
    assert rugas.lbl_veredito.text().startswith("RUGA DETETADA"), "sem referência, o traço impresso passa por ruga"
    assert "sem referência" in rugas.lbl_info.text()
    assert any(nome.startswith("relevo_") for nome, _ in rugas.visto.etapas), "as etapas do M5"

    rugas.btn_ensinar.click()  # é OK: a impressão dele vai para a referência e verifica-se de novo
    pasta = janela._dataset / "relevo_referencia"
    assert len(list(pasta.glob("*_util.png"))) == 1 and len(list(pasta.glob("*.png"))) == 2
    assert janela._cfg_edicao.relevo.referencia_pasta == str(pasta), "fica na edição: Guardar no perfil para manter"
    assert len(list((janela._dataset / "ok").glob("*.png"))) == 1
    _esperar_verificacao(app, janela)
    assert rugas.lbl_veredito.text() == "SEM RUGAS", rugas.lbl_info.text()

    rugas.btn_rugas.click()  # afinal tem rugas: a mesma imagem muda de pasta (não fica em duas)
    assert not list((janela._dataset / "ok").glob("*.png"))
    assert len(list((janela._dataset / "rugas").glob("*.png"))) == 1


def test_verificar_rugas_com_a_camara_parada(app, janela, monkeypatch):
    from interface import janela_rugas
    from testes.test_m5_relevo import VINCO, cena
    vincado = cena(np.random.default_rng(22), vincos=[VINCO])
    com_camara(monkeypatch, janela_rugas, lambda cfg: CamaraSimulada([vincado]))
    janela._verificar_rugas()  # parada: abre a câmara, tira uma imagem e fecha-a
    _esperar_verificacao(app, janela)
    assert janela.janela_rugas.lbl_veredito.text() == "RUGA DETETADA", janela.janela_rugas.lbl_info.text()
    assert "câmara" in janela.janela_rugas.lbl_info.text()

    monkeypatch.setattr(janela_rugas, "ESPERA_CAMARA_S", 0.2)
    com_camara(monkeypatch, janela_rugas, lambda cfg: CamaraSimulada([], sem_fim=True))
    janela._verificar_rugas()  # uma câmara que não dá imagens não pendura a verificação
    _esperar_verificacao(app, janela)
    assert janela.janela_rugas.lbl_veredito.text() == "NÃO VERIFICADO"
    assert "não deu nenhuma imagem" in janela.janela_rugas.lbl_info.text()


def test_verificar_rugas_so_o_administrador_ensina(app, janela):
    janela._definir_utilizador("operador")
    janela._abrir_janela_rugas()
    assert not janela.janela_rugas.btn_ensinar.isVisibleTo(janela.janela_rugas)
    assert not janela.janela_rugas.btn_rugas.isVisibleTo(janela.janela_rugas)
    assert not janela.janela_rugas.btn_nao_ruga.isVisibleTo(janela.janela_rugas)


def test_verificar_rugas_nao_e_ruga(app, janela, tmp_path):
    """Não é ruga…: das rugas achadas (com o número da imagem), as escolhidas vão para a referência do M5
    (nao_rugas/, não como saco OK) e a imagem verifica-se de novo, já sem elas."""
    from testes.test_m5_relevo import TRACO_IMPRESSO_GRAUS, VINCO, cena, mesmo_angulo
    caminho = tmp_path / "saco impresso.png"
    guardar_imagem(caminho, cena(np.random.default_rng(34), impressao=True, vincos=[VINCO]))
    janela._abrir_janela_rugas()
    rugas = janela.janela_rugas
    assert rugas.btn_nao_ruga.isVisibleTo(rugas) and not rugas.btn_nao_ruga.isEnabled()
    janela._verificar_rugas_em(str(caminho))
    _esperar_verificacao(app, janela)
    assert rugas.lbl_veredito.text() == "RUGA DETETADA (2)", rugas.lbl_info.text()
    assert rugas.btn_nao_ruga.isEnabled()

    oferecidas = []

    def escolher(lista):
        oferecidas.extend(lista)
        return [d for _, d in lista if mesmo_angulo(d.angulo_graus, TRACO_IMPRESSO_GRAUS)]

    rugas.escolher_nao_rugas = escolher
    rugas.btn_nao_ruga.click()
    assert [n for n, _ in oferecidas] == [1, 2], "com o número que têm na imagem"
    pasta = janela._dataset / "relevo_referencia"
    assert len(list((pasta / "nao_rugas").glob("*.json"))) == 1
    assert not list(pasta.glob("*.png")) and not list((janela._dataset / "ok").glob("*.png")), "não é um saco OK"
    assert janela._cfg_edicao.relevo.referencia_pasta == str(pasta), "fica na edição: Guardar no perfil para manter"
    _esperar_verificacao(app, janela)
    assert rugas.lbl_veredito.text() == "RUGA DETETADA", "fica o vinco"
    assert "«não é ruga» de 1 imagem(ns) reconhecidas neste saco: 1 reta(s) não contaram" in rugas.lbl_info.text(), \
        rugas.lbl_info.text()


def test_escolher_nao_rugas_so_as_marcadas(app):
    from interface.janela_rugas import EscolherNaoRugas
    d1, d2 = Defeito(TipoDefeito.RUGAS, descricao="ruga de 82 mm"), Defeito(TipoDefeito.RUGAS, descricao="ruga de 50 mm")
    dialogo = EscolherNaoRugas(None, [(1, d1), (3, d2)])
    assert [caixa.text() for caixa, _ in dialogo.caixas] == ["1. ruga de 82 mm", "3. ruga de 50 mm"], "o número da imagem"
    assert not dialogo.btn_ok.isEnabled(), "nenhuma marcada"
    dialogo.caixas[1][0].setChecked(True)
    assert dialogo.escolhidas() == [d2] and dialogo.btn_ok.isEnabled()


def test_verificar_rugas_nao_e_ruga_numa_zona(app, janela, tmp_path):
    """Numa zona escolhida também: verifica-se de novo a mesma zona."""
    from testes.test_m5_relevo import cena
    caminho = tmp_path / "saco.png"
    # Saco a direito, centrado (canto em (250, 275) px): o traço impresso vai de (670, 355) a (940, 695) px
    guardar_imagem(caminho, cena(np.random.default_rng(35), angulo=0.0, impressao=True))
    janela._abrir_janela_rugas()
    rugas = janela.janela_rugas
    janela._verificar_rugas_em(str(caminho))
    _esperar_verificacao(app, janela)
    rugas.btn_zona.click()
    rugas.imagem.roi_desenhada.emit(640, 300, 340, 410)
    assert rugas.lbl_veredito.text().startswith("RUGA DETETADA"), rugas.lbl_info.text()
    assert rugas.btn_nao_ruga.isEnabled(), "também numa zona"
    rugas.escolher_nao_rugas = lambda lista: [d for _, d in lista]
    rugas.btn_nao_ruga.click()
    assert rugas.lbl_veredito.text() == "SEM RUGAS", rugas.lbl_info.text()
    assert "zona escolhida" in rugas.lbl_info.text(), "verificou de novo a mesma zona"
    assert janela._verificacao is None, "sem verificar a imagem toda"


def test_verificar_rugas_sem_o_m2_do_perfil(tmp_path):
    """Para ver rugas não é preciso medir o saco: com a foto do fundo errada, localiza-o sem ela; sem fundo
    nenhum à vista (só o saco por dentro), o M5 vê a imagem toda."""
    from interface.janela_rugas import saco_localizado, verificar, veredito
    from testes.test_m5_relevo import VINCO, cena
    img = cena(np.random.default_rng(23), vincos=[VINCO])
    fundo_errado = tmp_path / "fundo.png"
    guardar_imagem(fundo_errado, np.zeros_like(img))  # como uma foto do fundo de outra luz
    cfg = config_teste(localizacao={"fundo_referencia": str(fundo_errado)})
    r, nota = verificar(cfg, Captura(img, "camara#1", 0.0))
    assert saco_localizado(r) and veredito(r)[1] == "RUGA DETETADA"
    assert "sem a foto do fundo" in nota and "Fundo vazio" in nota

    por_dentro = cena(np.random.default_rng(24), angulo=0.0, vincos=[(40, 10, 110, 80, 0.15)])[325:675, 290:960]
    r, nota = verificar(config_teste(), Captura(por_dentro, "camara#2", 0.0))
    assert not saco_localizado(r) and "imagem toda" in nota
    assert veredito(r)[1] == "RUGA DETETADA" and [d.modulo for d in r.defeitos] == ["relevo"]


def test_verificar_rugas_saco_maior_do_que_o_campo(tmp_path):
    """O saco sai da imagem por um lado (maior do que o campo): o contorno que o M2 tolerante desse seria um
    palpite que corta o saco; o M5 vê a imagem toda."""
    from interface.janela_rugas import cortado_pela_borda, saco_localizado, verificar, veredito
    from modulos.m2_localizacao import Localizador
    from testes.test_m5_relevo import cena
    img = cena(np.random.default_rng(25), angulo=0.0, vincos=[(40, 10, 110, 80, 0.15)])[200:800, 400:1000]
    r, nota = verificar(config_teste(), Captura(img, "camara#3", 0.0))
    assert not saco_localizado(r) and "imagem toda" in nota, nota
    assert veredito(r)[1] == "RUGA DETETADA", r.relatorios[-1].resumo
    inteiro = cena(np.random.default_rng(25), angulo=0.0)
    saco = Localizador(config_teste()).localizar(inteiro)
    assert not cortado_pela_borda(saco, inteiro.shape)


def test_verificar_rugas_ensinar_saco_maior_do_que_o_campo(app, janela, tmp_path):
    """Saco maior do que o campo: o M5 vê a imagem toda e o «É OK: ensinar» ensina-a; outra foto do mesmo
    produto noutra posição (deslocada e rodada) desconta a impressão na parte que tem em comum com ela."""
    from interface.janela_rugas import ensinavel
    from nucleo.imagens import ler_imagem
    from testes.test_m5_relevo import foto_do_campo, impresso_grande
    rng = np.random.default_rng(27)
    lona = impresso_grande(rng)
    caminho = tmp_path / "campo.png"
    guardar_imagem(caminho, foto_do_campo(lona, (160, 115), 0.0, rng))
    janela._abrir_janela_rugas()
    rugas = janela.janela_rugas
    janela._verificar_rugas_em(str(caminho))
    _esperar_verificacao(app, janela)
    assert "imagem toda" in rugas.lbl_info.text(), rugas.lbl_info.text()
    assert rugas.lbl_veredito.text().startswith("RUGA DETETADA"), "os traços claros impressos passam por ruga"
    assert ensinavel(rugas.visto.resultado) and rugas.btn_ensinar.isEnabled(), "a imagem toda ensina-se"

    rugas.btn_ensinar.click()
    _esperar_verificacao(app, janela)
    assert rugas.lbl_veredito.text() == "SEM RUGAS", rugas.lbl_info.text()
    [recorte] = [f for f in (janela._dataset / "relevo_referencia").glob("*.png") if not f.stem.endswith("_util")]
    assert ler_imagem(recorte).shape[:2] == (680, 900), "foi a imagem toda"

    outra = tmp_path / "outra posição.png"
    guardar_imagem(outra, foto_do_campo(lona, (145, 105), 30.0, rng))
    janela._verificar_rugas_em(str(outra))
    _esperar_verificacao(app, janela)
    assert "Impressão descontada com 1 saco(s) OK" in rugas.lbl_info.text(), rugas.lbl_info.text()


@pytest.mark.parametrize("angulo", [0, 30, 90, -60])
def test_etapas_com_o_saco_ao_alto(angulo):
    """Venha o saco como vier, nas etapas dos módulos fica na vertical; a original e a anotada não mudam."""
    from interface.ciclo import Visto, preparar
    from mestre import Mestre
    cfg = config_teste()
    img = cena_forma(np.random.default_rng(50), FORMAS["certo"], angulo=angulo)
    r = Mestre(cfg, apenas=["dimensoes"]).inspecionar(img, guardar_intermedios=True)
    etapas = dict(preparar(Visto(1, r, cfg)).etapas)
    assert etapas["original"].shape == img.shape and etapas["anotada"].shape[:2] == img.shape[:2]
    for nome in ("localizacao_segmentacao", "dimensoes_nominal", "dimensoes_forma"):
        altura, largura = etapas[nome].shape[:2]
        assert altura > largura, (nome, etapas[nome].shape)
    mascara = etapas["localizacao_segmentacao"]
    mascara = mascara if mascara.ndim == 2 else mascara[..., 0]
    ys, xs = np.nonzero(mascara > 127)
    assert (ys.max() - ys.min()) / (xs.max() - xs.min()) == pytest.approx(COMPRIMENTO / LARGURA, rel=0.1),         "a silhueta já no referencial do saco, com o comprimento na vertical"


@pytest.mark.parametrize("angulo", [10, 190, -80, 100])
@pytest.mark.parametrize("distancia", [20.0, COMPRIMENTO - 20.0])
def test_etapas_com_o_saco_direito_pelo_easy_open(angulo, distancia):
    """Com easy open, a ponta dos entalhes fica sempre em cima nas etapas, venha o saco como vier."""
    from interface.ciclo import Visto, ponta_do_easy_open, preparar
    from mestre import Mestre
    from modulos.m3_dimensoes.easy_open import COR_ENTALHE
    from testes.test_m3_dimensoes import _com_entalhes
    cfg = config_teste(dimensoes={"easy_open": True})
    img = cena_forma(np.random.default_rng(51), _com_entalhes(distancia), angulo=angulo, cor=(30, 30, 30))  # selado escuro
    r = Mestre(cfg, apenas=["dimensoes"]).inspecionar(img, guardar_intermedios=True)
    assert r.relatorio("dimensoes").medidas["easy_open_entalhes"] == 2 and ponta_do_easy_open(r) is not None
    forma = dict(preparar(Visto(1, r, cfg)).etapas)["dimensoes_forma"]
    ys, _ = np.nonzero(np.all(forma == COR_ENTALHE, axis=2))
    assert len(ys) and ys.mean() < forma.shape[0] * 0.3, "os entalhes em cima"


def test_verificar_rugas_so_numa_zona(app, janela, tmp_path):
    """Zona: arrastar um retângulo na imagem e o M5 procura só aí, na mesma imagem."""
    from interface.janela_rugas import verificar_zona
    from testes.test_m5_relevo import cena
    # Saco de 150 × 90 mm a direito, centrado (canto em (250, 275) px); vinco de (20, 10) a (110, 80) mm
    img = cena(np.random.default_rng(26), angulo=0.0, vincos=[(20, 10, 110, 80, 0.15)])
    cfg = config_teste()
    com = verificar_zona(cfg, img, (330, 305, 490, 390))
    assert com.estado is Estado.NOK and len(com.relatorio("relevo").defeitos) == 1
    assert "zona escolhida" in com.relatorios[0].mensagem
    sem = verificar_zona(cfg, img, (850, 300, 140, 400))  # à direita do vinco: só saco liso
    assert sem.estado is Estado.OK, sem.relatorio("relevo").defeitos
    with pytest.raises(ValueError, match="pequena"):
        verificar_zona(cfg, img, (400, 400, 10, 10))

    caminho = tmp_path / "saco.png"
    guardar_imagem(caminho, img)
    janela._abrir_janela_rugas()
    rugas = janela.janela_rugas
    assert not rugas.btn_zona.isEnabled(), "sem verificação, não há imagem para escolher a zona"
    janela._verificar_rugas_em(str(caminho))
    _esperar_verificacao(app, janela)
    rugas.btn_zona.click()
    assert rugas.imagem.desenhar_roi
    rugas.imagem.roi_desenhada.emit(850, 300, 140, 400)
    assert rugas.lbl_veredito.text() == "SEM RUGAS" and "zona escolhida" in rugas.lbl_info.text()
    rugas.imagem.roi_desenhada.emit(330, 305, 490, 390)  # outra zona, na mesma imagem
    assert rugas.lbl_veredito.text().startswith("RUGA DETETADA"), rugas.lbl_info.text()
    assert not rugas.btn_ensinar.isEnabled(), "uma zona não se ensina como saco OK"
    assert rugas.btn_rugas.isEnabled(), "mas a imagem pode ir para o dataset"
