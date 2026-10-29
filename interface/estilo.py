"""Interface, estilo: a marca (nome, logótipo, cores), o aspeto comum (ESTILO) e os ícones."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from nucleo.contrato import Estado


CORES_ESTADO = {
    Estado.OK: "#2e7d32",  # verde
    Estado.NOK: "#c62828",  # vermelho
    Estado.ERRO: "#ef6c00",  # laranja
    Estado.SEM_SACO: "#616161",  # cinzento
    Estado.POR_IMPLEMENTAR: "#78909c",  # cinzento azulado
}

# A marca: o nome, o logótipo (fundo transparente) e o ícone das janelas.
NOME_APP = "BIapp"
RECURSOS = Path(__file__).resolve().parent / "recursos"
LOGO, ICONE = RECURSOS / "biapp.png", RECURSOS / "biapp_icone.png"
AZUL_ESCURO, AZUL = "#123B66", "#2C6E9C"  # as duas cores do logótipo

# Aplicado a toda a aplicação (gui.py). Os botões com nome (setObjectName) têm cor própria.
ESTILO = """
QWidget { font-size: 11pt; color: #263238; }
QMainWindow, QWidget#fundo { background: #f2f5f8; }
QFrame#barra { background: white; border-radius: 10px; }

QPushButton, QToolButton {
    min-height: 40px; padding: 2px 14px; border: none; border-radius: 8px; background: #e9eef2;
}
QPushButton:hover, QToolButton:hover { background: #dbe5ee; }
QToolButton::menu-indicator { image: none; }
QPushButton:checked { background: #2C6E9C; color: white; font-weight: bold; }
QPushButton:disabled, QToolButton:disabled { color: #b0bec5; background: #f0f3f6; }
QPushButton#iniciar { background: #2e7d32; color: white; font-weight: bold; }
QPushButton#parar { background: #c62828; color: white; font-weight: bold; }
QPushButton#iniciar:disabled, QPushButton#parar:disabled { background: #e3e8ec; color: #b0bec5; }
QPushButton#segmento_esq { border-top-right-radius: 0; border-bottom-right-radius: 0; }
QPushButton#segmento_dir { border-top-left-radius: 0; border-bottom-left-radius: 0; }

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    min-height: 32px; border: 1px solid #d5dde3; border-radius: 6px; padding: 0 8px; background: white;
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #2C6E9C; }

QTableWidget, QListWidget, QPlainTextEdit, QScrollArea {
    background: white; border: none; border-radius: 8px; alternate-background-color: #f7f9fb;
}
QHeaderView::section {
    background: white; border: none; border-bottom: 1px solid #e3e8ec; padding: 4px 8px;
    color: #607d8b; font-weight: bold;
}
QTabWidget::pane { border: none; background: white; border-radius: 8px; }
QTabBar::tab { background: transparent; border: none; min-height: 34px; padding: 2px 20px; color: #78909c; }
QTabBar::tab:selected { color: #123B66; font-weight: bold; border-bottom: 3px solid #2C6E9C; }
QLabel#cabecalho { color: #78909c; font-weight: bold; }
QStatusBar { background: white; color: #78909c; }

QSplitter::handle { background: #dde4ea; border-radius: 3px; }
QSplitter::handle:hover { background: #2C6E9C; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
"""


# --- Ícones -----------------------------------------------------------------------------------

# Desenhos dos ícones (SVG, 24 × 24, estilo Material): definições, conta e câmara.
_ICONES = {
    "definicoes": '<path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61'
                  'l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54'
                  'c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94'
                  'l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94'
                  's.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96'
                  'c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54'
                  'c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61'
                  'l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>',
    "conta": '<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2'
             'c0-2.66-5.33-4-8-4z"/>',
    "camara": '<circle cx="12" cy="12" r="3.2"/><path d="M9 2 7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16'
              'c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5'
              '-2.24 5-5 5z"/>',
}


@cache
def icone(nome: str, cor: str = AZUL_ESCURO) -> QIcon:
    """Um dos ícones de _ICONES, na cor dada, nítido em qualquer tamanho."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{cor}">{_ICONES[nome]}</svg>'
    desenho = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    resultado = QIcon()
    for lado in (16, 24, 32, 48, 64):
        imagem = QPixmap(lado, lado)
        imagem.fill(Qt.GlobalColor.transparent)
        pintor = QPainter(imagem)
        desenho.render(pintor)
        pintor.end()
        resultado.addPixmap(imagem)
    return resultado
