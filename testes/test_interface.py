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
from interface.editor import SECOES_CAMARA, SECOES_DEFINICOES, EditorParametros, ajudas  # noqa: E402
from interface.estilo import ICONE, LOGO, icone  # noqa: E402
from interface.janela import Janela  # noqa: E402
from interface.login import JanelaLogin, assinatura, pode, verificar  # noqa: E402
from interface.ordens import DialogoEspecificacao, DialogoOrdem  # noqa: E402
from interface.paineis import VistaImagem  # noqa: E402
from interface import vista_dashboard  # noqa: E402
from interface.registos import guardar_correcao, pasta_dataset  # noqa: E402
from nucleo.config import RAIZ, Config, nome_define  # noqa: E402
from nucleo.contrato import Captura, Estado, Resultado  # noqa: E402
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
    feito = guardar_correcao(r, "ok", dataset, registo)
    copias = list((dataset / "ok").glob("*.png"))
    assert len(copias) == 1 and "linha" in copias[0].name and "blackfly/ok/" in feito
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
    feito = guardar_correcao(r, "risco", dataset, tmp_path / "correcoes.csv")
    assert "já está no dataset" in feito and list(dataset.rglob("*.png")) == [original]
    assert ler_csv(tmp_path / "correcoes.csv")[0]["imagem_dataset"] == ""


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
    assert len(list((tmp_path / "dataset" / "linha" / "dimensao").glob("*comprido_158.png"))) == 1


def test_parar_durante_uma_revisao(app, janela):
    janela.iniciar()
    esperar(app, lambda: janela._em_revisao is not None)
    assert "EM REVISÃO" in janela.indicador.text()
    janela.parar()
    esperar(app, lambda: janela.ciclo is None and janela._em_revisao is None)


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


def test_criar_produto_e_of_no_formulario_e_no_dialogo(app, janela):
    repo = janela._repositorio()
    form = DialogoEspecificacao(janela, janela, "novo", "admin", of=OrdemFabrico("OF9", "Novo"), nome="Novo")
    assert form.medidas["comprimento_nominal_mm"].value() == 150.0, "começa nas medidas em uso"
    form.medidas["tolerancia_comprimento_mm"].setValue(9.0)
    form.ed_cor.setText("azul")
    form.verniz.setChecked(True)
    form._guardar()
    assert form.result() == QDialog.DialogCode.Accepted, form.erro.text()
    produto = repo.procurar_produto("novo")
    assert (produto.tolerancia_comprimento_mm, produto.cor, produto.verniz) == (9.0, "azul", True)
    assert repo.procurar_of("OF9").produto == "Novo"

    form = DialogoEspecificacao(janela, janela, "of", "Ana (nº 7)", produto=produto, of=repo.procurar_of("OF9"))
    assert form.ed_cor.text() == "azul"
    form.medidas["tolerancia_largura_mm"].setValue(4.0)
    form._guardar()
    of = repo.procurar_of("OF9")
    assert of.ajustes == {"tolerancia_largura_mm": 4.0} and of.autor == "Ana (nº 7)"

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
