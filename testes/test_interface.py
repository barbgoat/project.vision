"""Interface gráfica: correções do veredito, editor de parâmetros e a janela a correr, sem ecrã."""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # sem janelas no ecrã; tem de vir antes da QApplication

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import gui  # noqa: E402
from interface.editor import SECOES_CAMARA, SECOES_DEFINICOES, EditorParametros, ajudas  # noqa: E402
from interface.estilo import ICONE, LOGO, icone  # noqa: E402
from interface.janela import Janela  # noqa: E402
from interface.login import JanelaLogin, verificar  # noqa: E402
from interface.paineis import VistaImagem  # noqa: E402
from interface.registos import guardar_correcao, pasta_dataset  # noqa: E402
from nucleo.config import RAIZ, Config, nome_define  # noqa: E402
from nucleo.contrato import Captura, Estado, Resultado  # noqa: E402
from nucleo.imagens import guardar_imagem  # noqa: E402
from testes.apoio import CORES, FORMAS, cena_forma, config_teste, correr, execucao, ler_csv, montagem  # noqa: E402

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
def janela(app, tmp_path):
    """A janela com três fotos (canto_dobrado e comprido_158 NOK, certo OK) e o perfil dos testes."""
    rng = np.random.default_rng(5)
    for nome in ("canto_dobrado", "certo", "comprido_158"):
        guardar_imagem(tmp_path / "fotos" / f"{nome}.png", cena_forma(rng, FORMAS[nome], angulo=-15, cor=CORES["azul"]))
    (tmp_path / "perfil.json").write_text(json.dumps(montagem()), encoding="utf-8")
    args = gui.construir_parser().parse_args([
        "--pasta", str(tmp_path / "fotos"), "--config", str(tmp_path / "perfil.json"),
        "--saida", str(tmp_path / "res"), "--dataset", str(tmp_path / "dataset" / "blackfly"),
    ])
    j = Janela(args)
    yield j
    j.close()  # para o ciclo e espera por ele


def _campo(editor: EditorParametros, secao: str, nome: str):
    return next(c.widget for c in editor._campos if (c.secao, c.nome) == (secao, nome))


def _producao_ate_ao_fim(app, j: Janela, decisoes: tuple[str, ...] = ("NOK", "NOK"), arrancar: bool = True) -> None:
    """Corre a pasta até ao fim, decidindo cada saco em revisão pela ordem de `decisoes`."""
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
    feito = guardar_correcao(r, "ok", dataset, registo)
    copias = list((dataset / "ok").glob("*.png"))
    assert len(copias) == 1 and "linha" in copias[0].name and "blackfly/ok/" in feito
    guardar_correcao(r, "risco", dataset, registo)
    linhas = ler_csv(registo)
    assert [linha["classe_real"] for linha in linhas] == ["ok", "risco"]
    assert linhas[0]["estado_sistema"] == "NOK" and linhas[0]["origem"] == "linha.mp4#7"
    assert "﻿" not in registo.read_text(encoding="utf-8-sig"), "o BOM só no início do ficheiro"


def test_correcao_de_uma_imagem_do_dataset_so_regista(tmp_path):
    dataset = tmp_path / "dataset" / "telemovel"
    original = dataset / "ok" / "saco 1 ç.png"
    guardar_imagem(original, IMG)
    r = Resultado(IMG, Estado.NOK, None, [], 1.0, captura=Captura(IMG, str(original), 1000.0))
    feito = guardar_correcao(r, "risco", dataset, tmp_path / "correcoes.csv")
    assert "já está no dataset" in feito and list(dataset.rglob("*.png")) == [original]
    assert ler_csv(tmp_path / "correcoes.csv")[0]["imagem_dataset"] == ""


def test_passagem_descartada_nao_se_corrige(tmp_path):
    r = Resultado(None, Estado.ERRO, None, [], 1.0)
    with pytest.raises(ValueError, match="descartada"):
        guardar_correcao(r, "ok", tmp_path / "dataset", tmp_path / "correcoes.csv")


def test_dataset_das_correcoes_segue_o_perfil():
    assert pasta_dataset(RAIZ / "configuracoes" / "telemovel.json") == RAIZ / "dataset" / "telemovel"
    assert pasta_dataset(RAIZ / "configuracoes" / "linha.json") == RAIZ / "dataset" / "blackfly"


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


def test_operador_so_ve_a_vista_operador(app, janela):
    janela._definir_utilizador("operador")
    escondidos = (janela.btn_engenharia, janela.btn_ao_vivo, janela.btn_historico, janela.btn_camara,
                  janela.btn_definicoes, janela.btn_congelar)
    assert all(w.isHidden() for w in escondidos) and not janela._engenharia()
    assert janela._modo() == "producao" and not janela.btn_iniciar.isHidden()
    janela._definir_utilizador("admin")
    assert not any(w.isHidden() for w in escondidos)


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
    janela.cb_fonte.setCurrentIndex(janela.cb_fonte.findData("video"))
    assert janela._cfg_edicao.disparo.modo == "virtual", "com um vídeo, o disparo 'foto' não serve"


# --- Editor de parâmetros ------------------------------------------------------------------

def test_editor_so_emite_configuracoes_validas(app):
    editor = EditorParametros()
    cfg = config_teste()
    editor.definir(cfg, perfil=cfg)
    recebidas: list[Config] = []
    editor.mudou.connect(recebidas.append)

    _campo(editor, "dimensoes", "tolerancia_comprimento_mm").setValue(4.5)
    assert recebidas[-1].dimensoes.tolerancia_comprimento_mm == 4.5 and not editor.erro.text()

    baixo, alto = _campo(editor, "relevo", "crista_limiar_baixo"), _campo(editor, "relevo", "crista_limiar_alto")
    baixo.setValue(alto.value() + 0.1)  # inválido: baixo > alto
    assert len(recebidas) == 1 and "crista_limiar_baixo" in editor.erro.text()
    alto.setValue(baixo.value() + 0.1)  # corrigido: as duas mudanças entram juntas
    assert len(recebidas) == 2 and not editor.erro.text()
    assert recebidas[-1].relevo.crista_limiar_baixo == pytest.approx(baixo.value())
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
    assert janela.etapas.count() >= 2 and janela.modulos.rowCount() == 5

    janela._corrigir("dimensao")  # Engenharia: guardar no dataset
    assert len(list((tmp_path / "dataset" / "blackfly" / "dimensao").glob("*comprido_158.png"))) == 1


def test_parar_durante_uma_revisao(app, janela):
    janela.iniciar()
    esperar(app, lambda: janela._em_revisao is not None)
    assert "EM REVISÃO" in janela.indicador.text()
    janela.parar()
    esperar(app, lambda: janela.ciclo is None and janela._em_revisao is None)


def test_ao_vivo_navega_na_pasta_e_aplica_parametros_logo(app, janela, tmp_path):
    janela.btn_ao_vivo.setChecked(True)
    janela.iniciar()
    esperar(app, lambda: janela._base is not None and janela._base.modo == "ao_vivo")
    primeira = janela._base.resultado.captura.origem
    assert janela._base.desvio_mm is not None, "a cruz no centro e o desvio do saco"

    janela.btn_seguinte.click()
    esperar(app, lambda: janela._base.resultado.captura.origem != primeira)
    _campo(janela.editor, "dimensoes", "tolerancia_comprimento_mm").setValue(9.5)  # sem "Aplicar"
    esperar(app, lambda: janela._base.cfg.dimensoes.tolerancia_comprimento_mm == 9.5)

    janela.parar()
    esperar(app, lambda: janela.ciclo is None)
    assert not (tmp_path / "res").exists(), "ao vivo não regista nada"


def test_trocar_de_modo_a_correr(app, janela):
    janela.btn_ao_vivo.setChecked(True)
    janela.iniciar()  # ao vivo nunca acaba sozinho
    esperar(app, lambda: janela._base is not None)
    janela.btn_producao.click()  # recomeça em produção sem se carregar em Parar
    esperar(app, lambda: janela.ciclo is not None and janela.ciclo.pedido.modo == "producao")
    _producao_ate_ao_fim(app, janela, arrancar=False)
    assert janela.historico.rowCount() == 3


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
