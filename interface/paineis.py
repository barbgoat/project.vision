"""Interface, painéis: as peças da janela. Só mostram e avisam; quem decide é a janela.

    VistaImagem        uma imagem do OpenCV ajustada ao espaço, sem a deformar
    Faixa              o estado do saco em grande, com a cor do estado
    Contadores         OK, NOK e descartados, em mosaicos, e o tempo por saco
    TabelaModulos      estado, tempo e mensagem de cada módulo
    TabelaDefeitos     um defeito por linha, em mm (o número é o da imagem anotada)
    Etapas             miniaturas das imagens de cada etapa; clicar mostra-a em grande
    Historico          os últimos sacos; clicar volta a mostrar um
    PainelLog          o log do programa
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListView, QListWidget, QListWidgetItem, QPlainTextEdit,
    QSizePolicy, QTableWidget, QTableWidgetItem, QWidget,
)

from interface.ciclo import MINIATURA_PX, Visto
from interface.estilo import CORES_ESTADO
from nucleo.contrato import Estado, Resultado


HISTORICO_LINHAS = 500  # sacos na tabela do histórico
HISTORICO_IMAGENS = 10  # sacos que guardam as imagens para se voltar a ver (com a câmara, 50–100 MB cada)


def qimagem(imagem: np.ndarray) -> QImage:
    """BGR (ou cinzento) uint8 do OpenCV → QImage (cópia)."""
    if imagem.ndim == 2:
        imagem = cv2.cvtColor(imagem, cv2.COLOR_GRAY2BGR)
    imagem = np.ascontiguousarray(imagem)
    altura, largura = imagem.shape[:2]
    return QImage(imagem.data, largura, altura, imagem.strides[0], QImage.Format.Format_BGR888).copy()


def _preparar_tabela(tabela: QTableWidget, colunas: list[str], esticar: int) -> None:
    """Tabela só de leitura, linha a linha, com a coluna `esticar` a ocupar o espaço que sobra."""
    tabela.setColumnCount(len(colunas))
    tabela.setHorizontalHeaderLabels(colunas)
    tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tabela.setWordWrap(False)  # uma linha por item; o texto todo aparece ao passar o rato
    tabela.setShowGrid(False)
    tabela.setAlternatingRowColors(True)
    tabela.verticalHeader().setVisible(False)
    cabecalho = tabela.horizontalHeader()
    cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    cabecalho.setSectionResizeMode(esticar, QHeaderView.ResizeMode.Stretch)


def _celula(texto: Any, estado: Estado | None = None, dica: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(texto))
    if estado is not None:
        item.setBackground(QColor(CORES_ESTADO[estado]))
        item.setForeground(QColor("white"))
    if dica:
        item.setToolTip(dica)
    return item


# --- Imagem e estado ----------------------------------------------------------------------

class VistaImagem(QWidget):
    """Uma imagem do OpenCV ajustada ao espaço disponível, sem a deformar, com zoom.

    Roda do rato: amplia ou desamplia à volta do cursor. Arrastar: move a imagem
    ampliada. Duplo clique: volta a ajustar ao espaço. Uma imagem nova do mesmo
    tamanho (p.ex. outra etapa) fica com o mesmo zoom, para comparar a mesma zona.
    """

    _FUNDO, _TEXTO = QColor("#1e1e1e"), QColor("#9e9e9e")
    _ZOOM_MAX = 20.0  # em relação a ajustada ao espaço
    _PASSO_ZOOM = 1.25  # por cada passo da roda do rato

    def __init__(self, texto_vazio: str = "") -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(160, 120)
        self.setToolTip("Roda do rato: ampliar · arrastar: mover · duplo clique: ajustar")
        self._texto_vazio = texto_vazio
        self._original: QPixmap | None = None
        self._zoom = 1.0  # 1 = a imagem inteira ajustada ao espaço
        self._centro = QPointF()  # o ponto da imagem (px) que fica no centro da vista
        self._arrasto: tuple[QPointF, QPointF] | None = None  # (posição do rato, centro) ao carregar

    def mostrar(self, imagem: np.ndarray | None) -> None:
        anterior = self._original.size() if self._original is not None else None
        self._original = QPixmap.fromImage(qimagem(imagem)) if imagem is not None else None
        if self._original is None or self._original.size() != anterior:
            self._ajustar()
        self.update()

    def _ajustar(self) -> None:
        self._zoom = 1.0
        if self._original is not None:
            self._centro = QPointF(self._original.width() / 2, self._original.height() / 2)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _escala(self) -> float:
        """px do ecrã por px da imagem."""
        return min(self.width() / self._original.width(), self.height() / self._original.height()) * self._zoom

    def _limitar_centro(self) -> None:
        """Com zoom, a imagem não sai do ecrã; ajustada, fica centrada."""
        largura, altura = self._original.width(), self._original.height()
        if self._zoom <= 1.0:
            self._centro = QPointF(largura / 2, altura / 2)
            return
        meia_x, meia_y = self.width() / 2 / self._escala(), self.height() / 2 / self._escala()
        x = min(max(self._centro.x(), meia_x), largura - meia_x) if largura > 2 * meia_x else largura / 2
        y = min(max(self._centro.y(), meia_y), altura - meia_y) if altura > 2 * meia_y else altura / 2
        self._centro = QPointF(x, y)

    def paintEvent(self, evento) -> None:
        pintor = QPainter(self)
        pintor.fillRect(self.rect(), self._FUNDO)
        if self._original is None:
            pintor.setPen(self._TEXTO)
            pintor.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._texto_vazio)
            return
        escala = self._escala()
        canto = QPointF(self.width() / 2 - self._centro.x() * escala, self.height() / 2 - self._centro.y() * escala)
        alvo = QRectF(canto, QSizeF(self._original.width() * escala, self._original.height() * escala))
        visivel = alvo.intersected(QRectF(self.rect()))
        fonte = QRectF((visivel.x() - canto.x()) / escala, (visivel.y() - canto.y()) / escala,
                       visivel.width() / escala, visivel.height() / escala)
        pintor.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 4)  # muito ampliada: píxeis
        pintor.drawPixmap(visivel, self._original, fonte)
        if self._zoom > 1.0:  # quanto está ampliada, no canto
            texto = f"{self._zoom:.1f}×"
            caixa = QRectF(self.width() - 70, self.height() - 30, 62, 22)
            pintor.fillRect(caixa, QColor(0, 0, 0, 150))
            pintor.setPen(QColor("white"))
            pintor.drawText(caixa, Qt.AlignmentFlag.AlignCenter, texto)

    def wheelEvent(self, evento) -> None:
        if self._original is None:
            return
        cursor = evento.position()
        escala = self._escala()
        ponto = QPointF(self._centro.x() + (cursor.x() - self.width() / 2) / escala,
                        self._centro.y() + (cursor.y() - self.height() / 2) / escala)  # o ponto sob o cursor
        passos = evento.angleDelta().y() / 120
        self._zoom = min(max(self._zoom * self._PASSO_ZOOM ** passos, 1.0), self._ZOOM_MAX)
        escala = self._escala()  # o ponto sob o cursor fica sob o cursor
        self._centro = QPointF(ponto.x() - (cursor.x() - self.width() / 2) / escala,
                               ponto.y() - (cursor.y() - self.height() / 2) / escala)
        self._limitar_centro()
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._zoom > 1.0 else Qt.CursorShape.ArrowCursor)
        self.update()
        evento.accept()

    def mousePressEvent(self, evento) -> None:
        if self._original is not None and self._zoom > 1.0 and evento.button() == Qt.MouseButton.LeftButton:
            self._arrasto = (evento.position(), QPointF(self._centro))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, evento) -> None:
        if self._arrasto is not None:
            inicio, centro = self._arrasto
            self._centro = centro - (evento.position() - inicio) / self._escala()
            self._limitar_centro()
            self.update()

    def mouseReleaseEvent(self, evento) -> None:
        if self._arrasto is not None:
            self._arrasto = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, evento) -> None:
        if self._original is not None:
            self._ajustar()

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        if self._original is not None:
            self._limitar_centro()


class Faixa(QLabel):
    """O estado do saco em grande, com a cor do estado."""

    def __init__(self, tamanho_pt: int) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # não alarga a janela
        self._tamanho = tamanho_pt
        self.mostrar(None, "parado")

    def mostrar(self, estado: Estado | None, texto: str) -> None:
        cor = CORES_ESTADO.get(estado, "#424242")
        self.setText(f"{estado}   {texto}" if estado else texto)
        self.setStyleSheet(f"background: {cor}; color: white; font-weight: bold; "
                           f"font-size: {self._tamanho}pt; padding: 6px; border-radius: 4px;")


def resumo(contagem: Counter[str], tempos: list[float], orcamento_ms: float, nao_analisadas: int = 0) -> str:
    """Os contadores numa linha, p.ex. "9 sacos  OK 1  NOK 7  DESCARTADOS 1 | médio 120 ms, máx. 310 ms (...)"."""
    texto = f"{contagem['OK'] + contagem['NOK']} sacos   " + "   ".join(f"{c} {contagem[c]}" for c in Contadores.CLASSES)
    if tempos:
        texto += (f"   |   médio {sum(tempos) / len(tempos):.0f} ms, máx. {max(tempos):.0f} ms "
                  f"(orçamento {orcamento_ms:.0f})")
    if nao_analisadas:
        texto += f"   |   {nao_analisadas} não analisadas"
    return texto


class Contadores(QWidget):
    """Os sacos contados (OK, NOK depois da revisão, descartados na revisão), em mosaicos, e o tempo por saco."""

    CLASSES = ("OK", "NOK", "DESCARTADOS")
    CORES = {"OK": CORES_ESTADO[Estado.OK], "NOK": CORES_ESTADO[Estado.NOK], "DESCARTADOS": "#616161"}

    def __init__(self, tamanho_pt: int) -> None:
        super().__init__()
        self._tamanho = tamanho_pt
        self._mosaicos: dict[str, QLabel] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for classe in self.CLASSES:
            mosaico = QLabel()
            mosaico.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mosaico.setStyleSheet(f"background: {self.CORES[classe]}; color: white; border-radius: 6px; "
                                  "padding: 4px 12px;")
            mosaico.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(mosaico, 1)
            self._mosaicos[classe] = mosaico
        self.tempos = QLabel()
        self.tempos.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.tempos, 1)
        self._texto = ""
        self.mostrar(Counter(), [], 0)

    def mostrar(self, contagem: Counter[str], tempos: list[float], orcamento_ms: float,
                nao_analisadas: int = 0) -> None:
        for classe, mosaico in self._mosaicos.items():
            mosaico.setText(f"<span style='font-size: {self._tamanho}pt; font-weight: bold;'>{contagem[classe]}"
                            f"</span><br>{classe}")
        linhas = [f"<b>{contagem['OK'] + contagem['NOK']} sacos</b>"]
        if tempos:
            linhas.append(f"{sum(tempos) / len(tempos):.0f} ms por saco")
        if nao_analisadas:
            linhas.append(f"<span style='color: #c62828;'>{nao_analisadas} não analisadas</span>")
        self.tempos.setText("<br>".join(linhas))
        self._texto = resumo(contagem, tempos, orcamento_ms, nao_analisadas)

    def texto(self) -> str:
        """O mesmo, numa linha de texto."""
        return self._texto


class TabelaModulos(QTableWidget):
    """Estado, tempo e mensagem de cada módulo; as medidas (ou o traceback do ERRO) ao passar o rato."""

    def __init__(self) -> None:
        super().__init__()
        _preparar_tabela(self, ["Módulo", "Estado", "ms", "Mensagem"], esticar=3)

    def mostrar(self, resultado: Resultado) -> None:
        self.setRowCount(len(resultado.relatorios))
        for i, r in enumerate(resultado.relatorios):
            medidas = "\n".join(f"{k} = {v:.1f}" for k, v in r.medidas.items())
            dica = r.detalhe or "\n".join(filter(None, [r.mensagem, medidas]))
            for j, item in enumerate([_celula(r.modulo, dica=dica), _celula(r.resumo, r.estado, dica),
                                      _celula(f"{r.tempo_ms:.0f}"), _celula(r.mensagem, dica=dica)]):
                self.setItem(i, j, item)
        self.resizeRowsToContents()


class TabelaDefeitos(QTableWidget):
    """Um defeito por linha, em mm, no referencial do saco; o nº é o da imagem anotada."""

    def __init__(self) -> None:
        super().__init__()
        _preparar_tabela(self, ["Nº", "Tipo", "x", "y", "Comp.", "Larg.", "Área mm²", "Descrição"], esticar=7)

    def mostrar(self, resultado: Resultado) -> None:
        defeitos = resultado.defeitos
        self.setRowCount(len(defeitos))
        for i, d in enumerate(defeitos):
            valores = [i + 1, d.tipo, *(f"{v:.1f}" for v in (d.x_mm, d.y_mm, d.comprimento_mm,
                                                               d.largura_mm, d.area_mm2)), d.descricao]
            for j, valor in enumerate(valores):
                self.setItem(i, j, _celula(valor, dica=d.descricao))
        self.resizeRowsToContents()


# --- Etapas e histórico -------------------------------------------------------------------

class Etapas(QListWidget):
    """Miniaturas das imagens de cada etapa, pela ordem do pipeline; clicar mostra-a em grande."""

    escolhida = Signal(str, object)  # nome da etapa, imagem BGR

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(MINIATURA_PX, MINIATURA_PX * 3 // 4))
        self.setSpacing(4)
        self._etapas: list[tuple[str, np.ndarray]] = []
        self.itemClicked.connect(lambda item: self.escolhida.emit(*self._etapas[self.row(item)]))

    def mostrar(self, visto: Visto) -> None:
        self.clear()
        self._etapas = visto.etapas
        for (nome, _), miniatura in zip(visto.etapas, visto.miniaturas):
            self.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(qimagem(miniatura))), nome))


class Historico(QTableWidget):
    """Os últimos sacos (o mais recente em cima); clicar num volta a mostrá-lo."""

    escolhido = Signal(object)  # Visto, ou None se as imagens já saíram da memória

    def __init__(self) -> None:
        super().__init__()
        _preparar_tabela(self, ["Nº", "Hora", "Origem", "Estado", "Defeitos", "ms", "Decisão"], esticar=2)
        self._vistos: dict[int, Visto] = {}
        self.cellClicked.connect(lambda linha, _: self.escolhido.emit(self._vistos.get(self._numero(linha))))

    def limpar(self) -> None:
        self.setRowCount(0)
        self._vistos.clear()

    def acrescentar(self, visto: Visto) -> None:
        r = visto.resultado
        instante = r.captura.instante if r.captura else r.passagem.inicio if r.passagem else None
        hora = datetime.fromtimestamp(instante).strftime("%H:%M:%S") if instante else ""
        origem = Path(r.captura.origem).name if r.captura else r.mensagem
        tipos = ", ".join(f"{n} {t}" for t, n in sorted(r.contagem_tipos.items()))
        self.insertRow(0)
        numero = _celula(visto.numero)
        numero.setData(Qt.ItemDataRole.UserRole, visto.numero)
        for j, item in enumerate([numero, _celula(hora), _celula(origem, dica=r.mensagem), _celula(r.estado, r.estado),
                                  _celula(tipos), _celula(f"{visto.ciclo_ms:.0f}"), _celula("")]):
            self.setItem(0, j, item)
        self._vistos[visto.numero] = visto
        for antigo in [n for n in self._vistos if n <= visto.numero - HISTORICO_IMAGENS]:
            del self._vistos[antigo]
        if self.rowCount() > HISTORICO_LINHAS:
            self.removeRow(self.rowCount() - 1)

    def marcar_decisao(self, numero: int, decisao: str) -> None:
        for linha in range(self.rowCount()):
            if self._numero(linha) == numero:
                self.setItem(linha, 6, _celula(decisao))

    def _numero(self, linha: int) -> int:
        return self.item(linha, 0).data(Qt.ItemDataRole.UserRole)


# --- Log ------------------------------------------------------------------------------------

class _Ponte(QObject):
    texto = Signal(str)


class RegistoQt(logging.Handler):
    """Envia o log (de qualquer thread) para um PainelLog."""

    def __init__(self) -> None:
        super().__init__()
        self.ponte = _Ponte()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, registo: logging.LogRecord) -> None:
        self.ponte.texto.emit(self.format(registo))


class PainelLog(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setStyleSheet("font-family: Consolas, monospace;")
