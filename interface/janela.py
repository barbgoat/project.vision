"""Interface, janela: a barra de comandos, as duas vistas (Operador e Engenharia) e as janelas à parte.

    [⚙ ▾]              [ OF 1234 · PreVital ]              PARADO  [Ao vivo|Trigger] [▶ Iniciar] [■ Parar]

    Engenharia                                         Operador
    ┌───────────────────────────┬──────────────┐       ┌──────────────────────────────┐
    │                [Congelar] │ NOK  saco 12 │       │        NOK   saco 12         │
    │  imagem anotada           │ tempos       │       │                              │
    │  (ou a etapa escolhida)   │ módulos      │       │        imagem anotada        │
    │                           │ defeitos     │       │                              │
    │                           │ [Dataset ▾]  │       │ [OK][NOK][DESCARTADOS]  ms   │
    ├───────────────────────────┴──────────────┤       └──────────────────────────────┘
    │ Etapas | Log                             │
    └──────────────────────────────────────────┘

    O menu ⚙ tem o resto: definições, câmara, histórico, dashboard, a vista e a conta.
    Janelas à parte:  Revisão     um saco não OK: descartar ou NOK (a produção espera)
                      OF          a ordem de fabrico e o produto em uso (interface/ordens.py)
                      Dashboard   OFs e produtos, contagens e evolução de cada OF
                      Histórico   os últimos sacos e os contadores
                      📷 Câmara    exposição, ganho, trigger (disparo), lente
                      ⚙ Definições perfil, fonte, parâmetros de inspeção e saída
    Atalhos: F5 inicia, Shift+F5 para. O operador só vê a vista Operador e indica a OF a cada início.

Modos (os dois inspecionam e registam cada saco, como o main.py):
    Ao vivo   a câmara capta em contínuo e a barreira virtual separa os sacos
    Trigger   a câmara só capta com o sinal do botão ou do sensor: um saco por sinal

Parâmetros: cada alteração é pré-visualizada no saco que está no ecrã (que fica
congelado) e só passa aos sacos seguintes com "Aplicar à produção". "Guardar no
perfil" escreve-os no perfil escolhido. O perfil fica lembrado (QSettings).
"""

from __future__ import annotations

import argparse
import dataclasses
import html
import logging
from collections import Counter
from pathlib import Path
from time import monotonic

import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QSplitter, QStackedWidget, QStyle, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from interface.ciclo import Afinacao, Ciclo, MedicaoContorno, Pedido, Previsao, Visto, preparar
from interface.estilo import AZUL, CORES_ESTADO, NOME_APP, icone
from interface.janela_conta import Conta
from interface.janela_parametros import Parametros
from interface.janelas import JanelaHistorico, JanelaRevisao
from interface.ordens import Ordens
from interface.paineis import (
    Contadores, Etapas, Faixa, PainelLog, RegistoQt, TabelaDefeitos, TabelaModulos, VistaImagem,
)
from interface.registos import registar_revisao
from modulos.m2_localizacao.medicao import LADOS
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config
from nucleo.contrato import CLASSES_DATASET, Estado
from nucleo.especificacoes import ErroRepositorio, Escolha, Repositorio
from nucleo.imagens import guardar_imagem


log = logging.getLogger("interface")

SEM_IMAGEM = "Carrega em ▶ Iniciar"
# Objetivos indicativos da qualidade do contorno (a afinar, modo Contorno): acima, a vermelho.
CONTORNO_RMS_MAX_PX = 0.5
CONTORNO_REJEITADOS_MAX_PCT = 10.0
SEGURAR_SACO_S = 3.0  # quanto tempo um saco analisado fica no ecrã antes de voltar a imagem ao vivo
# Cor do indicador do estado (à direita da barra): parado, ao vivo a correr, trigger a correr, a afinar.
CORES_INDICADOR = {None: "#78909c", "ao_vivo": AZUL, "trigger": "#2e7d32", "afinar": "#ef6c00"}


class Janela(Conta, Parametros, Ordens, QMainWindow):
    def __init__(self, args: argparse.Namespace, utilizador: str = "admin", memoria: QSettings | None = None) -> None:
        """args: os de gui.py; memoria: onde lembrar o perfil (None = não lembrar)."""
        super().__init__()
        self.setWindowTitle(f"{NOME_APP}: inspeção de sacos")
        self.args = args
        self._memoria = memoria
        self.utilizador, self.papel = utilizador, "admin"
        self.ciclo: Ciclo | None = None
        self.afinacao: Afinacao | None = None  # a câmara ao vivo, sem inspeção (Afinar câmara)
        self._forma_afinacao: tuple[int, int] | None = None  # (altura, largura) da imagem inteira a afinar
        self._medicoes: list[tuple[float, float]] = []  # (comprimento, largura) mm, desde o último Recomeçar
        self._imagem_afinacao: np.ndarray | None = None  # a última imagem inteira a afinar (para o Fundo vazio)
        self._texto_imagem = self._texto_contorno = ""  # o que a afinação mostra: a imagem e o contorno
        self._previsao: Previsao | None = None
        self._previsao_pendente: tuple[Visto, Config] | None = None
        lembrado = self._lembrado("perfil")
        self._perfil = Path(args.config or (lembrado if lembrado and Path(lembrado).exists() else CAMINHO_POR_OMISSAO))
        self._cfg_perfil = Config()  # como está no ficheiro do perfil
        self._cfg_edicao = Config()  # como está nos editores (definições e câmara): é a que se usa
        self._cfg_base = Config()  # o perfil com o produto e a OF por cima ("Repor" volta a esta)
        self._ordem: Escolha | None = None  # a OF e o produto em uso (None = sem OF)
        self._ensaio_sem_of = False  # o admin escolheu "Sem OF (ensaio)": não se volta a perguntar
        self._repo: Repositorio | None = None
        self.janela_dashboard = None  # criada quando se abre
        self._dataset = args.dataset
        self._ultimo: Visto | None = None  # o último que chegou do ciclo
        self._base: Visto | None = None  # o que está no ecrã (sem contar com a pré-visualização)
        self._pendente: Visto | None = None  # à espera de ser desenhado
        self._segurar_ate = 0.0  # até quando o último saco fica no ecrã, sem a imagem ao vivo por cima
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
        layout.addWidget(self._criar_barra_afinacao())
        layout.addWidget(self.vistas, 1)
        self.setCentralWidget(centro)
        self.lbl_resumo = QLabel()  # perfil e modo em uso, sempre à vista
        self.statusBar().addPermanentWidget(self.lbl_resumo)
        for tecla, funcao in (("F5", self.iniciar), ("Shift+F5", self.parar)):
            QShortcut(QKeySequence(tecla), self).activated.connect(funcao)

        self._registo = RegistoQt()
        self._registo.ponte.texto.connect(self.log.appendPlainText)
        logging.getLogger().addHandler(self._registo)

        self._carregar_perfil()
        modo = args.modo or self._cfg_perfil.disparo.modo  # o do perfil, se não for dado
        (self.btn_trigger if modo == "trigger" else self.btn_ao_vivo).setChecked(True)
        (self.acao_operador if args.vista == "operador" else self.acao_engenharia).setChecked(True)
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
        """A barra: o menu (⚙) à esquerda, a OF ao centro, o estado, o modo, Iniciar e Parar à direita."""
        # O menu: tudo o que não é do dia a dia (o que cada papel vê: _definir_utilizador).
        self.btn_menu = QToolButton()
        self.btn_menu.setIcon(icone("definicoes"))
        self.btn_menu.setIconSize(QSize(30, 30))
        self.btn_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_menu.setToolTip("Menu: definições, câmara, histórico, dashboard e conta")
        menu = QMenu(self.btn_menu)
        self.acao_utilizador = menu.addAction(icone("conta"), "")  # quem está a usar (só informação)
        self.acao_utilizador.setEnabled(False)
        menu.addSeparator()
        self.acao_definicoes = _acao(menu, "Definições…", self.janela_definicoes.abrir, icone("definicoes"))
        self.acao_camara = _acao(menu, "Câmara…", self.janela_camara.abrir, icone("camara"))
        self.acao_historico = _acao(menu, "Histórico…", self.janela_historico.abrir,
                                    self._icone_qt("SP_FileDialogDetailedView"))
        self.acao_dashboard = _acao(menu, "Dashboard…", self._abrir_dashboard,
                                    self._icone_qt("SP_FileDialogContentsView"))
        menu.addSeparator()
        vistas = QActionGroup(menu)
        self.acao_operador = _acao(menu, "Vista Operador", self._mudar_vista, grupo=vistas)
        self.acao_engenharia = _acao(menu, "Vista Engenharia", self._mudar_vista, grupo=vistas)
        menu.addSeparator()
        _acao(menu, "Mudar de utilizador…", self._terminar_sessao, icone("conta"))
        self.acao_contas = _acao(menu, "Contas e permissões…", self._mostrar_contas)
        _acao(menu, f"Sair do {NOME_APP}", self.close)
        self.btn_menu.setMenu(menu)

        self.btn_of = self._botao("Sem OF", icone_botao=self._icone_qt("SP_FileIcon"))
        self.btn_of.setObjectName("of")  # largo, ao centro (ESTILO)
        self.btn_of.clicked.connect(self._escolher_ordem)

        self.indicador = QLabel()  # "PARADO", "A AFINAR", "A CORRER" ou "EM REVISÃO", com a cor do modo
        self.indicador.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.indicador.setMinimumWidth(150)
        self.btn_ao_vivo = self._botao("Ao vivo", True, dica="A câmara capta em contínuo e a barreira virtual "
                                                             "separa os sacos: uma decisão por saco, registada.")
        self.btn_trigger = self._botao("Trigger", True, dica="A câmara só capta com o sinal do botão ou do sensor: "
                                                             "uma decisão por sinal, registada.")
        self.btn_ao_vivo.setObjectName("segmento_esq")  # colados, como um interruptor
        self.btn_trigger.setObjectName("segmento_dir")
        self._grupo(self.btn_ao_vivo, self.btn_trigger).idClicked.connect(self._mudar_modo)
        self.btn_afinar = self._botao("Afinar câmara", True, icone("camara"),
                                      "Só a câmara, sem inspeção nem registo, no modo escolhido ao lado: Ao vivo em "
                                      "contínuo (luz, foco, posição); Trigger, uma imagem por sinal do botão ou do "
                                      "sensor. Trocar de modo troca a afinação. A exposição e o ganho (📷 Câmara) "
                                      "mudam logo.")
        self.btn_afinar.clicked.connect(self._alternar_afinacao)
        self.btn_iniciar = self._botao("Iniciar", icone_botao=self._icone_qt("SP_MediaPlay"),
                                       dica="Começar a inspeção (F5)")
        self.btn_parar = self._botao("Parar", icone_botao=self._icone_qt("SP_MediaStop"),
                                     dica="Parar a inspeção (Shift+F5)")
        self.btn_iniciar.setObjectName("iniciar")  # verde (ESTILO)
        self.btn_parar.setObjectName("parar")  # vermelho
        self.btn_iniciar.clicked.connect(self.iniciar)
        self.btn_parar.clicked.connect(self.parar)

        moldura = QFrame()
        moldura.setObjectName("barra")  # cartão branco (ESTILO)
        barra = QHBoxLayout(moldura)
        barra.setContentsMargins(8, 6, 8, 6)
        barra.setSpacing(6)
        # Os lados com o mesmo peso: a OF fica no centro da janela, não só do espaço livre.
        esquerda, direita = QHBoxLayout(), QHBoxLayout()
        esquerda.addWidget(self.btn_menu)
        esquerda.addStretch()
        direita.addStretch()
        for item in (self.indicador, 12, self.btn_afinar, 12, _par(self.btn_ao_vivo, self.btn_trigger), 12,
                     self.btn_iniciar, self.btn_parar):
            _juntar(direita, item)
        barra.addLayout(esquerda, 1)
        barra.addWidget(self.btn_of)
        barra.addLayout(direita, 1)
        return moldura

    def _criar_barra_afinacao(self) -> QFrame:
        """A barra Afinar (só enquanto se afina): o próximo passo e as medidas à esquerda, os botões
        pela ordem em que se usam à direita."""
        self.lbl_passo = QLabel()
        self.lbl_passo.setStyleSheet(f"color: {CORES_INDICADOR['afinar']}; font-weight: bold; font-size: 11pt;")
        self.lbl_resumo_afinacao = QLabel()  # as medidas da imagem ou do contorno, numa linha
        self.lbl_resumo_afinacao.setStyleSheet("color: #546e7a;")
        self.btn_roi = self._botao("Definir ROI", True, dica="Arrastar na imagem o retângulo onde o saco vai "
                                                              "ficar, com fundo à volta: só isso segue para a "
                                                              "inspeção.")
        self.btn_roi.toggled.connect(self._desenhar_roi)
        self.btn_fundo = self._botao("Fundo vazio", dica="SEM saco à vista: guarda esta imagem como o fundo de "
                                                         "referência. O M2 compara cada imagem com ela (repetir "
                                                         "se a luz, o papel ou a câmara mudarem).")
        self.btn_fundo.clicked.connect(self._guardar_fundo)
        self.btn_contorno = self._botao("Contorno", True, dica="Medir o saco na ROI só com o M2 e desenhar o "
                                                               "contorno. Ao vivo, saco parado: o ruído; em "
                                                               "Trigger, reposicionando: a repetibilidade.")
        self.btn_contorno.toggled.connect(self._ligar_contorno)
        self.btn_recomecar = self._botao("Recomeçar", dica="Recomeçar a contagem das medições do contorno.")
        self.btn_recomecar.clicked.connect(self._recomecar_contagem)
        self.btn_escala = self._botao("Calibrar escala", dica="Com um saco de medidas conhecidas (as nominais) e "
                                                              "o Contorno ligado: acerta os píxeis por mm.")
        self.btn_escala.clicked.connect(self._calibrar_escala)
        camara = self._botao("Câmara…", icone_botao=icone("camara"), dica="Exposição, ganho e trigger.")
        camara.clicked.connect(self.janela_camara.abrir)
        guardar = self._botao("Guardar no perfil", dica="Guardar a ROI, o fundo, a escala e a câmara no perfil.")
        guardar.clicked.connect(self._guardar)
        textos = QVBoxLayout()
        textos.setSpacing(0)
        textos.addWidget(self.lbl_passo)
        textos.addWidget(self.lbl_resumo_afinacao)
        self.barra_afinacao = QFrame()
        self.barra_afinacao.setObjectName("barra")  # cartão branco (ESTILO)
        linha = QHBoxLayout(self.barra_afinacao)
        linha.setContentsMargins(12, 4, 8, 4)
        linha.addLayout(textos, 1)
        for botao in (self.btn_roi, self.btn_fundo, self.btn_contorno, self.btn_recomecar, self.btn_escala,
                      camara, guardar):
            linha.addWidget(botao)
        self.barra_afinacao.hide()
        return self.barra_afinacao

    def _atualizar_passo(self) -> None:
        """O próximo passo da afinação, pela ordem: ROI, fundo vazio, contorno, conferir e guardar."""
        if not hasattr(self, "lbl_passo"):  # ainda a construir a janela
            return
        cfg = self._cfg_edicao
        if self.btn_roi.isChecked():
            passo = "Arraste na imagem o retângulo onde o saco vai ficar (com fundo à volta)"
        elif cfg.aquisicao.roi() is None:
            passo = "1 · Definir ROI: o retângulo onde o saco vai ficar"
        elif not cfg.localizacao.fundo_referencia:
            passo = "2 · Tire o saco da frente da câmara e carregue em Fundo vazio"
        elif not self.btn_contorno.isChecked():
            passo = "3 · Ponha o saco dentro da ROI e ligue o Contorno"
        elif not self._medicoes:
            passo = "3 · À espera de medir: o saco tem de estar inteiro dentro da ROI"
        else:
            passo = "4 · Confira as medidas e Guarde no perfil; depois ▶ Iniciar"
        self.lbl_passo.setText(passo)

    def _grupo(self, *botoes: QPushButton) -> QButtonGroup:
        grupo = QButtonGroup(self)
        for i, botao in enumerate(botoes):
            grupo.addButton(botao, i)
        return grupo

    def _criar_operador(self) -> QWidget:
        self.faixa_op = Faixa(40)
        self.imagem_op = VistaImagem(SEM_IMAGEM)
        self.imagem_op.roi_desenhada.connect(self._definir_roi)
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
        self.imagem_eng.roi_desenhada.connect(self._definir_roi)
        # Por cima da imagem: congelar o ecrã.
        self.btn_congelar = self._botao("Congelar", True, self._icone_qt("SP_MediaPause"),
                                        "O ecrã fica neste saco, para afinar nele (a linha continua e é registada).")
        self.btn_congelar.toggled.connect(self._congelar)
        cabeca = QHBoxLayout()
        cabeca.addWidget(self.titulo_imagem, 1)
        cabeca.addWidget(self.btn_congelar)
        imagem = QWidget()
        coluna = QVBoxLayout(imagem)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.addLayout(cabeca)
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
        return "trigger" if self.btn_trigger.isChecked() else "ao_vivo"

    def _mudar_modo(self) -> None:
        self.btn_congelar.setChecked(False)
        if self.ciclo is not None and self.ciclo.pedido.modo != self._modo():
            self._reiniciar = True  # recomeça no outro modo quando este parar
            self.ciclo.parar()
        if self.afinacao is not None and self.afinacao.modo != self._modo():
            self._parar_afinacao()  # a afinar: passa logo ao outro modo
            self.afinacao = None  # já terminou: o finished dela só a apaga
            self.afinar()
        self._atualizar_botoes()

    def _engenharia(self) -> bool:
        return self.acao_engenharia.isChecked()

    def _mudar_vista(self) -> None:
        self.vistas.setCurrentIndex(1 if self._engenharia() else 0)
        if self.ciclo is not None:
            self.ciclo.etapas = self._engenharia()  # as etapas gastam tempo: só quando se veem

    def _atualizar_botoes(self) -> None:
        a_correr = self.ciclo is not None
        a_afinar = self.afinacao is not None
        self.cb_perfil.setEnabled(not a_correr and not a_afinar)
        self.btn_afinar.setEnabled(not a_correr)
        # A barra Afinar só aparece a afinar (a ROI e o contorno desenham-se sobre a imagem inteira);
        # o Congelar só a inspecionar.
        self.barra_afinacao.setVisible(a_afinar)
        self.btn_congelar.setVisible(not a_afinar)
        self._atualizar_passo()
        self.btn_iniciar.setEnabled(not a_correr)
        self.btn_parar.setEnabled(a_correr)
        for editor in (self.editor, self.editor_camara):
            editor.btn_aplicar.setEnabled(a_correr)
        cor = CORES_INDICADOR[self.ciclo.pedido.modo if a_correr else "afinar" if a_afinar else None]
        estado = "A CORRER" if a_correr else "A AFINAR" if a_afinar else "PARADO"
        if self._em_revisao is not None:
            cor, estado = CORES_ESTADO[Estado.ERRO], "EM REVISÃO"
        self.indicador.setText(estado)
        self.indicador.setStyleSheet(f"background: {cor}; color: white; font-size: 14pt; font-weight: bold; "
                                     "border-radius: 6px; padding: 4px 12px;")
        self._atualizar_resumo()

    def _atualizar_resumo(self) -> None:
        """Na barra de estado: o perfil e o modo que vão ser usados."""
        if not hasattr(self, "lbl_resumo"):  # ainda a construir a janela
            return
        of, produto = self._identidade()
        ordem = f"OF {of} · {produto}" if of else produto or "sem OF"
        modo = "Trigger" if self.btn_trigger.isChecked() else "Ao vivo"
        self.lbl_resumo.setText(f"{self.utilizador}   ·   {ordem}   ·   Perfil: {self._perfil.stem}   ·   "
                                f"Câmara Basler   ·   {modo}")

    # --- Arrancar e parar --------------------------------------------------------------
    def iniciar(self) -> None:
        if self.ciclo is not None:
            return
        modo = self._modo()
        # Sem OF as medidas são as do perfil (e dos defines), não as do produto na base de dados:
        # quem não pode produzir sem OF indica-a a cada início; os outros, enquanto não houver uma.
        perguntar = not self._pode("producao_sem_of") or (self._ordem is None and not self._ensaio_sem_of)
        if perguntar and not self._escolher_ordem():
            return
        self._parar_afinacao()  # a câmara só abre uma vez: passa da afinação para a inspeção
        self._contagem.clear()
        self._tempos.clear()
        self.historico.limpar()
        self._atualizar_contadores()
        self._lembrar()
        repositorio = None
        try:
            repositorio = self._repositorio()
        except ErroRepositorio as erro:  # produz-se na mesma; o registo.csv tem tudo
            log.warning("%s", erro)
        of, produto = self._identidade()
        self.ciclo = Ciclo(Pedido(modo, self._cfg_edicao, Path(self.args.saida), of, produto, repositorio))
        self.ciclo.etapas = self._engenharia()
        self.ciclo.registado.connect(self._registado)
        self.ciclo.visto.connect(self._recebido)
        self.ciclo.quadro.connect(self._ver_quadro)
        self.ciclo.mensagem.connect(self._mensagem)
        self.ciclo.finished.connect(self._terminou)
        self.ciclo.start()
        log.info("%s com a câmara (perfil %s, %s%s)", modo, self._perfil.name, self.utilizador,
                 f", OF {of}" if of else f", produto {produto}" if produto else "")
        self._atualizar_botoes()

    def parar(self) -> None:
        if self.ciclo is not None:
            self.ciclo.parar()

    # --- Afinar a câmara (só a imagem, sem inspeção) -------------------------------------
    def _alternar_afinacao(self) -> None:
        if self.btn_afinar.isChecked():
            self.afinar()
        else:
            self._parar_afinacao()

    def afinar(self) -> None:
        """A câmara no modo escolhido (Ao vivo ou Trigger), sem inspeção nem registo; abre a 📷 Câmara
        na secção que interessa: a exposição e o ganho ao vivo, o trigger em Trigger."""
        if self.ciclo is not None or self.afinacao is not None:
            return
        modo = self._modo()
        afinacao = self.afinacao = Afinacao(self._cfg_edicao, modo)
        afinacao.quadro.connect(self._ver_afinacao)
        afinacao.medidas.connect(self._medidas_afinacao)
        afinacao.contorno.connect(self._contorno_medido)
        afinacao.medir_contorno = self.btn_contorno.isChecked()
        afinacao.mensagem.connect(self._mensagem)
        afinacao.finished.connect(lambda: self._afinacao_terminou(afinacao))
        afinacao.start()
        trigger = modo == "trigger"
        for faixa in (self.faixa_op, self.faixa_eng):
            faixa.mostrar(None, "a afinar: trigger" if trigger else "a afinar: ao vivo")
        self.titulo_imagem.setText(f"<b style='color:{CORES_INDICADOR['afinar']}'>AFINAR CÂMARA</b>"
                                   f"   {'Trigger' if trigger else 'Ao vivo'} (sem inspeção nem registo)")
        self._texto_imagem = "À espera do sinal do botão ou do sensor…" if trigger else ""
        self._recomecar_contagem()
        self.editor_camara.mostrar_secao("disparo" if trigger else "aquisicao")
        self._forma_afinacao = None
        self._mostrar_auxilios()
        self._atualizar_botoes()

    def _parar_afinacao(self) -> None:
        """Para a afinação e espera que a câmara fique livre (a inspeção vai abri-la a seguir)."""
        if self.afinacao is not None:
            self.afinacao.parar()
            self.afinacao.wait(5000)

    def _afinacao_terminou(self, afinacao: Afinacao) -> None:
        afinacao.deleteLater()
        if self.afinacao is afinacao:
            self.afinacao = None
            self.btn_afinar.setChecked(False)
            self._texto_imagem = ""  # as medidas eram da afinação: não ficam como se fossem atuais
            self._recomecar_contagem()
            self._desenhar_roi(False)
            self._mostrar_auxilios()
            if self.ciclo is None:
                self.titulo_imagem.setText("")
                for faixa in (self.faixa_op, self.faixa_eng):
                    faixa.mostrar(None, "parado")
            self._atualizar_botoes()

    def _ver_afinacao(self, imagem) -> None:
        self._imagem_afinacao = imagem
        self.imagem_op.mostrar(imagem)
        self.imagem_eng.mostrar(imagem)
        if imagem.shape[:2] != self._forma_afinacao:  # a 1.ª imagem: já se sabe onde fica a barreira
            self._forma_afinacao = imagem.shape[:2]
            self._mostrar_auxilios()

    def _medidas_afinacao(self, texto: str) -> None:
        self._texto_imagem = texto
        self._mostrar_textos_afinacao()

    def _guardar_fundo(self) -> None:
        """A afinar, sem saco: a imagem inteira passa a ser o fundo de referência (dataset/<perfil>/fundo.png)."""
        if self.afinacao is None or self._imagem_afinacao is None:
            self._mensagem("Fundo vazio: primeiro Afinar câmara, sem saco à vista.")
            return
        caminho = self._dataset / "fundo.png"
        try:
            guardar_imagem(caminho, self._imagem_afinacao)
        except OSError as erro:
            QMessageBox.critical(self, "Fundo vazio", str(erro))
            return
        texto = caminho.relative_to(RAIZ).as_posix() if caminho.is_relative_to(RAIZ) else str(caminho)
        loc = dataclasses.replace(self._cfg_edicao.localizacao, fundo_referencia=texto)
        self._parametros_mudaram(dataclasses.replace(self._cfg_edicao, localizacao=loc))
        self._recomecar_contagem()
        self._mensagem(f"Fundo vazio guardado em {texto}: Guardar no perfil para o manter.")

    def _calibrar_escala(self) -> None:
        """px_por_mm a partir das medições do Contorno de um saco de medidas conhecidas (as nominais)."""
        if len(self._medicoes) < 3:
            self._mensagem("Calibrar escala: ligar o Contorno com o saco à vista e esperar por 3 medições ou mais.")
            return
        c, lg = np.array(self._medicoes).T
        d, ppm = self._cfg_edicao.dimensoes, self._cfg_edicao.geral.px_por_mm
        fator_c, fator_l = c.mean() / d.comprimento_nominal_mm, lg.mean() / d.largura_nominal_mm
        novo = ppm * (fator_c + fator_l) / 2
        resposta = QMessageBox.question(
            self, "Calibrar escala",
            f"Medido {c.mean():.2f} × {lg.mean():.2f} mm ({len(c)} medições); nominal "
            f"{d.comprimento_nominal_mm:g} × {d.largura_nominal_mm:g} mm.\n"
            f"px_por_mm: {ppm:.4f} → {novo:.4f}\n\n"
            f"O comprimento e a largura pedem escalas {abs(fator_c - fator_l) * 100:.1f} % diferentes "
            "(muito acima de 0,5 %: câmara inclinada ou nominal que não é a medida real).\n"
            "O nominal tem de ser a medida real deste saco (paquímetro). Aplicar?")
        if resposta != QMessageBox.StandardButton.Yes:
            return
        geral = dataclasses.replace(self._cfg_edicao.geral, px_por_mm=round(novo, 4))
        self._parametros_mudaram(dataclasses.replace(self._cfg_edicao, geral=geral))
        self._recomecar_contagem()
        self._mensagem(f"px_por_mm = {novo:.4f}: Guardar no perfil para o manter.")

    def _ligar_contorno(self, ligado: bool) -> None:
        if self.afinacao is not None:
            self.afinacao.medir_contorno = ligado
        self._recomecar_contagem()

    def _recomecar_contagem(self) -> None:
        """Esquece as medições do contorno (p.ex. antes de uma série de ruído ou de repetibilidade)."""
        self._medicoes.clear()
        self._texto_contorno = ""
        for vista in (self.imagem_op, self.imagem_eng):
            vista.contorno = vista.ideal = None
            vista.update()
        self._mostrar_textos_afinacao()

    def _contorno_medido(self, medicao: MedicaoContorno) -> None:
        if self.afinacao is None or not self.btn_contorno.isChecked():
            return  # chegou depois de desligar
        if medicao.erro:
            self._texto_contorno = f"<span style='color:#c62828'>Contorno: {html.escape(medicao.erro)}</span>"
        else:
            self._medicoes.append((medicao.comprimento_mm, medicao.largura_mm))
            self._texto_contorno = _texto_contorno(medicao, self._medicoes, self._cfg_edicao)
        for vista in (self.imagem_op, self.imagem_eng):
            vista.contorno, vista.ideal = medicao.contorno, medicao.ideal
            vista.update()
        self._mostrar_textos_afinacao()

    def _mostrar_textos_afinacao(self) -> None:
        if self._medicoes:
            c, lg = np.array(self._medicoes).T
            resumo = f"C {c[-1]:.2f} · L {lg[-1]:.2f} mm   (n {len(c)}"
            resumo += f", σ {c.std(ddof=1):.3f} / {lg.std(ddof=1):.3f} mm)" if len(c) >= 2 else ")"
        else:
            resumo = self._texto_imagem
        self.lbl_resumo_afinacao.setText(resumo)
        self._atualizar_passo()
        self.lbl_medidas.setText("<br>".join(t for t in (self._texto_imagem, self._texto_contorno) if t))
        self.info.setText("<br>".join(t for t in (self._texto_imagem.replace("   ·   ", "<br>"),
                                                  self._texto_contorno) if t))

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
        self._parar_afinacao()
        if self.ciclo is not None:
            self.ciclo.parar()
            self.ciclo.wait(5000)
        if self._previsao is not None:
            self._previsao.wait(5000)
        for janela in (self.janela_historico, self.janela_definicoes, self.janela_camara, self.janela_dashboard):
            if janela is not None:
                janela.close()
        if self._memoria is not None:  # os tamanhos que o utilizador escolheu
            for chave, divisoria in self._divisorias.items():
                self._memoria.setValue(chave, divisoria.saveState())
        logging.getLogger().removeHandler(self._registo)
        super().closeEvent(evento)

    # --- Mostrar -----------------------------------------------------------------------
    def _registado(self, visto: Visto) -> None:
        """Cada saco decidido. Um OK conta já; os outros vão à revisão (a inspeção espera)."""
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
            if self.ciclo.pedido.repositorio is not None:
                try:
                    self.ciclo.pedido.repositorio.marcar_decisao(self.ciclo.pasta_resultados.name, visto.numero,
                                                                 decisao)
                except ErroRepositorio as erro:
                    log.warning("%s", erro)
        self._em_revisao = None
        self._atualizar_contadores()
        self._atualizar_botoes()
        if self.ciclo is not None:
            self.ciclo.prosseguir()

    def _recebido(self, visto: Visto) -> None:
        """O saco mais recente, pronto a mostrar."""
        self._ultimo = visto
        if self.btn_congelar.isChecked() and self._engenharia():
            return  # o ecrã fica no saco escolhido; a linha continua
        # Só se desenha o mais recente: se a janela se atrasar, os intermédios são saltados.
        if self._pendente is None:
            QTimer.singleShot(0, self._desenhar_pendente)
        self._pendente = visto

    def _ver_quadro(self, imagem) -> None:
        """A imagem da câmara tal como chega, para acertar a luz e o foco; um saco acabado de analisar tem prioridade."""
        if monotonic() < self._segurar_ate or self._em_revisao is not None:
            return
        if self.btn_congelar.isChecked() and self._engenharia():
            return
        for faixa in (self.faixa_op, self.faixa_eng):
            faixa.mostrar(None, "câmara ao vivo")
        self.imagem_op.mostrar(imagem)
        self.imagem_eng.mostrar(imagem)
        self.titulo_imagem.setText("")

    def _desenhar_pendente(self) -> None:
        visto, self._pendente = self._pendente, None
        if visto is not None:
            self._mostrar(visto)

    def _mostrar(self, visto: Visto, original: bool = True) -> None:
        if original:
            self._base = visto
            self._segurar_ate = monotonic() + SEGURAR_SACO_S
        r = visto.resultado
        for faixa in (self.faixa_op, self.faixa_eng):
            faixa.mostrar(r.estado, f"saco {visto.numero}{_ressalva(r)}")
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
        self.acao_engenharia.setChecked(True)  # o detalhe do saco está na Engenharia
        self._mudar_vista()
        self.btn_congelar.setChecked(True)
        self._mostrar(visto if visto.preparado else preparar(visto))

    def _congelar(self, sim: bool) -> None:
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


def _acao(menu: QMenu, texto: str, funcao, icone_acao: QIcon | None = None,
          grupo: QActionGroup | None = None) -> QAction:
    """Uma entrada do menu ⚙; num grupo, é uma de várias opções (p.ex. a vista)."""
    acao = QAction(texto, menu)
    if icone_acao is not None:
        acao.setIcon(icone_acao)
    if grupo is not None:
        acao.setCheckable(True)
        grupo.addAction(acao)
    acao.triggered.connect(lambda *_: funcao())
    menu.addAction(acao)
    return acao


def _par(esquerdo: QWidget, direito: QWidget) -> QHBoxLayout:
    """Dois botões colados (um interruptor)."""
    par = QHBoxLayout()
    par.setSpacing(0)
    par.addWidget(esquerdo)
    par.addWidget(direito)
    return par


def _texto_contorno(medicao: MedicaoContorno, medicoes: list[tuple[float, float]], cfg: Config) -> str:
    """A medição do contorno (medidas e qualidade, a vermelho fora do objetivo) e as estatísticas da série:
    ao vivo com o saco parado, σ é o ruído; em Trigger, reposicionando a cada sinal, a repetibilidade."""
    q = medicao.medidas
    rms, rejeitados = q.get("contorno_rms_px", float("nan")), q.get("rejeitados_max_pct", float("nan"))
    pior = max(LADOS, key=lambda lado: q.get(f"rejeitados_{lado}_pct", 0.0))
    cor = "#2e7d32" if rms < CONTORNO_RMS_MAX_PX and rejeitados < CONTORNO_REJEITADOS_MAX_PCT else "#c62828"
    c, lg = np.array(medicoes).T
    d = cfg.dimensoes
    aresta = min(LADOS, key=lambda lado: q.get(f"aresta_{lado}_pct", 100.0))
    controlo = (f"aresta {q.get('aresta_min_pct', float('nan')):.0f} % (pior: {aresta}) · "
                f"reflexos {q.get('reflexos_max_pct', float('nan')):.0f} %")
    if "luz_pct" in q:
        controlo += f" · luz {q['luz_pct']:+.0f} % face ao fundo vazio"
    linhas = [f"<b>C {medicao.comprimento_mm:.2f} · L {medicao.largura_mm:.2f} mm</b>",
              f"<span style='color:{cor}'>RMS {rms:.2f} px · rejeitados {rejeitados:.0f} % (pior: {pior})</span>",
              controlo,
              f"n {len(c)} · média C {c.mean():.2f} · L {lg.mean():.2f} mm · face ao nominal "
              f"C {c.mean() - d.comprimento_nominal_mm:+.2f} · L {lg.mean() - d.largura_nominal_mm:+.2f} mm"]
    if len(c) >= 2:
        linhas.append(f"σ C {c.std(ddof=1):.3f} · σ L {lg.std(ddof=1):.3f} mm")
    if medicao.nao_fiavel:  # o controlo da medição do M2: na inspeção, este saco iria à revisão
        linhas.append(f"<span style='color:#c62828'>Na inspeção: ERRO, {html.escape(medicao.nao_fiavel)}</span>")
    return "<br>".join(linhas)


def _info(visto: Visto) -> str:
    """O resumo do saco no painel lateral (HTML)."""
    r, cfg = visto.resultado, visto.cfg
    linhas = []
    if r.captura is not None:
        linhas.append(f"<b>{html.escape(Path(r.captura.origem).name)}</b>")
    # O tempo do saco (inspeção + registo) a vermelho se passar do orçamento.
    ms = r.tempo_ms if visto.previsao else visto.ciclo_ms
    cor = "#c62828" if ms > cfg.geral.orcamento_ms else "inherit"
    tempo = f"<span style='color:{cor}'>{ms:.0f} ms</span>"
    if r.saco is not None:
        tempo += f"   ·   {r.saco.comprimento_mm:.1f} × {r.saco.largura_mm:.1f} mm"
    linhas.append(tempo)
    if r.mensagem:
        linhas.append(f"<span style='color:#ef6c00'>{html.escape(r.mensagem)}</span>")
    if r.ok and r.nao_verificado:
        linhas.append(f"<span style='color:#ef6c00'>OK só por {html.escape(', '.join(r.verificado_por))}; "
                      f"não verificado: {html.escape(', '.join(r.nao_verificado))}</span>")
    if r.atipicas:
        linhas.append("<span style='color:#ef6c00'>Atípico face aos sacos OK: "
                      + html.escape("; ".join(a.texto for a in r.atipicas)) + "</span>")
    return "<br>".join(linhas)


def _ressalva(r) -> str:
    """O que um OK não garante, curto, para a faixa: " · só dimensoes", " · atípico"."""
    if not r.ok:
        return ""
    texto = f" · só {', '.join(r.verificado_por)}" if r.nao_verificado else ""
    return texto + (" · atípico" if r.atipicas else "")
