"""Interface, janelas à parte: a base comum, o histórico e a revisão de um saco não OK."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from interface.ciclo import Visto
from interface.estilo import AZUL_ESCURO, NOME_APP
from interface.paineis import Contadores, Faixa, Historico, VistaImagem
from nucleo.contrato import CLASSES_DATASET, Resultado


class JanelaAuxiliar(QWidget):
    """Uma janela à parte (histórico, definições, câmara), que pode ficar ao lado da principal."""

    def __init__(self, principal: QWidget, titulo: str, icone_janela: QIcon, conteudo: QWidget,
                 tamanho: tuple[int, int]) -> None:
        super().__init__(principal, Qt.WindowType.Window)
        self.setWindowTitle(f"{NOME_APP}: {titulo}")
        simbolo = QLabel()
        simbolo.setPixmap(icone_janela.pixmap(32, 32))
        nome = QLabel(titulo)
        nome.setStyleSheet(f"color: {AZUL_ESCURO}; font-size: 16pt; font-weight: bold;")
        cabecalho = QHBoxLayout()
        cabecalho.addWidget(simbolo)
        cabecalho.addWidget(nome, 1)
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(self.close)
        rodape = QHBoxLayout()
        rodape.addStretch()
        rodape.addWidget(fechar)
        layout = QVBoxLayout(self)
        layout.addLayout(cabecalho)
        layout.addWidget(conteudo, 1)
        layout.addLayout(rodape)
        self.resize(*tamanho)

    def abrir(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


class JanelaHistorico(JanelaAuxiliar):
    """O histórico dos sacos e os contadores."""

    def __init__(self, principal: QWidget, icone_janela: QIcon) -> None:
        contadores, tabela = Contadores(18), Historico()
        tabela.setToolTip("Clica num saco para o ver na janela principal")
        conteudo = QWidget()
        layout = QVBoxLayout(conteudo)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(contadores)
        layout.addWidget(tabela, 1)
        super().__init__(principal, "Histórico de sacos", icone_janela, conteudo, (1000, 650))
        self.contadores, self.tabela = contadores, tabela


def classe_proposta(resultado: Resultado) -> str:
    """A pasta do dataset que o sistema propõe para um saco NOK: o tipo de defeito, "varios" com mais
    do que um tipo, ou "" (não guardar) sem defeitos (ERRO, SEM_SACO): aí não há classe a propor."""
    tipos = sorted(str(tipo) for tipo in resultado.contagem_tipos)
    if not tipos:
        return ""
    return tipos[0] if len(tipos) == 1 else "varios"


class JanelaRevisao(QDialog):
    """Um saco não OK à espera de decisão (a inspeção está parada). Três decisões:

        Mover para NOK   conta como NOK; a imagem vai para dataset/<classe>/ (a escolhida na lista,
                         proposta pelo que o sistema viu; "não guardar" deixa-a de fora)
        Era OK           falso NOK: não conta (como descartado); a imagem vai para dataset/ok/
        Descartar        passagem má (dois sacos, saco cortado...): não conta e não vai para o dataset

    Não fecha sem decisão (nem com Esc nem com o X).
    """

    decidido = Signal(str, str)  # ("NOK", "falso NOK" ou "descartado"; a classe do dataset, ou "")

    def __init__(self, principal: QWidget, visto: Visto) -> None:
        super().__init__(principal)
        self.visto = visto
        r = visto.resultado
        self.setWindowTitle(f"{NOME_APP}: saco {visto.numero}")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        faixa = Faixa(22)
        faixa.mostrar(r.estado, f"saco {visto.numero}")
        imagem = VistaImagem("sem imagem")
        imagem.setMinimumSize(640, 420)
        imagem.mostrar(visto.anotada)
        motivo = QLabel(r.mensagem or "; ".join(d.descricao for d in r.defeitos[:3]) or str(r.estado))
        motivo.setWordWrap(True)
        self.classe = QComboBox()
        self.classe.addItem("não guardar no dataset", "")
        for classe in CLASSES_DATASET:
            if classe != "ok":
                self.classe.addItem(classe, classe)
        self.classe.setCurrentIndex(max(0, self.classe.findData(classe_proposta(r))))
        self.classe.setToolTip("O defeito que o saco tem de facto: a imagem vai para essa pasta do dataset")
        linha_classe = QHBoxLayout()
        linha_classe.addWidget(QLabel("Defeito (para o dataset):"))
        linha_classe.addWidget(self.classe, 1)
        descartar = QPushButton("Descartar (passagem má)")
        descartar.setToolTip("Não conta e não vai para o dataset: dois sacos, saco cortado, sem saco...")
        era_ok = QPushButton("Era OK (falso NOK)")
        era_ok.setToolTip("O saco é bom: não conta como NOK e a imagem vai para o dataset como ok")
        nok = QPushButton("Mover para NOK e prosseguir")
        nok.setObjectName("parar")  # vermelho (ESTILO)
        descartar.clicked.connect(lambda: self._decidir("descartado"))
        era_ok.clicked.connect(lambda: self._decidir("falso NOK"))
        nok.clicked.connect(lambda: self._decidir("NOK"))
        botoes = QHBoxLayout()
        botoes.addWidget(descartar, 1)
        botoes.addWidget(era_ok, 1)
        botoes.addWidget(nok, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(faixa)
        layout.addWidget(imagem, 1)
        layout.addWidget(motivo)
        layout.addLayout(linha_classe)
        layout.addLayout(botoes)
        self.resize(900, 700)

    def _decidir(self, decisao: str) -> None:
        classe = {"NOK": self.classe.currentData(), "falso NOK": "ok"}.get(decisao, "")
        self.decidido.emit(decisao, classe)
        self.accept()

    def reject(self) -> None:  # Esc: é preciso decidir
        pass
