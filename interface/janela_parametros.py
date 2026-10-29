"""Interface, janela principal: os parâmetros e o dataset.

Métodos da Janela (janela.py), separados por assunto: as janelas ⚙ Definições e
📷 Câmara, o perfil e a fonte, a pré-visualização, Aplicar, Repor, Guardar no perfil
e Guardar no dataset.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QTabWidget, QVBoxLayout, QWidget,
)

from interface.ciclo import Previsao, Visto
from interface.editor import SECOES_CAMARA, SECOES_DEFINICOES, EditorParametros, diferencas
from interface.estilo import icone
from interface.janelas import JanelaAuxiliar
from interface.registos import guardar_correcao, pasta_dataset
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, carregar_configuracao


FONTES = {"Pasta": "pasta", "Vídeo": "video", "Webcam": "webcam", "Câmara": "spinnaker"}


class Parametros:
    """Os parâmetros e o dataset (métodos da Janela)."""

    def _criar_definicoes(self) -> JanelaAuxiliar:
        """⚙ Definições: o perfil e a fonte (Geral), e os parâmetros de inspeção e de saída."""
        self.cb_perfil = QComboBox()
        for caminho in sorted((RAIZ / "configuracoes").glob("*.json")):
            self.cb_perfil.addItem(caminho.stem, str(caminho.resolve()))
        self.cb_perfil.addItem("config.json (esta máquina)", str(CAMINHO_POR_OMISSAO.resolve()))
        indice = self.cb_perfil.findData(str(self._perfil.resolve()))
        if indice < 0:  # um perfil fora de configuracoes/, dado com --config
            self.cb_perfil.addItem(self._perfil.stem, str(self._perfil.resolve()))
            indice = self.cb_perfil.count() - 1
        self.cb_perfil.setCurrentIndex(indice)
        self.cb_perfil.currentIndexChanged.connect(self._carregar_perfil)
        self.cb_fonte = QComboBox()
        for nome, tipo in FONTES.items():
            self.cb_fonte.addItem(nome, tipo)
        tipo = self.args.fonte or self._lembrado("fonte") or "pasta"
        self.cb_fonte.setCurrentIndex(max(0, self.cb_fonte.findData(tipo)))
        self.cb_fonte.currentIndexChanged.connect(self._mudar_fonte)
        dado = self.args.video if tipo == "video" else self.args.pasta
        self.ed_caminho = QLineEdit(str(dado) if dado else self._lembrado("caminho") or "")
        self.ed_caminho.setPlaceholderText("pasta com as imagens ou ficheiro de vídeo")
        self.ed_caminho.textChanged.connect(self._atualizar_resumo)
        self.btn_procurar = self._botao("Procurar", icone_botao=self._icone_qt("SP_DirOpenIcon"),
                                        dica="Escolher a pasta ou o vídeo")
        self.btn_procurar.clicked.connect(self._procurar)
        caminho = QHBoxLayout()
        caminho.addWidget(self.ed_caminho, 1)
        caminho.addWidget(self.btn_procurar)
        geral = QWidget()
        formulario = QFormLayout(geral)
        formulario.addRow("Perfil", self.cb_perfil)
        formulario.addRow("Fonte", self.cb_fonte)
        formulario.addRow("Pasta ou vídeo", caminho)

        self.editor = EditorParametros(SECOES_DEFINICOES)
        self._ligar_editor(self.editor)
        separadores = QTabWidget()
        separadores.addTab(geral, "Geral")
        separadores.addTab(self.editor, "Inspeção e saída")
        return JanelaAuxiliar(self, "Definições", icone("definicoes"), separadores, (1050, 620))

    def _criar_camara(self) -> JanelaAuxiliar:
        """📷 Câmara: exposição, ganho, trigger (disparo) e lente."""
        ver = self._botao("Ver ao vivo", icone_botao=icone("camara"),
                          dica="Passa a Ao vivo e começa: centrar o saco, acertar a luz e o foco.")
        ver.clicked.connect(self._ver_ao_vivo)
        topo = QHBoxLayout()
        topo.addStretch()
        topo.addWidget(ver)
        self.editor_camara = EditorParametros(SECOES_CAMARA)
        self.editor_camara.btn_aplicar.hide()  # a câmara e o trigger só mudam no próximo Iniciar
        self._ligar_editor(self.editor_camara)
        conteudo = QWidget()
        coluna = QVBoxLayout(conteudo)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.addLayout(topo)
        coluna.addWidget(self.editor_camara, 1)
        return JanelaAuxiliar(self, "Câmara", icone("camara"), conteudo, (1000, 620))

    def _ligar_editor(self, editor: EditorParametros) -> None:
        editor.mudou.connect(self._parametros_mudaram)
        editor.aplicar.connect(self._aplicar)
        editor.repor.connect(self._repor)
        editor.guardar.connect(self._guardar)

    def _carregar_perfil(self) -> None:
        caminho = Path(self.cb_perfil.currentData())
        try:
            cfg = carregar_configuracao(caminho)
        except (ValueError, FileNotFoundError) as erro:
            QMessageBox.critical(self, "Perfil", str(erro))
            self.cb_perfil.blockSignals(True)  # volta a mostrar o perfil que continua em uso
            self.cb_perfil.setCurrentIndex(self.cb_perfil.findData(str(self._perfil.resolve())))
            self.cb_perfil.blockSignals(False)
            return
        self._perfil, self._cfg_perfil, self._cfg_edicao = caminho, cfg, cfg
        for editor in (self.editor, self.editor_camara):
            editor.definir(cfg, perfil=cfg, nome_perfil=caminho.name)
        if self.args.dataset is None:
            self._dataset = pasta_dataset(caminho)
        self.btn_corrigir_eng.setToolTip(f"A classe real deste saco: a imagem vai para {self._dataset}\\<classe>\\")
        self._atualizar_resumo()

    def _mudar_fonte(self) -> None:
        if self.cb_fonte.currentData() == "video" and self._cfg_edicao.disparo.modo == "foto":
            # Com um vídeo e o disparo "foto", cada imagem do vídeo contaria como um saco.
            cfg = self._cfg_edicao
            self._parametros_mudaram(dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo="virtual")))
            self._mensagem("Com um vídeo, o disparo passou a 'virtual' (📷 Câmara).")
        self._atualizar_botoes()

    def _procurar(self) -> None:
        if self.cb_fonte.currentData() == "pasta":
            caminho = QFileDialog.getExistingDirectory(self, "Pasta com as imagens", self.ed_caminho.text())
        else:
            caminho, _ = QFileDialog.getOpenFileName(self, "Vídeo", self.ed_caminho.text(),
                                                     "Vídeos (*.mp4 *.avi *.mov *.mkv);;Todos (*)")
        if caminho:
            self.ed_caminho.setText(caminho)

    # --- Parâmetros (⚙ Definições e 📷 Câmara) -------------------------------------------
    def _parametros_mudaram(self, cfg: Config) -> None:
        anterior, self._cfg_edicao = self._cfg_edicao, cfg
        for editor in (self.editor, self.editor_camara):  # os dois editores mostram a mesma configuração
            if editor.cfg() is not cfg:
                editor.definir(cfg)
        self._atualizar_resumo()
        if all(getattr(cfg, s) == getattr(anterior, s) for s in SECOES_DEFINICOES):
            return  # só mudou a câmara: entra no próximo Iniciar, não há nada a pré-visualizar
        if self.ciclo is not None and self.ciclo.pedido.modo == "ao_vivo":
            self.ciclo.aplicar(cfg)  # ao vivo: entra já
            return
        # Produção (ou parado): pré-visualizar no saco que está no ecrã, sem mexer na linha.
        if self._base is None or self._base.resultado.captura is None:
            return
        if self.ciclo is not None:
            self.btn_congelar.setChecked(True)
        self._prever(self._base, cfg)

    def _prever(self, base: Visto, cfg: Config) -> None:
        if self._previsao is not None and self._previsao.isRunning():
            self._previsao_pendente = (base, cfg)  # só conta a mais recente
            return
        self._previsao = Previsao(base, cfg)
        self._previsao.visto.connect(self._previsto)
        self._previsao.mensagem.connect(self._mensagem)
        self._previsao.finished.connect(self._previsao_terminou)
        self._previsao.start()

    def _previsto(self, visto: Visto) -> None:
        self._mostrar(visto, original=False)

    def _previsao_terminou(self) -> None:
        if self._previsao_pendente is not None:
            base, cfg = self._previsao_pendente
            self._previsao_pendente = None
            self._prever(base, cfg)

    def _aplicar(self) -> None:
        if self.ciclo is not None and self.ciclo.pedido.modo == "producao":
            self.ciclo.aplicar(self._cfg_edicao)
            self.btn_congelar.setChecked(False)  # volta a seguir a linha, já com os valores novos
            self._mensagem("Parâmetros enviados: entram no próximo saco.")

    def _repor(self) -> None:
        self._parametros_mudaram(self._cfg_perfil)

    def _guardar(self) -> None:
        cfg = self._cfg_edicao
        mudancas = diferencas(self._cfg_perfil, cfg)
        if not mudancas:
            self._mensagem("Nada a guardar: os valores são os do perfil.")
            return
        resposta = QMessageBox.question(
            self, "Guardar no perfil", f"Guardar em {self._perfil.name}?\n\n" + "\n".join(mudancas))
        if resposta != QMessageBox.StandardButton.Yes:
            return
        try:
            cfg.guardar(self._perfil)
        except (ValueError, OSError) as erro:
            QMessageBox.critical(self, "Guardar no perfil", str(erro))
            return
        self._cfg_perfil = cfg
        for editor in (self.editor, self.editor_camara):
            editor.definir(cfg, perfil=cfg)
        self._mensagem(f"{len(mudancas)} valor(es) guardado(s) em {self._perfil.name}.")

    # --- Correções ---------------------------------------------------------------------
    def _corrigir(self, classe: str) -> None:
        visto = self._base
        if visto is None:
            self._mensagem("Ainda não há nenhum saco no ecrã.")
            return
        registo = Path(self.args.saida) / "correcoes.csv"
        try:
            feito = guardar_correcao(visto.resultado, classe, self._dataset, registo)
        except (ValueError, OSError) as erro:
            QMessageBox.warning(self, "Guardar no dataset", str(erro))
            return
        self._mensagem(f"Dataset ({classe}): {feito}")
