"""Interface, login: a janela de entrada do BIapp. Sem utilizador e palavra-passe certos não se entra.

    ┌──────────────────── degradê dos azuis do logótipo ────────────────────┐
    │   ┌───────────────────────────────────────────────────────────────┐   │
    │   │                       │  Bem-vindo                            │   │
    │   │      [logótipo]       │  Utilizador     [______________]      │   │
    │   │                       │  Palavra-passe  [______________]      │   │
    │   │  Inspeção visual de   │  ☐ Mostrar palavra-passe              │   │
    │   │  sacos                │  [            Entrar            ]     │   │
    │   └───────────────────────────────────────────────────────────────┘   │
    └───────────────────────────────────────────────────────────────────────┘

Cada conta tem um papel: "admin" faz tudo; "operador" só vê a vista Operador
(iniciar/parar a produção, contadores, decidir os sacos em revisão) e escolhe,
cria ou edita a ordem de fabrico (OF), identificando-se com o nome e o nº.
O que cada papel pode fazer com as OFs e os produtos está em ACESSOS.

As palavras-passe não estão no código em texto: só o seu SHA-256. Para mudar
uma, calcular o novo valor e substituí-lo em CONTAS:

    python -c "import hashlib; print(hashlib.sha256(b'nova_palavra_passe').hexdigest())"

É um cadeado para o posto da linha, não uma proteção a sério: quem tem acesso
ao computador corre o main.py ou edita este ficheiro.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import NamedTuple

from PySide6.QtCore import QAbstractAnimation, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from interface.estilo import AZUL, AZUL_ESCURO, LOGO, NOME_APP

class Conta(NamedTuple):
    papel: str  # "admin" ou "operador"
    sha256: str  # da palavra-passe


CONTAS = {
    "admin": Conta("admin", "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"),  # "admin"
    "op": Conta("operador", "037aeaeaf4bbf26ddabe7256a8294dc52da48d575a1247b5c2598c47de7aebab"),  # "op"
}
# O que cada papel pode fazer (mostrado no menu da conta).
PERMISSOES = {
    "admin": "tudo: Ao vivo e Trigger, Engenharia, histórico, definições, câmara, dashboard, produtos e OFs",
    "operador": "vista Operador: iniciar e parar a produção, contadores, decidir os sacos em revisão, "
                "criar e editar a OF (com o nome e o nº)",
}
# Ordens de fabrico e produtos: o que cada papel pode fazer. Para dar ou tirar uma permissão, mudar aqui.
#   producao_sem_of  iniciar a produção sem OF (ensaios); quem não pode escolhe a OF a cada início
#   criar_of         criar uma OF e, se o produto for novo, a sua especificação
#   editar_of        mudar os parâmetros de uma OF (só dessa OF)
#   editar_produto   mudar ou duplicar a especificação de um produto que já existe
#   dashboard        ver o dashboard: OFs, produtos, contagens
ACESSOS = {
    "admin": {"producao_sem_of", "criar_of", "editar_of", "editar_produto", "dashboard"},
    "operador": {"criar_of", "editar_of"},
}
IDENTIFICAR = {"operador"}  # papéis que escrevem o nome e o nº de identificação ao criar ou editar uma OF

LOGO_ALTURA_PX = 250  # altura do logótipo no cartão

_ESTILO = f"""
QDialog#login {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b2747, stop:0.5 {AZUL_ESCURO}, stop:1 {AZUL});
}}
QFrame#cartao {{ background: white; border-radius: 16px; }}
QFrame#divisoria {{ background: #e3e8ec; }}
QLabel#titulo {{ color: {AZUL_ESCURO}; font-size: 22pt; font-weight: bold; }}
QLabel#marca {{ color: {AZUL_ESCURO}; font-size: 13pt; font-weight: bold; }}
QLabel#discreto {{ color: #78909c; }}
QLabel#rotulo {{ color: #37474f; font-weight: bold; }}
QLabel#erro {{ color: #c62828; }}
QFrame#cartao QLineEdit {{
    border: 1px solid #cfd8dc; border-radius: 8px; padding: 2px 12px; min-height: 38px;
    background: #f5f8fa; font-size: 12pt;
}}
QFrame#cartao QLineEdit:focus {{ border: 2px solid {AZUL}; background: white; }}
QCheckBox {{ color: #546e7a; }}
QPushButton#entrar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {AZUL}, stop:1 {AZUL_ESCURO});
    color: white; font-size: 13pt; font-weight: bold; border: none; border-radius: 8px; min-height: 48px;
}}
QPushButton#entrar:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a83b8, stop:1 #1a4f86);
}}
QPushButton#entrar:pressed {{ background: #0b2747; }}
QPushButton#sair {{ background: transparent; border: none; color: #78909c; min-height: 24px; }}
QPushButton#sair:hover {{ color: {AZUL_ESCURO}; text-decoration: underline; }}
"""


def pedir_login() -> str | None:
    """Mostra a janela de login; devolve o utilizador que entrou, ou None se desistiu."""
    app = QApplication.instance()
    fechar_ao_sair = app.quitOnLastWindowClosed()
    # Com a janela principal escondida (terminar sessão), fechar o login não pode fechar o programa.
    app.setQuitOnLastWindowClosed(False)
    try:
        login = JanelaLogin()
        return login.utilizador if login.exec() == QDialog.DialogCode.Accepted else None
    finally:
        app.setQuitOnLastWindowClosed(fechar_ao_sair)


def verificar(utilizador: str, palavra_passe: str) -> bool:
    """True se a palavra-passe é a do utilizador."""
    conta = CONTAS.get(utilizador.strip())
    obtido = hashlib.sha256(palavra_passe.encode("utf-8")).hexdigest()
    return conta is not None and hmac.compare_digest(conta.sha256, obtido)


def pode(papel: str, acao: str) -> bool:
    """True se o papel tem a permissão (ACESSOS), p.ex. pode("operador", "dashboard") → False."""
    return acao in ACESSOS.get(papel, set())


def assinatura(papel: str, utilizador: str, nome: str = "", numero: str = "") -> str:
    """Quem fica como autor de uma OF ou de um produto: o utilizador ou, se o papel tem de se
    identificar, "Nome (nº 123)". ValueError se falta o nome ou o nº."""
    if papel not in IDENTIFICAR:
        return utilizador
    nome, numero = nome.strip(), numero.strip()
    if not nome or not numero:
        raise ValueError("Escreve o teu nome e o teu nº de identificação.")
    return f"{nome} (nº {numero})"


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)  # o aspeto vem de _ESTILO
    return rotulo


class JanelaLogin(QDialog):
    """Pede o utilizador e a palavra-passe; aceita (exec() == Accepted) só se estiverem certos."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("login")
        self.setWindowTitle(f"{NOME_APP}")
        self.setStyleSheet(_ESTILO)
        self.utilizador = ""
        self._animacao: QPropertyAnimation | None = None

        # Esquerda: a marca.
        logo = QLabel()
        logo.setPixmap(QPixmap(str(LOGO)).scaledToHeight(LOGO_ALTURA_PX, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marca = QVBoxLayout()
        marca.addStretch()
        marca.addWidget(logo)
        marca.addSpacing(12)
        for texto, nome in (("Inspeção visual de sacos", "marca"), ("Ao vivo · Trigger · Afinação", "discreto")):
            rotulo = _rotulo(texto, nome)
            rotulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            marca.addWidget(rotulo)
        marca.addStretch()

        # Direita: o formulário.
        self.ed_utilizador = QLineEdit()
        self.ed_utilizador.setPlaceholderText("username")
        self.ed_palavra_passe = QLineEdit()
        self.ed_palavra_passe.setPlaceholderText("password")
        self.ed_palavra_passe.setEchoMode(QLineEdit.EchoMode.Password)
        mostrar = QCheckBox("Mostrar palavra-passe")
        mostrar.toggled.connect(lambda sim: self.ed_palavra_passe.setEchoMode(
            QLineEdit.EchoMode.Normal if sim else QLineEdit.EchoMode.Password))
        self.erro = _rotulo("", "erro")
        self.erro.setMinimumHeight(24)  # o espaço do erro já está reservado: o cartão não salta
        btn_entrar = QPushButton("Entrar")
        btn_entrar.setObjectName("entrar")
        btn_entrar.setDefault(True)  # Enter entra
        btn_entrar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_entrar.clicked.connect(self._entrar)
        btn_sair = QPushButton("Sair do programa")
        btn_sair.setObjectName("sair")
        btn_sair.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sair.clicked.connect(self.reject)

        formulario = QVBoxLayout()
        formulario.setSpacing(6)
        formulario.addWidget(_rotulo("Bem-vindo", "titulo"))
        formulario.addWidget(_rotulo(f"Entra no {NOME_APP} com o teu utilizador", "discreto"))
        formulario.addSpacing(18)
        for texto, campo in (("Utilizador", self.ed_utilizador), ("Palavra-passe", self.ed_palavra_passe)):
            formulario.addWidget(_rotulo(texto, "rotulo"))
            formulario.addWidget(campo)
            formulario.addSpacing(8)
        formulario.addWidget(mostrar)
        formulario.addWidget(self.erro)
        formulario.addWidget(btn_entrar)
        formulario.addWidget(btn_sair, 0, Qt.AlignmentFlag.AlignHCenter)

        divisoria = QFrame()
        divisoria.setObjectName("divisoria")
        divisoria.setFixedWidth(1)
        self.cartao = QFrame()
        self.cartao.setObjectName("cartao")
        sombra = QGraphicsDropShadowEffect(self.cartao)
        sombra.setBlurRadius(40)
        sombra.setOffset(0, 10)
        sombra.setColor(QColor(0, 0, 0, 110))
        self.cartao.setGraphicsEffect(sombra)
        conteudo = QHBoxLayout(self.cartao)
        conteudo.setContentsMargins(36, 32, 40, 28)
        conteudo.setSpacing(36)
        conteudo.addLayout(marca, 1)
        conteudo.addWidget(divisoria)
        conteudo.addLayout(formulario, 1)

        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 24)
        layout.addWidget(self.cartao)
        layout.addSpacing(16)
        self.cartao.setFixedWidth(820)
        self.setFixedSize(self.sizeHint())
        self.ed_utilizador.setFocus()

    def _entrar(self) -> None:
        utilizador, palavra_passe = self.ed_utilizador.text().strip(), self.ed_palavra_passe.text()
        if not utilizador:
            self.erro.setText("Escreve o utilizador.")
            self.ed_utilizador.setFocus()
            return
        if not palavra_passe:  # Enter no utilizador: passa à palavra-passe
            self.erro.clear()
            self.ed_palavra_passe.setFocus()
            return
        if verificar(utilizador, palavra_passe):
            self.utilizador = utilizador
            self.accept()
            return
        self.erro.setText("Utilizador ou palavra-passe errados.")
        self.ed_palavra_passe.clear()
        self.ed_palavra_passe.setFocus()
        self._abanar()

    def _abanar(self) -> None:
        """Abana o cartão para os lados, como quem diz que não."""
        if self._animacao is not None and self._animacao.state() == QAbstractAnimation.State.Running:
            return
        x, y = self.cartao.x(), self.cartao.y()
        self._animacao = QPropertyAnimation(self.cartao, b"pos", self)
        self._animacao.setDuration(380)
        deslocamentos = (0, -14, 14, -9, 9, -4, 4, 0)
        for i, dx in enumerate(deslocamentos):
            self._animacao.setKeyValueAt(i / (len(deslocamentos) - 1), QPoint(x + dx, y))
        self._animacao.start()
