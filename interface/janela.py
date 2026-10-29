"""Interface, janela: a barra de comandos, as duas vistas (Operador e Engenharia) e as janelas à parte.

    [Ao vivo|Produção] [▶ Iniciar][■ Parar] [◀][▶][Congelar]  PRODUÇÃO · A CORRER  [Operador|Engenharia] [Histórico] [📷][⚙] [👤 admin ▾]

    Engenharia                                         Operador
    ┌───────────────────────────┬──────────────┐       ┌──────────────────────────────┐
    │                           │ NOK  saco 12 │       │        NOK   saco 12         │
    │  imagem anotada           │ tempos       │       │                              │
    │  (ou a etapa escolhida)   │ módulos      │       │        imagem anotada        │
    │                           │ defeitos     │       │                              │
    │                           │ [Dataset ▾]  │       │ [OK][NOK][DESCARTADOS]  ms   │
    ├───────────────────────────┴──────────────┤       └──────────────────────────────┘
    │ Etapas | Log                             │
    └──────────────────────────────────────────┘

    Janelas à parte:  Revisão     um saco não OK: descartar ou NOK (a produção espera)
                      Histórico   os últimos sacos e os contadores
                      📷 Câmara    exposição, ganho, trigger (disparo), lente
                      ⚙ Definições perfil, fonte, parâmetros de inspeção e saída
                      👤 conta     quem está a usar, mudar de utilizador, contas e permissões
    Atalhos: F5 inicia, Shift+F5 para. O operador só vê a vista Operador.

Parâmetros: ao vivo entram logo; em produção, cada alteração é pré-visualizada
no saco que está no ecrã (que fica congelado) e só passa aos sacos seguintes
com "Aplicar à produção". "Guardar no perfil" escreve-os no perfil escolhido.
O perfil, a fonte e a pasta ficam lembrados (QSettings) para a sessão seguinte.
"""

from __future__ import annotations

import argparse
import html
import logging
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QStyle, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from interface.ciclo import Ciclo, Pedido, Previsao, Visto, preparar
from interface.estilo import AZUL, CORES_ESTADO, NOME_APP, icone
from interface.janela_conta import Conta
from interface.janela_parametros import Parametros
from interface.janelas import JanelaHistorico, JanelaRevisao
from interface.paineis import (
    Contadores, Etapas, Faixa, PainelLog, RegistoQt, TabelaDefeitos, TabelaModulos, VistaImagem,
)
from interface.registos import registar_revisao
from nucleo.config import CAMINHO_POR_OMISSAO, Config
from nucleo.contrato import CLASSES_DATASET, Estado


log = logging.getLogger("interface")

SEM_IMAGEM = "Carrega em ▶ Iniciar"
# Cor do indicador do estado (ao centro da barra): parado, ao vivo a correr, produção a correr.
CORES_INDICADOR = {None: "#78909c", "ao_vivo": AZUL, "producao": "#2e7d32"}


class Janela(Conta, Parametros, QMainWindow):
    def __init__(self, args: argparse.Namespace, utilizador: str = "admin", memoria: QSettings | None = None) -> None:
        """args: os de gui.py; memoria: onde lembrar o perfil, a fonte e a pasta (None = não lembrar)."""
        super().__init__()
        self.setWindowTitle(f"{NOME_APP}: inspeção de sacos")
        self.args = args
        self._memoria = memoria
        self.utilizador, self.papel = utilizador, "admin"
        self.ciclo: Ciclo | None = None
        self._previsao: Previsao | None = None
        self._previsao_pendente: tuple[Visto, Config] | None = None
        lembrado = self._lembrado("perfil")
        self._perfil = Path(args.config or (lembrado if lembrado and Path(lembrado).exists() else CAMINHO_POR_OMISSAO))
        self._cfg_perfil = Config()  # como está no ficheiro do perfil
        self._cfg_edicao = Config()  # como está nos editores (definições e câmara): é a que se usa
        self._dataset = args.dataset
        self._ultimo: Visto | None = None  # o último que chegou do ciclo
        self._base: Visto | None = None  # o que está no ecrã (sem contar com a pré-visualização)
        self._pendente: Visto | None = None  # à espera de ser desenhado
        self._contagem: Counter[str] = Counter()  # OK, NOK e DESCARTADOS (depois da revisão)
        self._em_revisao: JanelaRevisao | None = None  # o saco à espera de decisão
        self._tempos: list[float] = []
        self._reiniciar = False

        self.janela_historico = JanelaHistorico(self, self._icone_qt("SP_FileDialogDetailedView"))
        self.historico = self.janela_historico.tabela
        self.historico.escolhido.connect(self._escolher_do_historico)
        self.janela_definicoes = self._criar_definicoes()
        self.janela_camara = self._criar_camara()
        self.vistas = QStackedWidget()
        self.vistas.addWidget(self._criar_operador())
        self.vistas.addWidget(self._criar_engenharia())
        centro = QWidget()
        centro.setObjectName("fundo")
        layout = QVBoxLayout(centro)
        layout.addWidget(self._criar_barra())
        layout.addWidget(self.vistas, 1)
        self.setCentralWidget(centro)
        self.lbl_resumo = QLabel()  # perfil, fonte e disparo em uso, sempre à vista
        self.statusBar().addPermanentWidget(self.lbl_resumo)
        for tecla, funcao in (("F5", self.iniciar), ("Shift+F5", self.parar)):
            QShortcut(QKeySequence(tecla), self).activated.connect(funcao)

        self._registo = RegistoQt()
        self._registo.ponte.texto.connect(self.log.appendPlainText)
        logging.getLogger().addHandler(self._registo)

        self._carregar_perfil()
        (self.btn_producao if args.modo == "producao" else self.btn_ao_vivo).setChecked(True)
        (self.btn_operador if args.vista == "operador" else self.btn_engenharia).setChecked(True)
        self._definir_utilizador(utilizador)

    # --- Construção -------------------------------------------------------------------
    def _icone_qt(self, nome: str) -> QIcon:
        """Um ícone do próprio Qt (QStyle.StandardPixmap), p.ex. "SP_MediaPlay"."""
        return self.style().standardIcon(getattr(QStyle.StandardPixmap, nome))

    def _botao(self, texto: str, alternavel: bool = False, icone_botao: QIcon | None = None,
               dica: str = "") -> QPushButton:
        botao = QPushButton(texto)
        botao.setCheckable(alternavel)
        if icone_botao is not None:
            botao.setIcon(icone_botao)
            botao.setIconSize(QSize(22, 22))
        botao.setToolTip(dica)
        return botao

    def _botao_icone(self, icone_botao: QIcon, dica: str) -> QPushButton:
        """Botão só com um ícone grande (a dica diz o que faz)."""
        botao = QPushButton()
        botao.setIcon(icone_botao)
        botao.setIconSize(QSize(28, 28))
        botao.setFixedWidth(54)
        botao.setToolTip(dica)
        return botao

    def _criar_barra(self) -> QFrame:
        self.btn_ao_vivo = self._botao("Ao vivo", True, dica="Câmara em contínuo, sem trigger: centrar o saco, "
                                                             "acertar a luz e o foco, afinar. Não regista nada.")
        self.btn_producao = self._botao("Produção", True, dica="Com o trigger (disparo): uma decisão por saco, "
                                                               "registada em resultados/.")
        self.btn_operador = self._botao("Operador", True, dica="O estado de cada saco em grande.")
        self.btn_engenharia = self._botao("Engenharia", True, dica="Etapas, módulos, defeitos e log.")
        self._grupo(self.btn_ao_vivo, self.btn_producao).idClicked.connect(self._mudar_modo)
        self._grupo(self.btn_operador, self.btn_engenharia).idClicked.connect(self._mudar_vista)

        self.btn_iniciar = self._botao("Iniciar", icone_botao=self._icone_qt("SP_MediaPlay"),
                                       dica="Começar a inspeção (F5)")
        self.btn_parar = self._botao("Parar", icone_botao=self._icone_qt("SP_MediaStop"),
                                     dica="Parar a inspeção (Shift+F5)")
        self.btn_iniciar.setObjectName("iniciar")  # verde (ESTILO)
        self.btn_parar.setObjectName("parar")  # vermelho
        self.btn_iniciar.clicked.connect(self.iniciar)
        self.btn_parar.clicked.connect(self.parar)
        self.btn_anterior = self._botao("", icone_botao=self._icone_qt("SP_MediaSkipBackward"),
                                        dica="Ao vivo com uma pasta: imagem anterior")
        self.btn_seguinte = self._botao("", icone_botao=self._icone_qt("SP_MediaSkipForward"),
                                        dica="Ao vivo com uma pasta: imagem seguinte")
        self.btn_anterior.clicked.connect(lambda: self.ciclo and self.ciclo.navegar(-1))
        self.btn_seguinte.clicked.connect(lambda: self.ciclo and self.ciclo.navegar(+1))
        self.btn_congelar = self._botao_icone(self._icone_qt("SP_MediaPause"),
                                              "Congelar. Ao vivo: ficar nesta imagem para afinar nela.\n"
                                              "Produção: o ecrã fica neste saco (a linha continua e é registada).")
        self.btn_congelar.setCheckable(True)
        self.btn_congelar.toggled.connect(self._congelar)
        self.indicador = QLabel()  # p.ex. "PRODUÇÃO · A CORRER", com a cor do modo
        self.indicador.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.indicador.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.btn_historico = self._botao_icone(self._icone_qt("SP_FileDialogDetailedView"),
                                               "Histórico: os últimos sacos e os contadores")
        self.btn_historico.clicked.connect(self.janela_historico.abrir)
        self.btn_camara = self._botao_icone(icone("camara"), "Câmara: exposição, ganho, trigger (disparo) e lente")
        self.btn_camara.clicked.connect(self.janela_camara.abrir)
        self.btn_definicoes = self._botao_icone(icone("definicoes"),
                                                "Definições: perfil, fonte, parâmetros de inspeção e saída")
        self.btn_definicoes.clicked.connect(self.janela_definicoes.abrir)
        self.btn_conta = QToolButton()  # o menu é feito em _definir_utilizador
        self.btn_conta.setIcon(icone("conta"))
        self.btn_conta.setIconSize(QSize(26, 26))
        self.btn_conta.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_conta.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_conta.setToolTip("A conta: mudar de utilizador, contas e permissões")

        # Modo e vista: dois botões colados, como um interruptor.
        for esquerdo, direito in ((self.btn_ao_vivo, self.btn_producao), (self.btn_operador, self.btn_engenharia)):
            esquerdo.setObjectName("segmento_esq")
            direito.setObjectName("segmento_dir")

        moldura = QFrame()
        moldura.setObjectName("barra")  # cartão branco (ESTILO)
        barra = QHBoxLayout(moldura)
        barra.setContentsMargins(8, 6, 8, 6)
        barra.setSpacing(6)
        for item in (_par(self.btn_ao_vivo, self.btn_producao), 20, self.btn_iniciar, self.btn_parar, 20,
                     self.btn_anterior, self.btn_seguinte, self.btn_congelar, 20):
            _juntar(barra, item)
        barra.addWidget(self.indicador, 1)
        for item in (20, _par(self.btn_operador, self.btn_engenharia), 20, self.btn_historico, self.btn_camara,
                     self.btn_definicoes, 20, self.btn_conta):
            _juntar(barra, item)
        return moldura

    def _grupo(self, *botoes: QPushButton) -> QButtonGroup:
        grupo = QButtonGroup(self)
        for i, botao in enumerate(botoes):
            grupo.addButton(botao, i)
        return grupo

    def _criar_operador(self) -> QWidget:
        self.faixa_op = Faixa(40)
        self.imagem_op = VistaImagem(SEM_IMAGEM)
        self.contadores_op = Contadores(26)
        vista = QWidget()
        layout = QVBoxLayout(vista)
        layout.addWidget(self.faixa_op)
        layout.addWidget(self.imagem_op, 1)
        layout.addWidget(self.contadores_op)
        return vista

    def _criar_engenharia(self) -> QWidget:
        self.titulo_imagem = QLabel()
        self.titulo_imagem.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.imagem_eng = VistaImagem(SEM_IMAGEM)
        imagem = QWidget()
        coluna = QVBoxLayout(imagem)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.addWidget(self.titulo_imagem)
        coluna.addWidget(self.imagem_eng, 1)

        self.faixa_eng = Faixa(20)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.modulos = TabelaModulos()
        self.defeitos = TabelaDefeitos()
        self.btn_corrigir_eng = self._botao_corrigir("Guardar no dataset ▾")
        # Todas as divisórias (QSplitter) se arrastam; os tamanhos ficam lembrados (closeEvent).
        tabelas = QSplitter(Qt.Orientation.Vertical)
        for titulo, tabela in (("Módulos", self.modulos), ("Defeitos", self.defeitos)):
            bloco = QWidget()
            coluna = QVBoxLayout(bloco)
            coluna.setContentsMargins(0, 0, 0, 0)
            cabecalho = QLabel(titulo.upper())
            cabecalho.setObjectName("cabecalho")  # pequeno e cinzento (ESTILO)
            coluna.addWidget(cabecalho)
            coluna.addWidget(tabela, 1)
            tabelas.addWidget(bloco)
        lateral = QWidget()
        coluna = QVBoxLayout(lateral)
        coluna.setContentsMargins(0, 0, 0, 0)
        for w, esticar in ((self.faixa_eng, 0), (self.info, 0), (tabelas, 1), (self.btn_corrigir_eng, 0)):
            coluna.addWidget(w, esticar)

        topo = QSplitter(Qt.Orientation.Horizontal)
        topo.addWidget(imagem)
        topo.addWidget(lateral)
        topo.setSizes([650, 350])  # proporções iniciais

        self.etapas = Etapas()
        self.etapas.escolhida.connect(self._mostrar_etapa)
        self.log = PainelLog()
        self.separadores = QTabWidget()
        for w, nome in ((self.etapas, "Etapas"), (self.log, "Log")):
            self.separadores.addTab(w, nome)

        vista = QSplitter(Qt.Orientation.Vertical)
        vista.addWidget(topo)
        vista.addWidget(self.separadores)
        vista.setSizes([680, 320])
        self._divisorias = {"divisoria_tabelas": tabelas, "divisoria_topo": topo, "divisoria_vista": vista}
        for chave, divisoria in self._divisorias.items():
            if (estado := self._lembrado(chave)) is not None:
                divisoria.restoreState(estado)
        return vista

    def _botao_corrigir(self, texto: str) -> QToolButton:
        botao = QToolButton()
        botao.setText(texto)
        botao.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(botao)
        for classe in CLASSES_DATASET:
            menu.addAction(classe, lambda c=classe: self._corrigir(c))
        botao.setMenu(menu)
        return botao

    # --- Modo e vista -----------------------------------------------------------------
    def _modo(self) -> str:
        return "producao" if self.btn_producao.isChecked() else "ao_vivo"

    def _mudar_modo(self) -> None:
        self.btn_congelar.setChecked(False)
        if self.ciclo is not None and self.ciclo.pedido.modo != self._modo():
            self._reiniciar = True  # recomeça no outro modo quando este parar
            self.ciclo.parar()
        self._atualizar_botoes()

    def _ver_ao_vivo(self) -> None:
        """Da janela da câmara: passa a Ao vivo e começa (ou recomeça, se estava em produção)."""
        self.btn_ao_vivo.setChecked(True)
        self._mudar_modo()
        if self.ciclo is None:
            self.iniciar()

    def _engenharia(self) -> bool:
        return self.btn_engenharia.isChecked()

    def _mudar_vista(self) -> None:
        self.vistas.setCurrentIndex(1 if self._engenharia() else 0)
        if self.ciclo is not None:
            self.ciclo.etapas = self._engenharia()  # as etapas gastam tempo: só quando se veem

    def _atualizar_botoes(self) -> None:
        a_correr = self.ciclo is not None
        tipo, producao = self.cb_fonte.currentData(), self._modo() == "producao"
        for w in (self.cb_perfil, self.cb_fonte):
            w.setEnabled(not a_correr)
        for w in (self.ed_caminho, self.btn_procurar):
            w.setEnabled(not a_correr and tipo in ("pasta", "video"))
        self.btn_iniciar.setEnabled(not a_correr)
        self.btn_parar.setEnabled(a_correr)
        for w in (self.btn_anterior, self.btn_seguinte):
            w.setVisible(self.papel == "admin" and not producao and tipo == "pasta")
            w.setEnabled(a_correr)
        for editor in (self.editor, self.editor_camara):
            editor.btn_aplicar.setEnabled(a_correr and self.ciclo.pedido.modo == "producao")
        modo = "PRODUÇÃO" if producao else "AO VIVO"
        cor = CORES_INDICADOR[self.ciclo.pedido.modo if a_correr else None]
        estado = "A CORRER" if a_correr else "PARADO"
        if self._em_revisao is not None:
            cor, estado = CORES_ESTADO[Estado.ERRO], "EM REVISÃO"
        self.indicador.setText(f"{modo} · {estado}")
        self.indicador.setStyleSheet(f"background: {cor}; color: white; font-size: 14pt; font-weight: bold; "
                                     "border-radius: 6px; padding: 4px 12px;")
        self._atualizar_resumo()

    def _atualizar_resumo(self) -> None:
        """Na barra de estado: o perfil, a fonte e o disparo que vão ser usados."""
        if not hasattr(self, "lbl_resumo"):  # ainda a construir a janela
            return
        fonte, caminho = self.cb_fonte.currentText(), self.ed_caminho.text().strip()
        if caminho and self.cb_fonte.currentData() in ("pasta", "video"):
            fonte += f" ({Path(caminho).name})"
        self.lbl_resumo.setText(f"Perfil: {self._perfil.stem}   ·   Fonte: {fonte}   ·   "
                                f"Disparo: {self._cfg_edicao.disparo.modo}")

    # --- Arrancar e parar --------------------------------------------------------------
    def iniciar(self) -> None:
        if self.ciclo is not None:
            return
        tipo, texto = self.cb_fonte.currentData(), self.ed_caminho.text().strip()
        caminho = Path(texto) if texto else None
        if (tipo == "pasta" and (caminho is None or not caminho.is_dir())) or \
                (tipo == "video" and (caminho is None or not caminho.is_file())):
            falta = "a pasta com as imagens" if tipo == "pasta" else "o ficheiro de vídeo"
            onde = "em ⚙ Definições" if self.papel == "admin" else "(pede ao administrador: ⚙ Definições)"
            QMessageBox.warning(self, "Fonte", f"Falta escolher {falta} {onde}.")
            if self.papel == "admin":
                self.janela_definicoes.abrir()
            return
        modo = self._modo()
        if modo == "producao":
            self._contagem.clear()
            self._tempos.clear()
            self.historico.limpar()
            self._atualizar_contadores()
        self._lembrar()
        self.ciclo = Ciclo(Pedido(modo, tipo, caminho, self._cfg_edicao, Path(self.args.saida)))
        self.ciclo.etapas = self._engenharia()
        self.ciclo.congelado = self.btn_congelar.isChecked()
        self.ciclo.registado.connect(self._registado)
        self.ciclo.visto.connect(self._recebido)
        self.ciclo.mensagem.connect(self._mensagem)
        self.ciclo.finished.connect(self._terminou)
        self.ciclo.start()
        log.info("%s com %s %s (perfil %s, %s)", modo, tipo, caminho or "", self._perfil.name, self.utilizador)
        self._atualizar_botoes()

    def parar(self) -> None:
        if self.ciclo is not None:
            self.ciclo.parar()

    def _terminou(self) -> None:
        ciclo, self.ciclo = self.ciclo, None
        if ciclo is not None:
            ciclo.deleteLater()
        if self._em_revisao is not None:  # parou a meio de uma revisão: o saco fica por decidir
            self._em_revisao.deleteLater()
            self._em_revisao = None
        self._atualizar_botoes()
        if self._reiniciar:
            self._reiniciar = False
            self.iniciar()

    def closeEvent(self, evento: QCloseEvent) -> None:
        self._reiniciar = False
        if self.ciclo is not None:
            self.ciclo.parar()
            self.ciclo.wait(5000)
        if self._previsao is not None:
            self._previsao.wait(5000)
        for janela in (self.janela_historico, self.janela_definicoes, self.janela_camara):
            janela.close()
        if self._memoria is not None:  # os tamanhos que o utilizador escolheu
            for chave, divisoria in self._divisorias.items():
                self._memoria.setValue(chave, divisoria.saveState())
        logging.getLogger().removeHandler(self._registo)
        super().closeEvent(evento)

    # --- Mostrar -----------------------------------------------------------------------
    def _registado(self, visto: Visto) -> None:
        """Cada saco decidido em Produção. Um OK conta já; os outros vão à revisão (a inspeção espera)."""
        self._tempos.append(visto.ciclo_ms)
        self.historico.acrescentar(visto)
        if visto.resultado.estado is Estado.OK:
            self._contagem["OK"] += 1
            self._atualizar_contadores()
            return
        self.historico.marcar_decisao(visto.numero, "em revisão")
        self._em_revisao = JanelaRevisao(self, visto if visto.preparado else preparar(visto))
        self._em_revisao.decidido.connect(lambda decisao, v=visto: self._decidir(v, decisao))
        self._em_revisao.open()
        self._atualizar_botoes()

    def _decidir(self, visto: Visto, decisao: str) -> None:
        """A decisão da revisão: "NOK" conta como NOK; "descartado" não conta. A inspeção continua."""
        self._contagem["NOK" if decisao == "NOK" else "DESCARTADOS"] += 1
        self.historico.marcar_decisao(visto.numero, decisao)
        if self.ciclo is not None and self.ciclo.pasta_resultados is not None:
            registar_revisao(visto.resultado, visto.numero, decisao, self.ciclo.pasta_resultados)
        self._em_revisao = None
        self._atualizar_contadores()
        self._atualizar_botoes()
        if self.ciclo is not None:
            self.ciclo.prosseguir()

    def _recebido(self, visto: Visto) -> None:
        """O saco mais recente, pronto a mostrar."""
        self._ultimo = visto
        if visto.modo == "producao" and self.btn_congelar.isChecked() and self._engenharia():
            return  # o ecrã fica no saco escolhido; a linha continua
        # Só se desenha o mais recente: se a janela se atrasar, os intermédios são saltados.
        if self._pendente is None:
            QTimer.singleShot(0, self._desenhar_pendente)
        self._pendente = visto

    def _desenhar_pendente(self) -> None:
        visto, self._pendente = self._pendente, None
        if visto is not None:
            self._mostrar(visto)

    def _mostrar(self, visto: Visto, original: bool = True) -> None:
        if original:
            self._base = visto
        r = visto.resultado
        if visto.modo == "producao":
            texto = f"saco {visto.numero}"
        elif visto.desvio_mm is not None:
            texto = f"a {_norma(visto.desvio_mm):.1f} mm do centro"
        else:
            texto = "ao vivo"
        for faixa in (self.faixa_op, self.faixa_eng):
            faixa.mostrar(r.estado, texto)
        self.imagem_op.mostrar(visto.anotada)
        self.imagem_eng.mostrar(visto.anotada)
        self.titulo_imagem.setText(
            f"<b style='color:{AZUL}'>PRÉ-VISUALIZAÇÃO (não aplicada)</b>" if visto.previsao else "")
        self.info.setText(_info(visto))
        self.modulos.mostrar(r)
        self.defeitos.mostrar(r)
        self.etapas.mostrar(visto)

    def _mostrar_etapa(self, nome: str, imagem) -> None:
        self.imagem_eng.mostrar(imagem)
        self.titulo_imagem.setText(f"etapa: <b>{html.escape(nome)}</b>")

    def _escolher_do_historico(self, visto: Visto | None) -> None:
        if visto is None:
            self._mensagem("As imagens desse saco já saíram da memória (ver a pasta de resultados).")
            return
        self.btn_engenharia.setChecked(True)  # o detalhe do saco está na Engenharia
        self._mudar_vista()
        self.btn_congelar.setChecked(True)
        self._mostrar(visto if visto.preparado else preparar(visto))

    def _congelar(self, sim: bool) -> None:
        if self.ciclo is not None:
            self.ciclo.congelar(sim)
        if not sim and self._ultimo is not None:
            self._mostrar(self._ultimo)

    def _atualizar_contadores(self) -> None:
        orcamento = self._cfg_edicao.geral.orcamento_ms
        nao_analisadas = self.ciclo.descartadas if self.ciclo is not None else 0
        for contadores in (self.contadores_op, self.janela_historico.contadores):
            contadores.mostrar(self._contagem, self._tempos, orcamento, nao_analisadas)

    def _mensagem(self, texto: str) -> None:
        self.statusBar().showMessage(texto, 15000)
        log.info("%s", texto)


# --- Auxiliares ---------------------------------------------------------------------------

def _juntar(linha: QHBoxLayout, item: QWidget | QHBoxLayout | int) -> None:
    """Um widget, um grupo de widgets, ou um espaço em px."""
    if isinstance(item, int):
        linha.addSpacing(item)
    elif isinstance(item, QHBoxLayout):
        linha.addLayout(item)
    else:
        linha.addWidget(item)


def _par(esquerdo: QWidget, direito: QWidget) -> QHBoxLayout:
    """Dois botões colados (um interruptor)."""
    par = QHBoxLayout()
    par.setSpacing(0)
    par.addWidget(esquerdo)
    par.addWidget(direito)
    return par


def _norma(v: tuple[float, float]) -> float:
    return (v[0] ** 2 + v[1] ** 2) ** 0.5


def _info(visto: Visto) -> str:
    """O resumo do saco no painel lateral (HTML)."""
    r, cfg = visto.resultado, visto.cfg
    linhas = []
    if r.captura is not None:
        linhas.append(f"<b>{html.escape(Path(r.captura.origem).name)}</b>")
    # O tempo do saco (inspeção + registo) a vermelho se passar do orçamento.
    ms = r.tempo_ms if visto.modo == "ao_vivo" or visto.previsao else visto.ciclo_ms
    cor = "#c62828" if ms > cfg.geral.orcamento_ms else "inherit"
    tempo = f"<span style='color:{cor}'>{ms:.0f} ms</span>"
    if r.saco is not None:
        tempo += f"   ·   {r.saco.comprimento_mm:.1f} × {r.saco.largura_mm:.1f} mm"
    linhas.append(tempo)
    if r.mensagem:
        linhas.append(f"<span style='color:#ef6c00'>{html.escape(r.mensagem)}</span>")
    return "<br>".join(linhas)
