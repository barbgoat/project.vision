"""Interface, janela principal: a conta (👤) e a memória da sessão.

Métodos da Janela (janela.py), separados por assunto: quem está a usar e o que pode
ver, mudar de utilizador, e lembrar o perfil, a fonte e a pasta (QSettings).
"""

from __future__ import annotations

import html
import logging

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from interface.estilo import NOME_APP, icone
from interface.login import CONTAS, PERMISSOES, pedir_login


log = logging.getLogger("interface")


class Conta:
    """A conta e a memória da sessão (métodos da Janela)."""

    # --- Conta (👤) --------------------------------------------------------------------
    def _definir_utilizador(self, utilizador: str) -> None:
        """Quem está a usar: o menu da conta e o que se vê (o operador só tem a vista Operador)."""
        self.utilizador = utilizador
        self.papel = CONTAS[utilizador].papel if utilizador in CONTAS else "operador"
        admin = self.papel == "admin"
        self.btn_conta.setText(f" {utilizador}")
        if self.btn_conta.menu() is not None:
            self.btn_conta.menu().deleteLater()  # o menu da sessão anterior
        menu = QMenu(self.btn_conta)
        cabecalho = menu.addAction(icone("conta"), f"{utilizador} · {self.papel}")
        cabecalho.setEnabled(False)
        menu.addSeparator()
        for texto, funcao, visivel in (("Mudar de utilizador / terminar sessão…", self._terminar_sessao, True),
                                       ("Contas e permissões…", self._mostrar_contas, admin),
                                       (f"Sair do {NOME_APP}", self.close, True)):
            if visivel:
                acao = QAction(texto, menu)
                acao.triggered.connect(funcao)
                menu.addAction(acao)
        self.btn_conta.setMenu(menu)

        for w in (self.btn_ao_vivo, self.btn_producao, self.btn_congelar, self.btn_operador, self.btn_engenharia,
                  self.btn_historico, self.btn_camara, self.btn_definicoes):
            w.setVisible(admin)
        if not admin:  # só a produção, na vista Operador
            self.btn_producao.setChecked(True)
            self.btn_operador.setChecked(True)
            self.btn_congelar.setChecked(False)
            for janela in (self.janela_historico, self.janela_definicoes, self.janela_camara):
                janela.hide()
        self._mudar_vista()
        self._atualizar_botoes()

    def _mostrar_contas(self) -> None:
        linhas = "".join(f"<tr><td><b>{html.escape(nome)}</b></td><td>{conta.papel}</td>"
                         f"<td>{html.escape(PERMISSOES[conta.papel])}</td></tr>" for nome, conta in CONTAS.items())
        QMessageBox.information(
            self, "Contas e permissões",
            f"<table cellpadding='6'><tr><th align='left'>Conta</th><th align='left'>Papel</th>"
            f"<th align='left'>Pode</th></tr>{linhas}</table>"
            "<p>As contas e as palavras-passe estão em <i>interface/login.py</i>.</p>")

    def _terminar_sessao(self) -> None:
        if self.ciclo is not None:
            resposta = QMessageBox.question(self, "Terminar sessão", "A inspeção está a correr. Parar e terminar a sessão?")
            if resposta != QMessageBox.StandardButton.Yes:
                return
            self._reiniciar = False
            self.ciclo.parar()
            self.ciclo.wait(5000)
        log.info("Sessão de %s terminada.", self.utilizador)
        for janela in (self.janela_historico, self.janela_definicoes, self.janela_camara):
            janela.hide()
        self.hide()
        utilizador = pedir_login()
        if utilizador is None:  # desistiu: fecha o programa
            self.close()
            QApplication.instance().quit()
            return
        self._definir_utilizador(utilizador)
        self.showMaximized()

    # --- Memória da sessão (QSettings) ----------------------------------------------
    def _lembrado(self, chave: str) -> str | None:
        return self._memoria.value(chave) if self._memoria is not None else None

    def _lembrar(self) -> None:
        """Guarda o perfil, a fonte e a pasta para a próxima sessão (p.ex. a do operador)."""
        if self._memoria is not None:
            self._memoria.setValue("perfil", str(self._perfil))
            self._memoria.setValue("fonte", self.cb_fonte.currentData())
            self._memoria.setValue("caminho", self.ed_caminho.text().strip())
