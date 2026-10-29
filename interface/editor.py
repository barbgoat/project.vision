"""Interface, editor: os parâmetros de umas secções da configuração, um campo por parâmetro.

Usado nas janelas ⚙ Definições e 📷 Câmara. Cada campo tem como dica o define e o
comentário de nucleo/defines.py.
"""

from __future__ import annotations

import dataclasses
import html
import re
import typing
from functools import cache
from typing import Any, Literal

from PySide6.QtCore import QEvent, QLocale, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from interface.estilo import AZUL
from nucleo.config import AFINACAO, RAIZ, Config, nome_define


# O que se muda em cada janela: definições (roda dentada) e câmara.
SECOES_DEFINICOES = ("geral", "localizacao", "dimensoes", "cor", "relevo", "manchas", "saida")
SECOES_CAMARA = ("aquisicao", "disparo", "lente")
NOMES_SECOES = {
    "geral": "Geral (px/mm, orçamento)", "localizacao": "M2 Localização", "dimensoes": "M3 Dimensões",
    "cor": "M4 Cor", "relevo": "M5 Relevo", "manchas": "M6 Manchas", "saida": "M7 Saída (imagens, registos)",
    "aquisicao": "Exposição e ganho", "disparo": "Trigger (disparo)", "lente": "Lente",
}


@cache
def ajudas() -> dict[str, str]:
    """O comentário de cada define em nucleo/defines.py ("" se não tiver).

    É o da própria linha ou, se não tiver, o bloco de comentários por cima,
    que vale para todos os defines seguidos sem comentário próprio (p.ex. os
    limites do M2, debaixo de "# Limites LARGOS...").
    """
    textos: dict[str, str] = {}
    bloco: list[str] = []
    herdar = depois_de_define = False
    for linha in (RAIZ / "nucleo" / "defines.py").read_text(encoding="utf-8").splitlines():
        if define := re.match(r"([A-Z0-9_]+): Final = .*?(?:#\s*(.*))?$", linha):
            if define[2]:
                textos[define[1]], herdar = define[2], False
            else:
                textos[define[1]] = " ".join(bloco) if herdar else ""
            depois_de_define = True
        elif linha.startswith("#"):
            if re.match(r"#\s*(=+|-{3}.*)$", linha):  # separadores e títulos com ---
                continue
            if depois_de_define or not herdar:
                bloco = []
            bloco.append(linha.lstrip("#").strip())
            herdar, depois_de_define = True, False
        else:
            bloco, herdar, depois_de_define = [], False, False
    return textos


def diferencas(antes: Config, depois: Config) -> list[str]:
    """O que mudou entre duas configurações, p.ex. "dimensoes.tolerancia_comprimento_mm: 3.0 → 4.0"."""
    a, d = dataclasses.asdict(antes), dataclasses.asdict(depois)
    return [f"{s}.{n}: {a[s][n]!r} → {d[s][n]!r}" for s in d for n in d[s] if a[s][n] != d[s][n]]


class _SemRoda(QObject):
    """A roda do rato só muda um campo depois de se clicar nele; antes disso, rola a página."""

    def eventFilter(self, objeto: QObject, evento: QEvent) -> bool:
        if evento.type() == QEvent.Type.Wheel and not objeto.hasFocus():
            evento.ignore()  # ignorado, passa para a área que rola
            return True
        return False


@cache
def _sem_roda() -> _SemRoda:
    return _SemRoda()  # criado só quando já há a QApplication


class _Campo:
    """Um parâmetro no editor: o widget e como ler e escrever o seu valor."""

    def __init__(self, secao: str, nome: str, tipo: Any, valor: Any) -> None:
        self.secao, self.nome = secao, nome
        self.rotulo = QLabel(nome)
        define = nome_define(secao, nome)
        dica = f"<b>{define}</b><br>{html.escape(ajudas().get(define, ''))}"
        self.rotulo.setToolTip(dica)
        if typing.get_origin(tipo) is Literal:
            self.widget = QComboBox()
            self.widget.addItems([str(o) for o in typing.get_args(tipo)])
        elif tipo is bool:
            self.widget = QCheckBox()
        elif tipo is int:
            self.widget = QSpinBox()
            self.widget.setRange(0, 10**6)
        elif tipo is float:
            self.widget = QDoubleSpinBox()
            # O passo das setas: o da gama de afinação, se lá estiver; senão, a última casa decimal do valor.
            passo = next((p for n, _, _, p in AFINACAO.get(secao, []) if n == nome), None)
            casas = _casas(passo or valor)
            self.widget.setDecimals(casas)
            self.widget.setRange(-10**6 if valor < 0 else 0, 10**6)
            self.widget.setSingleStep(passo or 10**-casas)
        else:  # str e list[float]: texto (listas como "70, 0, 0")
            self.widget = QLineEdit()
            self.widget.setMinimumWidth(220)
        if isinstance(self.widget, (QSpinBox, QDoubleSpinBox)):
            self.widget.setKeyboardTracking(False)  # só ao carregar Enter, nas setas ou ao sair do campo
            self.widget.setLocale(QLocale.c())  # ponto decimal, como no defines.py e nos JSON
        if isinstance(self.widget, (QSpinBox, QDoubleSpinBox, QComboBox)):
            self.widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.widget.installEventFilter(_sem_roda())
        self.widget.setToolTip(dica)
        self.lista = typing.get_origin(tipo) is list

    def ler(self) -> Any:
        w = self.widget
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return w.value()
        if self.lista:
            try:
                return [float(v) for v in w.text().replace(";", ",").split(",") if v.strip()]
            except ValueError:
                raise ValueError(f"{self.secao}.{self.nome}: escrever números separados por vírgulas") from None
        return w.text()

    def escrever(self, valor: Any) -> None:
        w = self.widget
        w.blockSignals(True)
        if isinstance(w, QComboBox):
            w.setCurrentText(str(valor))
        elif isinstance(w, QCheckBox):
            w.setChecked(valor)
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setValue(valor)
        else:
            w.setText(", ".join(f"{v:g}" for v in valor) if self.lista else str(valor))
        w.blockSignals(False)

    def ao_mudar(self, funcao) -> None:
        w = self.widget
        if isinstance(w, QComboBox):
            sinal = w.currentTextChanged
        elif isinstance(w, QCheckBox):
            sinal = w.toggled
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            sinal = w.valueChanged
        else:
            sinal = w.editingFinished
        sinal.connect(lambda *_: funcao())


def _casas(numero: float) -> int:
    """Casas decimais para mostrar um passo ou um valor, p.ex. 0.005 → 3, 12.4 → 1, 150 → 1 (máx. 4)."""
    return max(1, len(f"{abs(numero):.4f}".rstrip("0").split(".")[1]))


class EditorParametros(QWidget):
    """Os parâmetros das `secoes` dadas (uma página por secção), gerados a partir da configuração.

    Cada campo tem como dica o define e o comentário de nucleo/defines.py. A
    azul: os valores diferentes do perfil carregado (o que "Guardar" escreve).
    Uma configuração inválida (p.ex. limiar baixo > alto) não é emitida: o
    erro aparece a vermelho até ser corrigida. As outras secções vêm da última
    configuração dada a definir(): dois editores podem partilhar a mesma.
    """

    mudou = Signal(object)  # Config válida, depois de cada alteração
    aplicar = Signal()
    repor = Signal()
    guardar = Signal()

    def __init__(self, secoes: tuple[str, ...] = SECOES_DEFINICOES) -> None:
        super().__init__()
        self._secoes = secoes
        self._perfil = Config()  # a configuração do perfil carregado
        self._cfg = self._perfil  # a última configuração válida
        self._campos: list[_Campo] = []
        self.secao = QComboBox()
        self.nome_perfil = QLabel()
        self.erro = QLabel()
        self.erro.setStyleSheet("color: #c62828;")
        self.erro.setWordWrap(True)
        self.btn_aplicar = QPushButton("Aplicar à produção")
        self.btn_aplicar.setToolTip("Os sacos seguintes passam a usar estes valores.")
        btn_repor = QPushButton("Repor o perfil")
        btn_guardar = QPushButton("Guardar no perfil")
        self.btn_aplicar.clicked.connect(self.aplicar)
        btn_repor.clicked.connect(self.repor)
        btn_guardar.clicked.connect(self.guardar)

        paginas = QStackedWidget()
        cfg = Config()
        for secao in secoes:
            classe = type(getattr(cfg, secao))
            tipos = typing.get_type_hints(classe)
            grelha = QGridLayout()
            campos = [_Campo(secao, f.name, tipos[f.name], getattr(getattr(cfg, secao), f.name))
                      for f in dataclasses.fields(classe)]
            metade = (len(campos) + 1) // 2  # duas colunas de (nome, valor)
            for i, campo in enumerate(campos):
                grelha.addWidget(campo.rotulo, i % metade, 2 * (i // metade))
                grelha.addWidget(campo.widget, i % metade, 2 * (i // metade) + 1)
                campo.ao_mudar(self._alterado)
            grelha.setColumnMinimumWidth(2, 24)  # espaço entre as duas colunas
            grelha.setColumnStretch(4, 1)  # o que sobra fica à direita e em baixo
            grelha.setRowStretch(metade, 1)
            pagina = QWidget()
            pagina.setLayout(grelha)
            rolar = QScrollArea()
            rolar.setWidgetResizable(True)
            rolar.setWidget(pagina)
            paginas.addWidget(rolar)
            self.secao.addItem(NOMES_SECOES[secao], secao)
            self._campos += campos
        self.secao.currentIndexChanged.connect(paginas.setCurrentIndex)

        topo = QHBoxLayout()
        for w in (QLabel("Secção:"), self.secao, self.nome_perfil):
            topo.addWidget(w)
        topo.addStretch()
        for w in (self.btn_aplicar, btn_repor, btn_guardar):
            topo.addWidget(w)
        layout = QVBoxLayout(self)
        layout.addLayout(topo)
        layout.addWidget(self.erro)
        layout.addWidget(paginas, 1)

    def definir(self, cfg: Config, perfil: Config | None = None, nome_perfil: str = "") -> None:
        """Mostra os valores de `cfg`; `perfil` é a referência para marcar o que mudou."""
        if perfil is not None:
            self._perfil = perfil
        if nome_perfil:
            self.nome_perfil.setText(f"perfil: {nome_perfil}")
        self._cfg = cfg
        for campo in self._campos:
            campo.escrever(getattr(getattr(cfg, campo.secao), campo.nome))
        self.erro.clear()
        self._marcar()

    def cfg(self) -> Config:
        """A última configuração válida."""
        return self._cfg

    def _alterado(self) -> None:
        try:
            valores: dict[str, dict[str, Any]] = {s: {} for s in self._secoes}
            for campo in self._campos:
                valores[campo.secao][campo.nome] = campo.ler()
            nova = dataclasses.replace(self._cfg, **{
                s: dataclasses.replace(getattr(self._cfg, s), **v) for s, v in valores.items()})
        except ValueError as erro:
            self.erro.setText(str(erro))
            return
        self.erro.clear()
        self._cfg = nova
        self._marcar()
        self.mudou.emit(nova)

    def _marcar(self) -> None:
        for campo in self._campos:
            mudou = getattr(getattr(self._cfg, campo.secao), campo.nome) != \
                getattr(getattr(self._perfil, campo.secao), campo.nome)
            campo.rotulo.setStyleSheet(f"color: {AZUL}; font-weight: bold;" if mudou else "")
