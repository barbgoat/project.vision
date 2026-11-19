"""Interface: Verificar rugas. Só o M2 e o M5 num saco, a pedido, fora da inspeção.

Pôr o saco à frente da câmara e carregar em Verificar rugas (F6). A imagem vem:
    - da afinação, se a câmara está a afinar (a mais recente, na ROI);
    - do último saco, se a inspeção está a correr;
    - senão, da própria câmara: abre-a, tira uma imagem e fecha-a.
Também se pode verificar uma imagem guardada (Abrir imagem…).

Para ver rugas não é preciso medir o saco. Se o M2 não o localizar com o perfil (foto do fundo
desatualizada, saco a tocar na borda, controlos da medição), tenta sem a foto do fundo e sem os
controlos; se nem assim, o M5 vê a imagem toda (sem as bordas). A janela diz como foi visto. Cada
imagem tirada da câmara fica em <saída>/verificar_rugas/<dia>/, com o veredito no nome.

A janela diz RUGA ou SEM RUGAS, com o saco anotado (cada ruga numerada) e as etapas do M5. Para
ensinar (só o administrador):
    É OK: ensinar   o recorte do saco vai para a referência do M5 (relevo.referencia_pasta; sem ela,
                    <dataset>/relevo_referencia, a guardar no perfil): a impressão dele deixa de
                    passar por ruga. A imagem vai também para o dataset (ok).
    Tem rugas       a imagem vai para o dataset (rugas): é com estas e com as OK que se mede e afina
                    o M5 (ferramentas/bancada_rugas.py).
"""

from __future__ import annotations

import dataclasses
import html
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
    QWidget,
)

from interface.ciclo import Visto, no_modo, preparar
from interface.estilo import CORES_ESTADO, NOME_APP
from interface.paineis import Etapas, VistaImagem
from interface.registos import guardar_correcao
from mestre import Mestre, no_espaco_metrico
from modulos.m1_aquisicao import criar_fonte
from modulos.m5_relevo import Relevo, referencia
from nucleo.config import RAIZ, Config
from nucleo.contrato import LOCALIZACAO, Captura, Estado, Relatorio, Resultado, Saco, SemConfianca
from nucleo.imagens import guardar_imagem, ler_original, recortar

log = logging.getLogger(__name__)

PASTA_REFERENCIA = "relevo_referencia"  # no dataset do perfil, enquanto relevo.referencia_pasta está vazio
CINZENTO = "#546e7a"
ESPERA_CAMARA_S = 5.0  # tempo máximo à espera de uma imagem da câmara
MARGEM_IMAGEM_MM = 5.0  # na imagem toda (sem saco localizado): a faixa junto às bordas que não se inspeciona
BORDA_PX = 2  # um ponto do contorno a menos disto da borda da imagem está na borda
BORDA_MAX_FRACAO = 0.05  # o M2 tolerante com mais disto do contorno na borda: o saco não cabe, ver a imagem toda
NOMES_VEREDITO = {Estado.OK: "sem_rugas", Estado.NOK: "ruga", Estado.SEM_SACO: "sem_saco", Estado.ERRO: "nao_verificado"}


def localizacao_tolerante(cfg: Config) -> Config:
    """O M2 sem a foto do fundo (pode estar desatualizada) e sem os controlos da medição: para ver rugas
    não é preciso medir o saco ao centésimo, só saber onde está."""
    loc = dataclasses.replace(
        cfg.localizacao, fundo_referencia="", rejeitar_borda=False, solidez_min=0.0, qualidade_aresta_min_pct=0.0,
        qualidade_rejeitados_max_pct=0.0, qualidade_reflexos_max_pct=0.0, qualidade_residuo_max_mm=0.0,
        qualidade_contraste_min=0.0, qualidade_luz_max_pct=0.0)
    return dataclasses.replace(cfg, localizacao=loc)


def saco_imagem_inteira(img: np.ndarray, ppm: float, margem_mm: float = MARGEM_IMAGEM_MM) -> Saco:
    """A imagem toda como se fosse o recorte do saco (sem rodar), menos uma faixa junto às bordas."""
    altura, largura = img.shape[:2]
    mascara = np.full((altura, largura), 255, np.uint8)
    util = mascara.copy()
    m = max(1, round(margem_mm * ppm))
    util[:m], util[-m:], util[:, :m], util[:, -m:] = 0, 0, 0, 0
    contorno = np.array([[[0, 0]], [[largura - 1, 0]], [[largura - 1, altura - 1]], [[0, altura - 1]]], np.int32)
    return Saco(contorno, ((largura - 1) / 2, (altura - 1) / 2), 0.0, largura / ppm, altura / ppm,
                np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), img, mascara, util)


def cortado_pela_borda(saco: Saco, forma: tuple[int, ...]) -> bool:
    """O contorno do saco segue a borda da imagem em mais de BORDA_MAX_FRACAO dos pontos: o saco é maior do que
    o campo (ou está cortado) e o contorno do M2 tolerante, por dentro, é um palpite (corta o saco a meio,
    por uma sombra ou um reflexo). Aí o M5 vê melhor a imagem toda."""
    altura, largura = forma[:2]
    c = saco.contorno.reshape(-1, 2)
    m = BORDA_PX
    na_borda = (c[:, 0] <= m) | (c[:, 0] >= largura - 1 - m) | (c[:, 1] <= m) | (c[:, 1] >= altura - 1 - m)
    return bool(len(c)) and float(na_borda.mean()) > BORDA_MAX_FRACAO


def imagem_inteira(cfg: Config, captura: Captura, motivo: str) -> Resultado:
    """O M5 na imagem toda, sem o M2 (que não localizou o saco por `motivo`)."""
    inicio = perf_counter()
    img = no_espaco_metrico(captura.imagem, captura.px_por_mm, cfg.geral.px_por_mm)
    saco = saco_imagem_inteira(img, cfg.geral.px_por_mm)
    try:
        m5 = Relevo().inspecionar(saco, captura, cfg, guardar_intermedios=True)
        m5 = dataclasses.replace(m5, defeitos=[dataclasses.replace(d, modulo=Relevo.nome) for d in m5.defeitos])
    except SemConfianca as erro:
        m5 = Relatorio(Relevo.nome, Estado.ERRO, mensagem=str(erro))
    ms = (perf_counter() - inicio) * 1000
    m5 = dataclasses.replace(m5, tempo_ms=ms)
    estado = m5.estado if m5.estado in (Estado.OK, Estado.NOK) else Estado.ERRO
    return Resultado(img, estado, saco, [Relatorio(LOCALIZACAO, Estado.SEM_SACO, mensagem=motivo), m5], ms,
                     captura=captura)


def verificar(cfg: Config, captura: Captura, of: str = "", produto: str = "") -> tuple[Resultado, str]:
    """Só o M2 e o M5 numa imagem. Devolve (resultado, nota): a nota diz como o saco foi visto quando o
    M2 não o localizou com o perfil ("" se localizou)."""
    def com(config: Config) -> Resultado:
        r = Mestre(config, apenas=[Relevo.nome], of=of, produto=produto).inspecionar(
            captura.imagem, guardar_intermedios=True, px_por_mm=captura.px_por_mm, captura=captura)
        r.captura = captura
        return r

    resultado = com(cfg)
    if resultado.saco is not None:
        return resultado, ""
    motivo = resultado.mensagem or str(resultado.estado)
    tolerante = com(localizacao_tolerante(cfg))
    if tolerante.saco is not None and not cortado_pela_borda(tolerante.saco, tolerante.imagem.shape):
        return tolerante, (f"o M2 não localizou o saco com o perfil ({motivo}); foi localizado sem a foto do fundo "
                           "e sem os controlos da medição. Se for a foto do fundo, refazer o Fundo vazio "
                           "(Afinar câmara).")
    if tolerante.saco is not None:
        motivo += "; sem o perfil, o saco sai pela borda da imagem (maior do que o campo?)"
    r = imagem_inteira(cfg, captura, motivo)
    r.of, r.produto = of, produto
    return r, f"o M2 não localizou o saco ({motivo}): o M5 viu a imagem toda, menos as bordas."


class VerificacaoRugas(QThread):
    """Tira a imagem (da câmara, se não vier dada) e corre nela só o M2 e o M5, fora da janela."""

    visto = Signal(object)  # Visto preparado
    mensagem = Signal(str)

    def __init__(self, cfg: Config, imagem: np.ndarray | None, origem: str, numero: int,
                 of: str = "", produto: str = "", pasta_imagens: Path | None = None) -> None:
        """pasta_imagens: onde guardar a imagem verificada (None = não guardar)."""
        super().__init__()
        self.cfg, self.imagem, self.origem, self.numero = cfg, imagem, origem, numero
        self.of, self.produto, self.pasta_imagens = of, produto, pasta_imagens

    def run(self) -> None:
        try:
            captura = self._captura()
            if captura is None:
                self.mensagem.emit(f"A câmara não deu nenhuma imagem em {ESPERA_CAMARA_S:g} s (ligada? noutro "
                                   "programa?). Também se pode verificar uma imagem guardada: Abrir imagem…")
                return
            inicio = perf_counter()
            resultado, nota = verificar(self.cfg, captura, self.of, self.produto)
            ms = (perf_counter() - inicio) * 1000
        except Exception as erro:  # a câmara, a configuração, a referência... (o traceback fica no log)
            log.exception("Verificar rugas falhou.")
            self.mensagem.emit(str(erro))
            return
        self._guardar(captura, resultado)
        self.visto.emit(preparar(Visto(self.numero, resultado, self.cfg, ms, nota=nota)))

    def _guardar(self, captura: Captura, resultado: Resultado) -> None:
        """A imagem tirada da câmara, com o veredito no nome (uma imagem aberta do disco já está guardada)."""
        if self.pasta_imagens is None or self.imagem is not None and Path(self.origem).is_file():
            return
        agora = datetime.now()
        nome = f"{agora:%H%M%S_%f}"[:-3] + f"_{self.numero}_{NOMES_VEREDITO.get(veredito(resultado)[0], 'x')}.png"
        try:
            guardar_imagem(self.pasta_imagens / f"{agora:%Y%m%d}" / nome, captura.imagem)
        except OSError as erro:
            log.warning("Verificar rugas: a imagem não foi guardada: %s", erro)

    def _captura(self) -> Captura | None:
        if self.imagem is not None:
            return Captura(self.imagem, self.origem, time.time())
        # A câmara é partilhada: abre-se, tira-se uma imagem e fecha-se. A segunda, porque a primeira
        # pode vir do arranque da câmara. Uma câmara que não dá imagens não pendura a verificação.
        with criar_fonte(no_modo(self.cfg, "ao_vivo")) as fonte:
            limite = threading.Timer(ESPERA_CAMARA_S, fonte.parar)
            limite.start()
            try:
                primeira = fonte.ler()
                segunda = fonte.ler() if primeira is not None and not fonte.parada else None
            finally:
                limite.cancel()
        return segunda or primeira


def saco_localizado(r: Resultado) -> bool:
    """O M2 localizou o saco (com o perfil ou sem a foto do fundo): há um recorte normalizado."""
    return r.saco is not None and bool(r.relatorios) and r.relatorios[0].estado is Estado.OK


def veredito(r: Resultado) -> tuple[Estado, str]:
    """(estado para a cor, texto em grande) da verificação."""
    m5 = next((x for x in r.relatorios if x.modulo == "relevo"), None)
    if m5 is None:  # o M2 não deu um saco
        if r.estado is Estado.SEM_SACO:
            return Estado.SEM_SACO, "SEM SACO"
        return Estado.ERRO, "NÃO VERIFICADO (o saco não foi localizado)"
    if m5.estado is Estado.NOK:
        n = len(m5.defeitos)
        return Estado.NOK, "RUGA DETETADA" + (f" ({n})" if n > 1 else "")
    if m5.estado is Estado.OK:
        return Estado.OK, "SEM RUGAS"
    return Estado.ERRO, "NÃO VERIFICADO"


def detalhes(visto: Visto, origem: str) -> str:
    """O que a verificação viu, em HTML: as rugas, a referência e os avisos."""
    r = visto.resultado
    linhas = [f"<b>Imagem:</b> {html.escape(origem)} &nbsp;·&nbsp; {visto.ciclo_ms:.0f} ms"]
    if visto.nota:
        linhas.append(f"<span style='color:{CORES_ESTADO[Estado.ERRO]}'><b>Atenção:</b> {html.escape(visto.nota)}</span>")
    m5 = next((x for x in r.relatorios if x.modulo == "relevo"), None)
    if m5 is None:
        linhas.append(f"<span style='color:{CORES_ESTADO[Estado.ERRO]}'>{html.escape(r.mensagem)}</span>")
        return "<br>".join(linhas)
    if m5.mensagem:  # primeiro: diz se o veredito é de confiar
        cor = CORES_ESTADO[Estado.ERRO]
        linhas.append(f"<span style='color:{cor}'><b>Atenção:</b> {html.escape(m5.mensagem)}</span>")
    sacos = int(m5.medidas.get("referencia_sacos", 0))
    if sacos:
        linhas.append(f"Impressão descontada com {sacos} saco(s) OK da referência "
                      f"(alinhamento {m5.medidas.get('referencia_alinhamento', float('nan')):.2f})")
    for i, d in enumerate(m5.defeitos, start=1):
        linhas.append(f"<b>{i}.</b> {html.escape(d.descricao)}, com o centro a ({d.x_mm:+.0f}, {d.y_mm:+.0f}) mm "
                      "do centro do saco")
    if "queimado_pct" in m5.medidas:
        linhas.append(f"Queimado (branco saturado, onde não se vê nada): {m5.medidas['queimado_pct']:.1f} % do saco")
    if m5.estado is Estado.NOK:
        linhas.append("<i>Se o saco é bom e o que está marcado é impressão: «É OK: ensinar» (a impressão "
                      "dele passa a ser descontada).</i>")
    return "<br>".join(linhas)


class JanelaRugas(QDialog):
    """O resultado da verificação em grande, o saco anotado e as etapas do M5. Não bloqueia a
    janela principal (pode ficar aberta ao lado, a verificar saco atrás de saco)."""

    verificar = Signal()  # verificar o saco à frente da câmara (a Janela escolhe de onde vem a imagem)
    abrir = Signal(str)  # verificar uma imagem guardada
    ensinar = Signal(object)  # Visto: o saco é OK, para a referência do M5 e o dataset
    com_rugas = Signal(object)  # Visto: o saco tem rugas, para o dataset

    def __init__(self, principal: QWidget) -> None:
        super().__init__(principal)
        self.setWindowTitle(f"{NOME_APP}: verificar rugas")
        self.visto: Visto | None = None
        self.origem = ""
        self.lbl_veredito = QLabel()
        self.lbl_veredito.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imagem = VistaImagem("Põe o saco à frente da câmara e carrega em Verificar")
        self.imagem.setMinimumSize(560, 360)
        self.etapas = Etapas()
        self.etapas.setToolTip("As etapas do M2 e do M5: clicar mostra-a em grande")
        self.etapas.escolhida.connect(lambda nome, img: self.imagem.mostrar(img))
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_info.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btn_verificar = QPushButton("Verificar (F6)")
        self.btn_verificar.setObjectName("iniciar")  # verde (ESTILO)
        self.btn_verificar.setToolTip("Verifica o saco que está à frente da câmara")
        self.btn_verificar.clicked.connect(self.verificar)
        self.btn_abrir = QPushButton("Abrir imagem…")
        self.btn_abrir.setToolTip("Verifica uma imagem guardada (a original da câmara, não a anotada)")
        self.btn_abrir.clicked.connect(self._abrir)
        self.btn_ensinar = QPushButton("É OK: ensinar")
        self.btn_ensinar.setToolTip("O saco é bom: o recorte vai para a referência do M5 (a impressão dele deixa "
                                    "de passar por ruga) e a imagem para o dataset (ok)")
        self.btn_ensinar.clicked.connect(lambda: self.visto is not None and self.ensinar.emit(self.visto))
        self.btn_rugas = QPushButton("Tem rugas: guardar")
        self.btn_rugas.setToolTip("O saco tem rugas: a imagem vai para o dataset (rugas), para medir e afinar o M5")
        self.btn_rugas.clicked.connect(lambda: self.visto is not None and self.com_rugas.emit(self.visto))
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(self.close)
        botoes = QHBoxLayout()
        for botao in (self.btn_verificar, self.btn_abrir):
            botoes.addWidget(botao)
        botoes.addStretch()
        for botao in (self.btn_ensinar, self.btn_rugas, fechar):
            botoes.addWidget(botao)

        rolar = QScrollArea()  # com muitas rugas, o texto não cabe
        rolar.setWidgetResizable(True)
        rolar.setFrameShape(QFrame.Shape.NoFrame)
        rolar.setWidget(self.lbl_info)
        baixo = QSplitter(Qt.Orientation.Horizontal)
        baixo.addWidget(rolar)
        baixo.addWidget(self.etapas)
        baixo.setSizes([450, 450])
        centro = QSplitter(Qt.Orientation.Vertical)
        centro.addWidget(self.imagem)
        centro.addWidget(baixo)
        centro.setSizes([560, 200])
        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_veredito)
        layout.addWidget(centro, 1)
        layout.addLayout(botoes)
        QShortcut(QKeySequence("F6"), self).activated.connect(self.verificar)
        self._pintar(None, "PRONTO: põe o saco e carrega em Verificar")
        self._ensinar_ativo(False, False)
        self.resize(1000, 820)

    # --- Estado ------------------------------------------------------------------------
    def pode_ensinar(self, sim: bool) -> None:
        """Ensinar muda a referência e o dataset: só o administrador."""
        self.btn_ensinar.setVisible(sim)
        self.btn_rugas.setVisible(sim)

    def a_verificar(self, origem: str) -> None:
        self.origem = origem
        self.btn_verificar.setEnabled(False)
        self.btn_abrir.setEnabled(False)
        self._ensinar_ativo(False, False)
        self._pintar(None, f"A VERIFICAR…  ({origem})")

    def mostrar(self, visto: Visto) -> None:
        self.visto = visto
        estado, texto = veredito(visto.resultado)
        self._pintar(estado, texto)
        self.setWindowTitle(f"{NOME_APP}: verificar rugas ({visto.numero})")
        self.imagem.mostrar(visto.anotada)
        self.etapas.mostrar(visto)
        self.lbl_info.setText(detalhes(visto, self.origem))
        self._pronto()
        # Só se ensina um saco que o M2 localizou (o recorte normalizado); guardar a imagem dá sempre
        self._ensinar_ativo(saco_localizado(visto.resultado), visto.resultado.captura is not None)

    def falhou(self, texto: str) -> None:
        self._pintar(Estado.ERRO, "NÃO VERIFICADO")
        self.lbl_info.setText(f"<span style='color:{CORES_ESTADO[Estado.ERRO]}'>{html.escape(texto)}</span>")
        self._pronto()

    def ensinado(self, texto: str) -> None:
        self.lbl_info.setText(self.lbl_info.text() + f"<br><b>{html.escape(texto)}</b>")

    def _pronto(self) -> None:
        self.btn_verificar.setEnabled(True)
        self.btn_abrir.setEnabled(True)

    def _ensinar_ativo(self, ensinar: bool, guardar: bool) -> None:
        self.btn_ensinar.setEnabled(ensinar)
        self.btn_rugas.setEnabled(guardar)

    def _pintar(self, estado: Estado | None, texto: str) -> None:
        cor = CORES_ESTADO.get(estado, CINZENTO)
        self.lbl_veredito.setText(texto)
        self.lbl_veredito.setStyleSheet(f"background: {cor}; color: white; font-weight: bold; font-size: 24pt; "
                                        "padding: 8px; border-radius: 4px;")

    def _abrir(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(self, "Verificar uma imagem", str(RAIZ / "dataset"),
                                                 "Imagens (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if caminho:
            self.abrir.emit(caminho)


class Rugas:
    """Verificar rugas (métodos da Janela)."""

    def _verificar_rugas(self) -> None:
        """Abre a janela das rugas e verifica o saco que está à frente da câmara."""
        self._abrir_janela_rugas()
        fonte = self._imagem_para_rugas()
        if fonte is None:
            self.janela_rugas.falhou("Ainda não há imagem: espera pela primeira imagem da câmara (ou pelo "
                                     "primeiro saco, a inspecionar) e volta a carregar em Verificar.")
            return
        self._comecar_verificacao(*fonte)

    def _verificar_rugas_em(self, caminho: str) -> None:
        """Verifica uma imagem guardada."""
        try:
            imagem = ler_original(caminho)
        except (ValueError, OSError) as erro:
            self.janela_rugas.falhou(str(erro))
            return
        if imagem is None:
            self.janela_rugas.falhou(f"{caminho} não é uma imagem.")
            return
        self._comecar_verificacao(imagem, caminho)  # o caminho todo: se já está no dataset, não se copia outra vez

    def _abrir_janela_rugas(self) -> None:
        if self.janela_rugas is None:
            self.janela_rugas = JanelaRugas(self)
            self.janela_rugas.verificar.connect(self._verificar_rugas)
            self.janela_rugas.abrir.connect(self._verificar_rugas_em)
            self.janela_rugas.ensinar.connect(self._ensinar_ok_rugas)
            self.janela_rugas.com_rugas.connect(self._guardar_com_rugas)
        self.janela_rugas.pode_ensinar(self.papel == "admin")
        self.janela_rugas.show()
        self.janela_rugas.raise_()
        self.janela_rugas.activateWindow()

    def _imagem_para_rugas(self) -> tuple[np.ndarray | None, str] | None:
        """(imagem, de onde vem): a imagem None quer dizer tirá-la da câmara. None se ainda não há imagem."""
        if self.afinacao is not None:  # a câmara está aberta pela afinação: a imagem mais recente, na ROI
            if self._imagem_afinacao is None:
                return None
            return recortar(self._imagem_afinacao, self._cfg_edicao.aquisicao.roi()), "câmara (afinação)"
        if self.ciclo is not None:  # a câmara está aberta pela inspeção: o último saco
            visto = self._ultimo
            if visto is None or visto.resultado.captura is None:
                return None
            return visto.resultado.captura.imagem, f"saco {visto.numero} da inspeção"
        return None, "câmara"

    def _comecar_verificacao(self, imagem: np.ndarray | None, origem: str, no_dataset: Path | None = None) -> None:
        """no_dataset: a cópia que esta imagem já tem no dataset (ao verificar de novo o mesmo saco)."""
        if self._verificacao is not None and self._verificacao.isRunning():
            return  # uma de cada vez (o botão está desligado; o F6 da janela principal não)
        self._verificacoes += 1
        of, produto = self._identidade()
        verificacao = VerificacaoRugas(self._cfg_edicao, imagem, origem, self._verificacoes, of, produto,
                                       Path(self.args.saida) / "verificar_rugas")
        if no_dataset is not None:  # antes de a janela o mostrar: "Tem rugas" muda-a de pasta, não a copia
            verificacao.visto.connect(lambda visto: setattr(visto, "no_dataset", no_dataset))
        verificacao.visto.connect(self.janela_rugas.mostrar)
        verificacao.visto.connect(self._rugas_verificadas)
        verificacao.mensagem.connect(self.janela_rugas.falhou)
        verificacao.mensagem.connect(lambda texto: self._mensagem(f"Verificar rugas: {texto}"))
        verificacao.finished.connect(lambda: self._verificacao_terminou(verificacao))
        self._verificacao = verificacao
        self.janela_rugas.a_verificar(origem)
        verificacao.start()

    def _verificacao_terminou(self, verificacao: VerificacaoRugas) -> None:
        verificacao.deleteLater()
        if self._verificacao is verificacao:
            self._verificacao = None

    def _rugas_verificadas(self, visto: Visto) -> None:
        estado, texto = veredito(visto.resultado)
        self._mensagem(f"Verificar rugas ({visto.numero}): {texto} ({visto.ciclo_ms:.0f} ms)")

    def _esperar_verificacao(self) -> None:
        """A câmara é uma só: antes de a abrir para afinar ou inspecionar, espera que a verificação a largue."""
        if self._verificacao is not None:
            self._verificacao.wait(15000)

    def _ensinar_ok_rugas(self, visto: Visto) -> None:
        """O saco é OK: o recorte vai para a referência do M5 e a imagem para o dataset (ok). Depois
        verifica-o outra vez, já com a impressão dele descontada."""
        r = visto.resultado
        if not saco_localizado(r):
            return
        cfg = self._cfg_edicao
        pasta = cfg.relevo.referencia_pasta or _texto(self._dataset / PASTA_REFERENCIA)
        try:
            referencia.guardar_recorte(r.saco, pasta, datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3])
            feito, visto.no_dataset = guardar_correcao(r, "ok", self._dataset, Path(self.args.saida) / "correcoes.csv",
                                                       visto.no_dataset)
        except (ValueError, OSError) as erro:
            QMessageBox.warning(self.janela_rugas, "É OK: ensinar", str(erro))
            return
        n = len(referencia.recortes(pasta))
        texto = f"Ensinado: {n} saco(s) OK na referência ({pasta}); dataset: {feito}."
        if not cfg.relevo.referencia_pasta:
            self._parametros_mudaram(dataclasses.replace(cfg, relevo=dataclasses.replace(cfg.relevo,
                                                                                          referencia_pasta=pasta)))
            texto += " Guardar no perfil para manter a referência."
        self._mensagem(f"Rugas: {texto}")
        self.janela_rugas.ensinado(texto)
        self._comecar_verificacao(r.captura.imagem, r.captura.origem, visto.no_dataset)

    def _guardar_com_rugas(self, visto: Visto) -> None:
        try:
            feito, visto.no_dataset = guardar_correcao(visto.resultado, "rugas", self._dataset,
                                                       Path(self.args.saida) / "correcoes.csv", visto.no_dataset)
        except (ValueError, OSError) as erro:
            QMessageBox.warning(self.janela_rugas, "Tem rugas: guardar", str(erro))
            return
        self._mensagem(f"Rugas: dataset (rugas): {feito}")
        self.janela_rugas.ensinado(f"Guardado no dataset (rugas): {feito}")


def _texto(caminho: Path) -> str:
    """Um caminho para a configuração: relativo ao projeto, se estiver dentro dele."""
    return caminho.relative_to(RAIZ).as_posix() if caminho.is_relative_to(RAIZ) else str(caminho)
