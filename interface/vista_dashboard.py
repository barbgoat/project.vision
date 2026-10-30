"""Interface, vista do dashboard: mostra o estado do dashboard (nucleo.dashboard.estado), só a ver.

Usada pela BIapp (interface/ordens.py, com os botões de editar por cima) e pela janela
própria do biapp_dashboard.py (que recebe o estado por SSE). Não lê a base de dados:
só mostra o dicionário que recebe em mostrar().

    Hoje                os sacos de hoje: OK, NOK, descartados; Reiniciar e Limpar dados (em testes)
    Ordens de fabrico   procurar; por OF, o resumo, as medidas (pedido vs medido), os
                        defeitos por tipo e a evolução no tempo (Grafico)
    Produtos            procurar
    Por dia             os totais de cada dia
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget, QTabWidget, QToolTip,
    QVBoxLayout, QWidget,
)

from interface.estilo import AZUL
from interface.paineis import Contadores, _celula, _preparar_tabela
from nucleo import dashboard

COR_OK, COR_NOK = "#81c784", "#c62828"  # no gráfico: verde claro e vermelho (distinguem-se também em daltónicos)
_ESQUERDA = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_ERRO = "color: #c62828;"


class VistaDashboard(QWidget):
    """O dashboard a partir de um estado; mostrar() pode ser chamado a cada atualização (tempo real).

    A OF e o produto escolhidos mantêm-se entre atualizações. barra_of e barra_produtos
    são onde quem a usa põe os seus botões (a BIapp: editar, duplicar...). A vista não
    mexe na base de dados: Reiniciar e Limpar dados só avisam quem a usa (sinais).
    """

    reiniciar_pedido = Signal()  # depois de a vista voltar ao início: ler tudo outra vez
    limpar_pedido = Signal(bool)  # apagar os dados (já confirmado); True = também os produtos e as OFs

    def __init__(self) -> None:
        super().__init__()
        self._estado: dict = {}
        self.titulo_hoje = QLabel()
        self.titulo_hoje.setObjectName("cabecalho")  # pequeno e cinzento (ESTILO)
        self.hoje = Contadores(18)
        self.atualizado = QLabel()
        self.atualizado.setStyleSheet("color: #78909c;")
        self.btn_reiniciar = QPushButton("Reiniciar")
        self.btn_reiniciar.setToolTip("Limpa as procuras e as escolhas e volta a ler tudo")
        self.btn_reiniciar.clicked.connect(self._reiniciar)
        self.btn_limpar = QPushButton("Limpar dados…")
        self.btn_limpar.setObjectName("parar")  # vermelho (ESTILO)
        self.btn_limpar.setToolTip("Apaga os dados da base de dados, para recomeçar os testes (pergunta antes)")
        self.btn_limpar.clicked.connect(self._limpar)
        self.btn_limpar.hide()  # só quem a usa decide se pode (permitir_limpar)

        # Ordens de fabrico
        self.procura_of = QLineEdit()
        self.procura_of.setPlaceholderText("procurar OF ou produto")
        self.tabela_of = _tabela(dashboard.COLUNAS_OF)
        self.resumo = QLabel()
        self.resumo.setWordWrap(True)
        self.medidas = _tabela(dashboard.COLUNAS_MEDIDAS)
        self.medidas.setMaximumHeight(110)
        self.tipos = _tabela(dashboard.COLUNAS_TIPOS)
        self.grafico = Grafico()
        self.barra_of = QHBoxLayout()
        self.barra_of.addWidget(self.resumo, 1)
        tabelas = QVBoxLayout()
        tabelas.addWidget(self.medidas)
        tabelas.addWidget(self.tipos, 1)
        graficos = QHBoxLayout()
        graficos.addLayout(tabelas, 2)
        graficos.addWidget(self.grafico, 3)
        detalhe = QWidget()
        coluna = QVBoxLayout(detalhe)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.addLayout(self.barra_of)
        coluna.addLayout(graficos, 1)
        divisoria = QSplitter(Qt.Orientation.Vertical)
        divisoria.addWidget(self.tabela_of)
        divisoria.addWidget(detalhe)
        divisoria.setSizes([250, 450])
        ordens = QWidget()
        coluna = QVBoxLayout(ordens)
        coluna.addWidget(self.procura_of)
        coluna.addWidget(divisoria, 1)

        # Produtos e dias
        self.procura_produto = QLineEdit()
        self.procura_produto.setPlaceholderText("procurar produto")
        self.tabela_produtos = _tabela(dashboard.COLUNAS_PRODUTOS)
        self.barra_produtos = QHBoxLayout()
        self.barra_produtos.addStretch()
        produtos = QWidget()
        coluna = QVBoxLayout(produtos)
        coluna.addWidget(self.procura_produto)
        coluna.addWidget(self.tabela_produtos, 1)
        coluna.addLayout(self.barra_produtos)
        self.tabela_dias = _tabela(dashboard.COLUNAS_DIAS)

        self.separadores = QTabWidget()
        self.separadores.addTab(ordens, "Ordens de fabrico")
        self.separadores.addTab(produtos, "Produtos")
        self.separadores.addTab(self.tabela_dias, "Por dia")
        topo = QHBoxLayout()
        topo.addWidget(self.titulo_hoje)
        topo.addStretch()
        topo.addWidget(self.atualizado)
        topo.addWidget(self.btn_reiniciar)
        topo.addWidget(self.btn_limpar)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(topo)
        layout.addWidget(self.hoje)
        layout.addWidget(self.separadores, 1)

        self.procura_of.textChanged.connect(self._preencher_ordens)
        self.procura_produto.textChanged.connect(self._preencher_produtos)
        self.tabela_of.itemSelectionChanged.connect(self._mostrar_of)
        self.mostrar({})

    # --- O que se mostra -------------------------------------------------------------
    def mostrar(self, estado: dict) -> None:
        """Mostra um estado novo (de nucleo.dashboard.estado), mantendo o que estava escolhido."""
        self._estado = estado
        hoje = estado.get("hoje", {})
        self.titulo_hoje.setText(f"HOJE {hoje.get('dia', '')}".strip())
        self.hoje.mostrar(Counter(OK=hoje.get("ok", 0), NOK=hoje.get("nok", 0),
                                  DESCARTADOS=hoje.get("descartados", 0)), [], 0)
        self.atualizado.setText(f"atualizado às {estado['gerado'][11:]}" if estado.get("gerado") else "")
        self._preencher_ordens()
        self._preencher_produtos()
        _preencher(self.tabela_dias, [d["colunas"] for d in estado.get("dias", [])])

    def reiniciar(self) -> None:
        """Volta ao início: sem procuras nem escolhas, no primeiro separador."""
        for procura in (self.procura_of, self.procura_produto):
            procura.clear()
        for tabela in (self.tabela_of, self.tabela_produtos):
            tabela.clearSelection()
        self.separadores.setCurrentIndex(0)
        self.mostrar(self._estado)

    def permitir_limpar(self, sim: bool) -> None:
        """Mostra ou esconde o botão Limpar dados (SAIDA_DASHBOARD_LIMPAR)."""
        self.btn_limpar.setVisible(sim)

    def _reiniciar(self) -> None:
        self.reiniciar()
        self.reiniciar_pedido.emit()

    def _limpar(self) -> None:
        tudo = perguntar_limpeza(self)
        if tudo is not None:
            self.limpar_pedido.emit(tudo)

    def erro(self, texto: str) -> None:
        """Um problema a ler os dados (a base de dados ou a ligação ao servidor)."""
        self.atualizado.setText(f"<span style='{_ERRO}'>{html.escape(texto)}</span>")

    def of_escolhida(self) -> str | None:
        """O número da OF escolhida na tabela, ou None."""
        linhas = self.tabela_of.selectionModel().selectedRows()
        return self.tabela_of.item(linhas[0].row(), 1).text() if linhas else None

    def produto_escolhido(self) -> str | None:
        """O nome do produto escolhido na tabela, ou None."""
        linhas = self.tabela_produtos.selectionModel().selectedRows()
        return self.tabela_produtos.item(linhas[0].row(), 1).text() if linhas else None

    # --- Preencher ---------------------------------------------------------------------
    def _ordens(self) -> list[dict]:
        procura = self.procura_of.text().strip().casefold()
        return [o for o in self._estado.get("ordens", [])
                if procura in o["numero"].casefold() or procura in o["produto"].casefold()]

    def _preencher_ordens(self) -> None:
        escolhida = self.of_escolhida()
        ordens = self._ordens()
        self.tabela_of.blockSignals(True)
        _preencher(self.tabela_of, [o["colunas"] for o in ordens])
        numeros = [o["numero"] for o in ordens]
        if numeros:
            self.tabela_of.selectRow(numeros.index(escolhida) if escolhida in numeros else 0)
        self.tabela_of.blockSignals(False)
        self._mostrar_of()

    def _preencher_produtos(self) -> None:
        escolhido = self.produto_escolhido()
        procura = self.procura_produto.text().strip().casefold()
        produtos = [p for p in self._estado.get("produtos", []) if procura in p["produto"].casefold()]
        _preencher(self.tabela_produtos, [p["colunas"] for p in produtos])
        nomes = [p["produto"] for p in produtos]
        if escolhido in nomes:
            self.tabela_produtos.selectRow(nomes.index(escolhido))

    def _mostrar_of(self) -> None:
        numero = self.of_escolhida()
        of = next((o for o in self._estado.get("ordens", []) if o["numero"] == numero), None)
        if of is None:
            self.resumo.setText("Sem OFs." if self._estado else "")
            for tabela in (self.medidas, self.tipos):
                tabela.setRowCount(0)
            self.grafico.mostrar([])
            return
        self.resumo.setText(
            f"<b style='color:{AZUL}; font-size:14pt'>OF {html.escape(of['numero'])} · {html.escape(of['produto'])}</b>"
            f"<br>{html.escape(of['resumo'])}; <b>taxa de NOK {100 * of['taxa_nok']:.1f} %</b>"
            f"<br><span style='color:#78909c'>Ajustes da OF: {html.escape(of['ajustes'])}</span>")
        _preencher(self.medidas, of["medidas"])
        _preencher(self.tipos, of["tipos"])
        self.grafico.mostrar([tuple(periodo) for periodo in of["evolucao"]])


def perguntar_limpeza(pai: QWidget) -> bool | None:
    """Pergunta o que apagar: False = só as inspeções; True = tudo; None = não apagar."""
    caixa = QMessageBox(QMessageBox.Icon.Warning, "Limpar dados",
                        "Apagar os dados do dashboard? Não se pode desfazer.\n\n"
                        "Só as inspeções: as contagens, os dias e os gráficos voltam a zero; os produtos e as "
                        "OFs ficam.\nTudo: também os produtos e as OFs.", parent=pai)
    inspecoes = caixa.addButton("Só as inspeções", QMessageBox.ButtonRole.DestructiveRole)
    tudo = caixa.addButton("Tudo", QMessageBox.ButtonRole.DestructiveRole)
    caixa.setDefaultButton(caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole))
    caixa.exec()
    return {inspecoes: False, tudo: True}.get(caixa.clickedButton())


def _tabela(colunas: list[str]) -> QTableWidget:
    """Uma tabela do dashboard: cabeçalhos e valores alinhados à esquerda, com a mesma margem;
    só a última coluna estica."""
    tabela = QTableWidget()
    _preparar_tabela(tabela, colunas, len(colunas) - 1)
    tabela.horizontalHeader().setDefaultAlignment(_ESQUERDA)
    tabela.setStyleSheet("QTableWidget::item { padding: 0px 8px; }")  # a margem dos cabeçalhos (ESTILO)
    return tabela


def _preencher(tabela: QTableWidget, linhas: list[list]) -> None:
    """Põe as linhas na tabela, cada valor por baixo do seu cabeçalho."""
    tabela.setRowCount(len(linhas))
    for i, valores in enumerate(linhas):
        for coluna, valor in enumerate(valores):
            item = _celula(valor, dica=str(valor))
            item.setTextAlignment(_ESQUERDA)
            tabela.setItem(i, coluna, item)


class Grafico(QWidget):
    """A evolução de uma OF: OK (em baixo) e NOK (em cima) por período, com a taxa de NOK por cima."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self._evolucao: list[tuple[str, int, int]] = []
        self._barras: list[QRectF] = []

    def mostrar(self, evolucao: list[tuple[str, int, int]]) -> None:
        self._evolucao = evolucao
        self.update()

    def paintEvent(self, evento) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tinta, discreta = QColor("#263238"), QColor("#78909c")
        esq, topo, dir_, baixo = 40, 46, 12, 24  # em cima: a legenda e, por baixo, a taxa da barra mais alta
        area = QRectF(esq, topo, self.width() - esq - dir_, self.height() - topo - baixo)
        # Legenda: o texto é sempre cinzento escuro; o quadrado ao lado diz a cor.
        x = area.right()
        for nome, cor in (("NOK", COR_NOK), ("OK", COR_OK)):
            largura = p.fontMetrics().horizontalAdvance(nome)
            x -= largura
            p.setPen(tinta)
            p.drawText(QRectF(x, 4, largura, 18), Qt.AlignmentFlag.AlignVCenter, nome)
            x -= 16
            p.fillRect(QRectF(x, 8, 10, 10), QColor(cor))
            x -= 14
        self._barras = []
        if not self._evolucao:
            p.setPen(discreta)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Ainda não há sacos inspecionados nesta OF.")
            return
        maximo = max(ok + nok for _, ok, nok in self._evolucao) or 1
        p.setPen(QColor("#cfd8dc"))
        p.drawLine(area.bottomLeft(), area.bottomRight())  # a linha de base
        p.setPen(discreta)
        p.drawText(QRectF(0, area.top() - 8, esq - 6, 16), Qt.AlignmentFlag.AlignRight, str(maximo))
        p.drawText(QRectF(0, area.bottom() - 8, esq - 6, 16), Qt.AlignmentFlag.AlignRight, "0")
        n = len(self._evolucao)
        passo = area.width() / n
        largura = max(2.0, min(48.0, passo - 4))
        for i, (_, ok, nok) in enumerate(self._evolucao):
            x = area.left() + i * passo + (passo - largura) / 2
            h_ok, h_nok = area.height() * ok / maximo, area.height() * nok / maximo
            p.fillRect(QRectF(x, area.bottom() - h_ok, largura, h_ok), QColor(COR_OK))
            if nok:  # 2 px de intervalo entre as duas partes
                p.fillRect(QRectF(x, area.bottom() - h_ok - h_nok, largura, max(1.0, h_nok - 2)), QColor(COR_NOK))
            self._barras.append(QRectF(area.left() + i * passo, area.top(), passo, area.height()))
            if largura >= 28 and ok + nok:
                p.setPen(tinta)
                p.drawText(QRectF(x - 8, area.bottom() - h_ok - h_nok - 18, largura + 16, 16),
                           Qt.AlignmentFlag.AlignCenter, f"{100 * nok / (ok + nok):.0f}%")
        p.setPen(discreta)
        for i in {0, n - 1}:  # só o primeiro e o último período, para não se sobreporem
            # Por baixo da sua barra, sem sair do gráfico.
            largura_texto = p.fontMetrics().horizontalAdvance(self._evolucao[i][0]) + 8
            centro = area.left() + (i + 0.5) * passo
            x = min(max(centro - largura_texto / 2, area.left()), area.right() - largura_texto)
            p.drawText(QRectF(x, area.bottom() + 4, largura_texto, 18), Qt.AlignmentFlag.AlignCenter,
                       self._evolucao[i][0])

    def mouseMoveEvent(self, evento) -> None:
        ponto = evento.position()
        for (inicio, ok, nok), zona in zip(self._evolucao, self._barras):
            if zona.contains(ponto):
                fim = datetime.strptime(inicio, "%Y-%m-%d %H:%M") + timedelta(minutes=dashboard.PERIODO_MIN)
                taxa = 100 * nok / (ok + nok) if ok + nok else 0
                QToolTip.showText(evento.globalPosition().toPoint(),
                                  f"{inicio} a {fim:%H:%M}\nOK {ok}   NOK {nok}   ({taxa:.1f} % NOK)", self)
                return
        QToolTip.hideText()
