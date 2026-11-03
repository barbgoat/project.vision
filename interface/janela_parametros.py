"""Interface, janela principal: os parâmetros e o dataset.

Métodos da Janela (janela.py), separados por assunto: as janelas ⚙ Definições e
📷 Câmara, o perfil, a pré-visualização, Aplicar, Repor, Guardar no perfil
e Guardar no dataset.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from interface.ciclo import Previsao, Visto
from interface.editor import SECOES_CAMARA, SECOES_DEFINICOES, EditorParametros, diferencas
from interface.estilo import icone
from interface.janelas import JanelaAuxiliar
from interface.registos import guardar_correcao, pasta_dataset
from modulos.m1_aquisicao.disparo import faixa_da_barreira
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, carregar_configuracao
from nucleo.especificacoes import repor_medidas



class Parametros:
    """Os parâmetros e o dataset (métodos da Janela)."""

    def _criar_definicoes(self) -> JanelaAuxiliar:
        """⚙ Definições: o perfil (Geral), e os parâmetros de inspeção e de saída."""
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
        geral = QWidget()
        formulario = QFormLayout(geral)
        formulario.addRow("Perfil", self.cb_perfil)
        formulario.addRow("Câmara", QLabel("Basler (pypylon); o modo, Ao vivo ou Trigger, escolhe-se na barra"))

        self.editor = EditorParametros(SECOES_DEFINICOES)
        self._ligar_editor(self.editor)
        separadores = QTabWidget()
        separadores.addTab(geral, "Geral")
        separadores.addTab(self.editor, "Inspeção e saída")
        return JanelaAuxiliar(self, "Definições", icone("definicoes"), separadores, (1050, 620))

    def _criar_camara(self) -> JanelaAuxiliar:
        """📷 Câmara: exposição, ganho, trigger (disparo) e lente."""
        ver = self._botao("Ver ao vivo", icone_botao=icone("camara"),
                          dica="Passa a Ao vivo e mostra a câmara em contínuo (parado: afina, sem inspeção).")
        ver.clicked.connect(lambda: self._ver_camara("ao_vivo"))
        ver_trigger = self._botao("Ver com trigger", icone_botao=icone("camara"),
                                  dica="Passa a Trigger e mostra uma imagem por sinal do botão ou do sensor "
                                       "(parado: afina, sem inspeção).")
        ver_trigger.clicked.connect(lambda: self._ver_camara("trigger"))
        self.btn_roi = self._botao("Definir ROI", True, dica="A afinar (Ao vivo ou Trigger): arrastar na imagem o "
                                                              "retângulo que segue para a inspeção (com fundo à "
                                                              "volta do saco).")
        self.btn_roi.toggled.connect(self._desenhar_roi)
        sem_roi = self._botao("Imagem inteira", dica="Sem ROI: a imagem toda da câmara segue para a inspeção.")
        sem_roi.clicked.connect(lambda: self._definir_roi(0, 0, 0, 0))
        self.lbl_medidas = QLabel()  # ao afinar: brilho, saturados, nitidez (Afinacao.medidas)
        topo = QHBoxLayout()
        topo.addWidget(self.lbl_medidas, 1)
        for botao in (ver, ver_trigger, self.btn_roi, sem_roi):
            topo.addWidget(botao)
        self.editor_camara = EditorParametros(SECOES_CAMARA)
        # A afinar, a exposição e o ganho mudam logo; a inspeção só os usa no próximo Iniciar.
        self.editor_camara.btn_aplicar.hide()
        self._ligar_editor(self.editor_camara)
        conteudo = QWidget()
        coluna = QVBoxLayout(conteudo)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.addLayout(topo)
        coluna.addWidget(self.editor_camara, 1)
        return JanelaAuxiliar(self, "Câmara", icone("camara"), conteudo, (1000, 620))

    def _desenhar_roi(self, ligado: bool) -> None:
        """Arrastar na imagem desenha a ROI (só a afinar: é a imagem inteira da câmara). Os dois
        botões Definir ROI (na janela da câmara e por cima da imagem) andam juntos."""
        for botao in (self.btn_roi, getattr(self, "btn_roi_imagem", None)):
            if botao is not None and botao.isChecked() != ligado:
                botao.blockSignals(True)
                botao.setChecked(ligado)
                botao.blockSignals(False)
        for vista in (self.imagem_op, self.imagem_eng):
            vista.desenhar_roi = ligado
            vista.setCursor(Qt.CursorShape.CrossCursor if ligado else Qt.CursorShape.ArrowCursor)

    def _definir_roi(self, x: int, y: int, largura: int, altura: int) -> None:
        a = self._cfg_edicao.aquisicao
        roi = dataclasses.replace(a, roi_x_px=x, roi_y_px=y, roi_largura_px=largura, roi_altura_px=altura)
        self._desenhar_roi(False)
        self._parametros_mudaram(dataclasses.replace(self._cfg_edicao, aquisicao=roi))
        self._mensagem(f"ROI {largura} × {altura} px em ({x}, {y}): Guardar no perfil para a manter."
                       if largura else "Sem ROI: a imagem inteira. Guardar no perfil para o manter.")

    def _mostrar_auxilios(self) -> None:
        """A afinar, por cima da imagem inteira: a ROI e, Ao vivo, a faixa vigiada pela barreira virtual
        (no meio da ROI, como na inspeção). Parado ou a inspecionar, nada."""
        if not hasattr(self, "imagem_eng"):  # ainda a construir a janela
            return
        auxilios = []
        if self.afinacao is not None:
            cfg = self._cfg_edicao
            roi = cfg.aquisicao.roi()
            if roi is not None:
                auxilios.append((roi, "#ef6c00", "ROI"))
            if self.afinacao.modo == "ao_vivo" and self._forma_afinacao is not None:
                altura, largura = self._forma_afinacao
                x, y, w, h = roi or (0, 0, largura, altura)
                x0, y0 = min(max(x, 0), largura - 1), min(max(y, 0), altura - 1)  # como recortar()
                w, h = min(x + w, largura) - x0, min(y + h, altura) - y0
                fx, fy, fw, fh = faixa_da_barreira(cfg, w, h)
                auxilios.append(((x0 + fx, y0 + fy, fw, fh), "#1e88e5", "barreira"))
        for vista in (self.imagem_op, self.imagem_eng):
            vista.auxilios = auxilios
            vista.update()

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
        self._perfil, self._cfg_perfil, self._cfg_edicao, self._cfg_base = caminho, cfg, cfg, cfg
        for editor in (self.editor, self.editor_camara):
            editor.definir(cfg, perfil=cfg, nome_perfil=caminho.name)
        if self._ordem is not None:  # a OF em uso fica, por cima da montagem nova
            self._usar_ordem(self._ordem)
        if self.args.dataset is None:
            self._dataset = pasta_dataset(caminho)
        self.btn_corrigir_eng.setToolTip(f"A classe real deste saco: a imagem vai para {self._dataset}\\<classe>\\")
        self._atualizar_resumo()

    # --- Parâmetros (⚙ Definições e 📷 Câmara) -------------------------------------------
    def _parametros_mudaram(self, cfg: Config) -> None:
        anterior, self._cfg_edicao = self._cfg_edicao, cfg
        for editor in (self.editor, self.editor_camara):  # os dois editores mostram a mesma configuração
            if editor.cfg() is not cfg:
                editor.definir(cfg)
        self._atualizar_resumo()
        if self.afinacao is not None:
            self.afinacao.aplicar(cfg)  # a afinar: a exposição e o ganho veem-se já
        self._mostrar_auxilios()
        if all(getattr(cfg, s) == getattr(anterior, s) for s in SECOES_DEFINICOES):
            return  # só mudou a câmara: entra no próximo Iniciar, não há nada a pré-visualizar
        # Pré-visualizar no saco que está no ecrã, sem mexer na linha.
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
        if self.ciclo is not None:
            self.ciclo.aplicar(self._cfg_edicao)
            self.btn_congelar.setChecked(False)  # volta a seguir a linha, já com os valores novos
            self._mensagem("Parâmetros enviados: entram no próximo saco.")

    def _repor(self) -> None:
        self._parametros_mudaram(self._cfg_base)

    def _guardar(self) -> None:
        cfg = self._cfg_edicao
        if self._ordem is not None:  # as medidas são do produto: no perfil da montagem só entra a montagem
            cfg = repor_medidas(cfg, self._cfg_perfil)
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
        if self._ordem is None:
            self._cfg_base = cfg
        for editor in (self.editor, self.editor_camara):
            editor.definir(self._cfg_edicao, perfil=self._cfg_base)
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
