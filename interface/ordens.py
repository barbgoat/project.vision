"""Interface, ordens de fabrico: escolher a OF ao iniciar, o formulário do produto e o dashboard.

    DialogoOrdem          ao iniciar a produção: a OF e/ou o produto (o operador
                          identifica-se com o nome e o nº para criar ou editar)
    DialogoEspecificacao  o formulário de um produto ou de uma OF: comprimento e largura
                          com as tolerâncias, cor predominante e verniz
    JanelaDashboard       o dashboard (interface/vista_dashboard.py) com os botões para
                          criar, editar e duplicar; atualiza-se sozinho
    Ordens                os métodos da Janela (janela.py) sobre a OF em uso

Aqui só há a parte gráfica. O que o dashboard mostra e como se criam e mudam os
produtos e as OFs está em nucleo/dashboard.py; a base de dados, em
nucleo/especificacoes.py. O que cada papel pode fazer está em interface/login.py
(ACESSOS). A OF em uso compõe a configuração: montagem → produto → OF.
"""

from __future__ import annotations

import dataclasses
import html

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from interface.estilo import NOME_APP
from interface.janelas import JanelaAuxiliar
from interface.login import IDENTIFICAR, assinatura, pode
from interface.vista_dashboard import VistaDashboard
from nucleo import dashboard
from nucleo.especificacoes import (
    MEDIDAS, ErroRepositorio, Escolha, Especificacao, OrdemFabrico, Repositorio, caminho_base_dados, compor,
    efetiva, repor_medidas, resolver,
)


_ERRO = "color: #c62828;"


# --- Ao iniciar: a OF e/ou o produto --------------------------------------------------------

class DialogoOrdem(QDialog):
    """A OF e/ou o produto com que se vai produzir. Cria a OF (e o produto, se for novo) quando é preciso.

    Aceite: `escolha` tem o produto e a OF (None = sem OF, só quem pode).
    """

    def __init__(self, janela: Ordens, repo: Repositorio) -> None:
        super().__init__(janela)
        self.setWindowTitle(f"{NOME_APP}: ordem de fabrico")
        self.janela, self.repo = janela, repo
        self.escolha: Escolha | None = None
        of, produto = janela._identidade()
        self.ed_of = QLineEdit(of)
        self.ed_of.setPlaceholderText("número da OF")
        self.cb_produto = QComboBox()
        self.cb_produto.setEditable(True)
        self.cb_produto.addItems([p.produto for p in repo.produtos()])
        self.cb_produto.setCurrentText(produto)
        self.cb_produto.lineEdit().setPlaceholderText("nome do saco")
        self.ed_nome, self.ed_numero = QLineEdit(), QLineEdit()
        self.estado = QLabel()
        self.estado.setWordWrap(True)
        self.estado.setMinimumHeight(48)

        grelha = QGridLayout()
        linhas = [("Ordem de fabrico", self.ed_of), ("Produto", self.cb_produto)]
        if janela.papel in IDENTIFICAR:
            linhas += [("O teu nome", self.ed_nome), ("Nº de identificação", self.ed_numero)]
        for i, (texto, campo) in enumerate(linhas):
            grelha.addWidget(QLabel(texto), i, 0)
            grelha.addWidget(campo, i, 1)

        self.btn_continuar = QPushButton("Continuar")
        self.btn_continuar.setObjectName("iniciar")  # verde (ESTILO)
        self.btn_continuar.setDefault(True)
        self.btn_editar = QPushButton("Editar a OF…")
        self.btn_editar.setToolTip("Mudar os parâmetros desta OF (só desta OF, não do produto)")
        cancelar = QPushButton("Cancelar")
        botoes = QHBoxLayout()
        if janela._pode("producao_sem_of"):
            sem_of = QPushButton("Sem OF (ensaio)")
            sem_of.clicked.connect(self.accept)  # escolha = None
            botoes.addWidget(sem_of)
        botoes.addStretch()
        for botao in (self.btn_editar, cancelar, self.btn_continuar):
            botoes.addWidget(botao)
        self.btn_continuar.clicked.connect(self._continuar)
        self.btn_editar.clicked.connect(self._editar)
        cancelar.clicked.connect(self.reject)
        self.ed_of.textChanged.connect(self._ver)
        self.cb_produto.currentTextChanged.connect(self._ver)

        layout = QVBoxLayout(self)
        layout.addLayout(grelha)
        layout.addWidget(self.estado)
        layout.addLayout(botoes)
        self.resize(560, 0)
        self._ver()

    def _resolver(self) -> Escolha:
        return resolver(self.repo, self.ed_of.text(), self.cb_produto.currentText())

    def _ver(self) -> None:
        """O que vai acontecer com o que está escrito."""
        self.btn_editar.setEnabled(False)
        try:
            e = self._resolver()
        except (ValueError, ErroRepositorio) as erro:
            self.estado.setText(f"<span style='{_ERRO}'>{html.escape(str(erro))}</span>")
            return
        if e.produto is None:
            texto = "Produto novo: é preciso criar a especificação."
            if not self.janela._pode("criar_of"):
                texto += " Pede ao administrador."
        elif e.of is None:
            texto = f"Produto <b>{html.escape(e.produto.produto)}</b>, sem OF."
        elif e.nova:
            texto = (f"OF nova para <b>{html.escape(e.produto.produto)}</b>, com as últimas especificações "
                     "usadas neste produto.")
        else:
            texto = (f"OF <b>{html.escape(e.of.numero)}</b>: <b>{html.escape(e.produto.produto)}</b> "
                     f"(alterada em {e.of.data_hora} por {html.escape(e.of.autor)}).")
        self.btn_editar.setEnabled(e.produto is not None and e.of is not None and self.janela._pode("editar_of"))
        self.estado.setText(texto)

    def _autor(self) -> str:
        return assinatura(self.janela.papel, self.janela.utilizador, self.ed_nome.text(), self.ed_numero.text())

    def _continuar(self) -> None:
        try:
            e = self._resolver()
            if e.nova and not self.janela._pode("criar_of"):
                raise ValueError("Não tens permissão para criar OFs nem produtos: pede ao administrador.")
            if e.produto is None:  # produto novo: o formulário cria o produto e a OF
                formulario = DialogoEspecificacao(self, self.janela, "novo", self._autor(), of=e.of,
                                                  nome=self.cb_produto.currentText())
                if formulario.exec() != QDialog.DialogCode.Accepted:
                    return
                e = Escolha(formulario.produto, formulario.of)
            elif e.nova and e.of is not None:
                e = dashboard.criar_of(self.repo, e, self._autor())
        except (ValueError, ErroRepositorio) as erro:
            self.estado.setText(f"<span style='{_ERRO}'>{html.escape(str(erro))}</span>")
            return
        self.escolha = e
        self.accept()

    def _editar(self) -> None:
        try:
            e = self._resolver()
            formulario = DialogoEspecificacao(self, self.janela, "of", self._autor(), produto=e.produto, of=e.of)
        except (ValueError, ErroRepositorio) as erro:
            self.estado.setText(f"<span style='{_ERRO}'>{html.escape(str(erro))}</span>")
            return
        if formulario.exec() == QDialog.DialogCode.Accepted:
            self.escolha = Escolha(formulario.produto, formulario.of)
            self.accept()


# --- O formulário do produto (ou da OF) ----------------------------------------------------

class DialogoEspecificacao(QDialog):
    """O formulário de um produto, ou de uma OF: comprimento e largura com as tolerâncias, cor e verniz
    e, só no produto, a resolução da localização (0 = a do perfil; a OF não a muda).

    modo:
        "novo"     um produto novo (e a OF, se tiver número); para duplicar, parte de `produto`
        "produto"  uma versão nova de um produto que já existe
        "of"       o que esta OF muda no seu produto (parte do produto com a OF por cima)
    Os cantos, a esquadria e o resto da inspeção vêm do perfil da montagem. Aceite: `produto`
    e `of` guardados.
    """

    def __init__(self, pai: QWidget, janela: Ordens, modo: str, autor: str, produto: Especificacao | None = None,
                 of: OrdemFabrico | None = None, nome: str = "") -> None:
        super().__init__(pai)
        self.janela, self.modo, self.autor = janela, modo, autor
        self.repo = janela._repositorio()
        self.produto, self.of = produto, of
        if modo == "of":
            inicial = efetiva(produto, of)
        elif produto is not None:
            inicial = produto
        else:  # produto novo: as medidas da configuração em uso, para começar
            dimensoes = janela._cfg_edicao.dimensoes
            inicial = Especificacao(nome, **{c: getattr(dimensoes, c) for c in MEDIDAS})
        titulos = {"novo": "produto novo", "produto": f"produto {produto.produto if produto else ''}",
                   "of": f"OF {of.numero if of else ''}"}
        self.setWindowTitle(f"{NOME_APP}: {titulos[modo]}")

        self.ed_produto = QLineEdit(nome if modo == "novo" else inicial.produto)
        self.ed_produto.setReadOnly(modo != "novo")
        self.ed_of = QLineEdit(of.numero if of else "")
        self.ed_of.setPlaceholderText("opcional")
        self.ed_of.setReadOnly(modo == "of")
        self.medidas = {c: _milimetros(getattr(inicial, c)) for c in MEDIDAS}
        self.ed_cor = QLineEdit(inicial.cor)
        self.ed_cor.setPlaceholderText("p.ex. branco, azul-escuro")
        self.verniz = QCheckBox("tem verniz")
        self.verniz.setChecked(inicial.verniz)
        for w in (self.ed_cor, self.verniz):
            w.setToolTip("Fica guardado, mas por agora não é verificado.")
        self._inicial = inicial
        perfil = janela._cfg_perfil
        self.resolucao = QDoubleSpinBox()
        self.resolucao.setRange(0, 100)
        self.resolucao.setDecimals(1)
        self.resolucao.setSingleStep(0.5)
        self.resolucao.setSpecialValueText(f"do perfil ({perfil.localizacao.resolucao_px_por_mm:g})")
        self.resolucao.setValue(inicial.resolucao_localizacao_px_por_mm or 0.0)
        self.resolucao.setToolTip(
            "20 = sem redução (brilhantes); 5 = mais rápido (mate). Valores ≥ resolução da câmara "
            f"({perfil.geral.px_por_mm:.1f} px/mm) equivalem a sem redução. 0 = a do perfil da montagem.")

        grelha = QGridLayout()
        linhas = [("Produto", self.ed_produto)] + ([("Ordem de fabrico", self.ed_of)] if modo != "produto" else [])
        linhas += [("Comprimento (mm)", "comprimento"), ("Largura (mm)", "largura"),
                   ("Cor predominante", self.ed_cor), ("Verniz", self.verniz)]
        if modo != "of":  # é do produto: a OF não a muda
            linhas.append(("Resolução da localização (px/mm)", self.resolucao))
        for linha, (texto, campo) in enumerate(linhas):
            rotulo = QLabel(texto)
            if campo is self.resolucao:
                rotulo.setToolTip(self.resolucao.toolTip())
            grelha.addWidget(rotulo, linha, 0)
            if isinstance(campo, str):  # nominal ± tolerância
                grelha.addWidget(self.medidas[f"{campo}_nominal_mm"], linha, 1)
                grelha.addWidget(QLabel("±"), linha, 2)
                grelha.addWidget(self.medidas[f"tolerancia_{campo}_mm"], linha, 3)
            else:
                grelha.addWidget(campo, linha, 1, 1, 3)
        self.erro = QLabel()
        self.erro.setStyleSheet(_ERRO)
        self.erro.setWordWrap(True)
        guardar = QPushButton("Guardar")
        guardar.setObjectName("iniciar")
        guardar.setDefault(True)
        guardar.clicked.connect(self._guardar)
        cancelar = QPushButton("Cancelar")
        cancelar.clicked.connect(self.reject)
        botoes = QHBoxLayout()
        botoes.addStretch()
        botoes.addWidget(cancelar)
        botoes.addWidget(guardar)
        layout = QVBoxLayout(self)
        layout.addLayout(grelha)
        layout.addWidget(self.erro)
        layout.addLayout(botoes)
        self.resize(560, 0)

    def _lido(self) -> Especificacao:
        """O que está escrito no formulário."""
        resolucao = (self.resolucao.value() or None) if self.modo != "of" else self._inicial.resolucao_localizacao_px_por_mm
        return Especificacao(self.ed_produto.text(), **{c: campo.value() for c, campo in self.medidas.items()},
                             cor=self.ed_cor.text(), verniz=self.verniz.isChecked(),
                             resolucao_localizacao_px_por_mm=resolucao, autor=self.autor)

    def _guardar(self) -> None:
        montagem, lido = self.janela._cfg_edicao, self._lido()
        try:
            if self.modo == "of":
                self.of = dashboard.guardar_of(self.repo, montagem, self.ed_of.text(), self.produto, lido, self.autor)
            elif self.modo == "produto":
                self.produto = dashboard.guardar_produto(self.repo, montagem,
                                                         dataclasses.replace(lido, produto=self.produto.produto))
            else:
                self.produto, self.of = dashboard.criar_produto(self.repo, montagem, lido, self.ed_of.text())
        except (ValueError, ErroRepositorio) as erro:
            self.erro.setText(str(erro))
            return
        self.accept()


def _milimetros(valor: float) -> QDoubleSpinBox:
    campo = QDoubleSpinBox()
    campo.setRange(0, 2000)
    campo.setDecimals(1)
    campo.setSingleStep(0.5)
    campo.setLocale(QLocale.c())  # ponto decimal, como no resto da aplicação
    campo.setValue(valor)
    return campo


# --- Dashboard ------------------------------------------------------------------------------

class JanelaDashboard(JanelaAuxiliar):
    """O dashboard na BIapp: a vista (interface/vista_dashboard.py) com os botões de editar.

    Atualiza-se sozinho: com a janela aberta, vê a cada saida.dashboard_intervalo_ms se a
    base de dados mudou (Repositorio.versao) e só então refaz o estado.
    """

    def __init__(self, janela: Ordens, icone_janela) -> None:
        self.janela = janela
        self.vista = VistaDashboard()
        editar_of = QPushButton("Editar a OF…")
        editar_of.clicked.connect(self._editar_of)
        self.vista.barra_of.addWidget(editar_of)
        for texto, funcao in (("Novo produto…", self._novo), ("Duplicar…", self._duplicar),
                              ("Ver / editar…", self._editar_produto)):
            botao = QPushButton(texto)
            botao.clicked.connect(funcao)
            self.vista.barra_produtos.addWidget(botao)
        super().__init__(janela, "Dashboard", icone_janela, self.vista, (1150, 820))
        self.vista.tabela_produtos.doubleClicked.connect(self._editar_produto)
        self.vista.reiniciar_pedido.connect(self.atualizar)
        self.vista.limpar_pedido.connect(self._limpar)
        self._versao: tuple | None = None
        self._relogio = QTimer(self)
        self._relogio.timeout.connect(self._vigiar)

    def abrir(self) -> None:
        saida = self.janela._cfg_perfil.saida
        self.vista.permitir_limpar(saida.dashboard_limpar)
        self.atualizar()
        self._relogio.start(saida.dashboard_intervalo_ms)
        super().abrir()

    def hideEvent(self, evento) -> None:
        self._relogio.stop()  # fechada não gasta nada
        super().hideEvent(evento)

    def atualizar(self) -> None:
        """Volta a ler tudo da base de dados."""
        try:
            repo = self.janela._repositorio()
            self._versao = repo.versao()
            self.vista.mostrar(dashboard.estado(repo))
        except ErroRepositorio as erro:
            self.vista.erro(str(erro))

    def _limpar(self, tudo: bool) -> None:
        """Apaga os dados (já confirmado na vista); sem produtos, a BIapp deixa de ter OF em uso."""
        try:
            apagados = self.janela._repositorio().limpar(tudo)
        except ErroRepositorio as erro:
            self.vista.erro(str(erro))
            return
        if tudo and self.janela.ciclo is None:
            self.janela._usar_ordem(None)
        self.janela._mensagem(f"Dashboard: {apagados} registo(s) apagado(s)"
                              f"{' (inspeções, OFs e produtos)' if tudo else ' (inspeções)'}.")
        self.atualizar()

    def _vigiar(self) -> None:
        try:
            mudou = self.janela._repositorio().versao() != self._versao
        except ErroRepositorio as erro:
            self.vista.erro(str(erro))
            return
        if mudou:
            self.atualizar()

    # --- Editar ----------------------------------------------------------------------
    def _formulario(self, modo: str, produto: Especificacao | None = None, of: OrdemFabrico | None = None,
                    nome: str = "") -> None:
        try:
            formulario = DialogoEspecificacao(self, self.janela, modo, self.janela.utilizador, produto, of, nome)
        except (ValueError, ErroRepositorio) as erro:
            QMessageBox.warning(self, "Dashboard", str(erro))
            return
        if formulario.exec() == QDialog.DialogCode.Accepted:
            self.janela._recarregar_ordem()  # se for a OF (ou o produto) em uso, entra no próximo saco
            self.atualizar()

    def _editar_of(self) -> None:
        numero = self.vista.of_escolhida()
        repo = self.janela._repositorio()
        of = repo.procurar_of(numero) if numero else None
        if of is not None:
            self._formulario("of", repo.procurar_produto(of.produto), of)

    def _editar_produto(self) -> None:
        nome = self.vista.produto_escolhido()
        produto = self.janela._repositorio().procurar_produto(nome) if nome else None
        if produto is not None:
            self._formulario("produto", produto)

    def _novo(self) -> None:
        self._formulario("novo")

    def _duplicar(self) -> None:
        nome = self.vista.produto_escolhido()
        produto = self.janela._repositorio().procurar_produto(nome) if nome else None
        if produto is None:
            return
        nome, ok = QInputDialog.getText(self, "Duplicar", f"Nome do produto novo (cópia de {produto.produto}):")
        if ok and nome.strip():
            self._formulario("novo", produto, nome=nome.strip())


# --- Métodos da Janela ----------------------------------------------------------------------

class Ordens:
    """A OF em uso e o dashboard (métodos da Janela)."""

    def _repositorio(self) -> Repositorio:
        """A base de dados do perfil carregado (aberta só quando é precisa)."""
        caminho = caminho_base_dados(self._cfg_perfil, self.args.saida)
        if self._repo is None or self._repo.caminho != caminho:
            self._repo = Repositorio(caminho)
        return self._repo

    def _pode(self, acao: str) -> bool:
        return pode(self.papel, acao)

    def _identidade(self) -> tuple[str, str]:
        """(OF, produto) em uso; "" quando não há."""
        e = self._ordem
        return (e.of.numero if e and e.of else "", e.produto.produto if e and e.produto else "")

    def _escolher_ordem(self) -> bool:
        """Pede a OF e/ou o produto (e cria-os, se for preciso) e passa a usá-los. False se desistiu."""
        try:
            dialogo = DialogoOrdem(self, self._repositorio())
        except ErroRepositorio as erro:
            QMessageBox.critical(self, "Ordem de fabrico", str(erro))
            return False
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return False
        self._ensaio_sem_of = dialogo.escolha is None
        return self._usar_ordem(dialogo.escolha)

    def _usar_ordem(self, escolha: Escolha | None) -> bool:
        """Passa a usar esta OF/produto: montagem → produto → OF. A correr, entra no próximo saco."""
        # A montagem sem o produto que estava em uso (medidas e, se a tinha, a resolução da localização)
        anterior = self._ordem.produto if self._ordem is not None else None
        montagem = repor_medidas(self._cfg_edicao, self._cfg_perfil, anterior)
        try:
            if escolha is None or escolha.produto is None:  # sem OF: o produto volta a ser o do perfil
                cfg = montagem
            else:
                cfg = compor(montagem, escolha.produto, escolha.of)
        except ValueError as erro:
            QMessageBox.critical(self, "Ordem de fabrico", str(erro))
            return False
        self._ordem = escolha if escolha is not None and escolha.produto is not None else None
        self._cfg_base = self._cfg_edicao = cfg
        for editor in (self.editor, self.editor_camara):
            editor.definir(cfg, perfil=cfg)
        if self.ciclo is not None:
            self.ciclo.aplicar(cfg, *self._identidade())
        self._atualizar_ordem()
        return True

    def _recarregar_ordem(self) -> None:
        """Depois de se editar uma OF ou um produto: volta a ler a OF em uso da base de dados."""
        of, produto = self._identidade()
        if not (of or produto):
            return
        try:
            escolha = resolver(self._repositorio(), of, produto)
        except (ValueError, ErroRepositorio) as erro:
            self._mensagem(f"OF em uso: {erro}")
            return
        if escolha.produto is not None:
            self._usar_ordem(escolha)

    def _atualizar_ordem(self) -> None:
        of, produto = self._identidade()
        texto = f"OF {of} · {produto}" if of else produto or "Sem OF"
        self.btn_of.setText(texto)
        self.btn_of.setToolTip(f"Ordem de fabrico e produto em uso: {texto}. Clicar para mudar "
                               "(a correr, entra no próximo saco).")
        self._atualizar_resumo()

    def _abrir_dashboard(self) -> None:
        if self.janela_dashboard is None:
            self.janela_dashboard = JanelaDashboard(self, self.acao_dashboard.icon())
        self.janela_dashboard.abrir()
