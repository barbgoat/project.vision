"""Interface, ciclo: a inspeção a correr numa thread, Ao vivo ou em Produção.

    Ao vivo    a câmara (ou o vídeo) em contínuo, sem trigger: analisa sempre a
               imagem mais recente e desenha uma cruz no centro, para centrar o
               saco, acertar a luz e o foco, e afinar. Com uma pasta, analisa a
               imagem escolhida (◀ ▶). Um parâmetro mudado entra logo na análise
               seguinte. Não regista nada.
    Produção   como o main.py: fonte → disparo (trigger) → uma decisão por saco
               → M7 (registo.csv, imagens). Os parâmetros mudados só entram
               entre sacos, com aplicar(). Um saco não OK para a inspeção até
               a janela decidir (descartar ou NOK) e chamar prosseguir().

A janela recebe cada saco duas vezes:
    registado  logo a seguir à decisão (Produção): para os contadores e o histórico;
    visto      já com a imagem anotada e as etapas prontas a mostrar.
A preparação para o ecrã corre noutra thread e só trata o saco mais recente: a
inspeção nunca espera pela janela, e o tempo de cada saco não muda por ela existir.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Literal

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from mestre import Mestre
from modulos.m1_aquisicao import AquisicaoEmParalelo, FontePasta, criar_disparo, criar_fonte
from modulos.m1_aquisicao.fontes import FonteImagem, TipoFonte
from modulos.m7_saida import Depuracao, Saida
from nucleo.config import RAIZ, Config
from nucleo.contrato import Captura, Estado, Resultado
from nucleo.imagens import ler_imagem


log = logging.getLogger("interface")

Modo = Literal["ao_vivo", "producao"]

MINIATURA_PX = 200  # largura das miniaturas das etapas
_CRUZ_BGR = (255, 255, 0)  # ciano: a cruz no centro da imagem, ao vivo


@dataclass
class Pedido:
    """O que a janela pede ao ciclo."""

    modo: Modo
    fonte: TipoFonte
    caminho: Path | None  # a pasta ou o vídeo
    cfg: Config
    saida: Path  # pasta dos resultados (Produção)


@dataclass
class Visto:
    """Um saco (Produção) ou uma imagem (Ao vivo), e o que é preciso para o mostrar."""

    numero: int  # 1, 2, 3... desde o arranque
    modo: Modo
    resultado: Resultado
    cfg: Config  # a configuração com que foi analisado
    ciclo_ms: float = 0.0  # inspeção + registo (o que tem de caber no orçamento)
    previsao: bool = False  # analisado com parâmetros ainda não aplicados
    # Preenchidos por preparar(), fora da janela:
    preparado: bool = False
    anotada: np.ndarray | None = None  # None numa passagem descartada
    etapas: list[tuple[str, np.ndarray]] = field(default_factory=list)
    miniaturas: list[np.ndarray] = field(default_factory=list)  # uma por etapa
    desvio_mm: tuple[float, float] | None = None  # Ao vivo: centro do saco − centro da imagem


def preparar(visto: Visto) -> Visto:
    """Prepara o Visto para o ecrã: imagem anotada, etapas e miniaturas. Devolve o mesmo Visto."""
    r, cfg = visto.resultado, visto.cfg
    etapas = Depuracao(cfg, RAIZ).etapas(r)  # "original", "anotada", candidatas, intermédias
    anotada = next((img for nome, img in etapas if nome == "anotada"), None)
    if anotada is None and r.imagem is not None:  # o desenho falhou (já está no log)
        anotada = r.imagem.copy()
    if visto.modo == "ao_vivo" and anotada is not None:
        visto.desvio_mm = _cruz(anotada, r, cfg)
    visto.anotada, visto.etapas = anotada, etapas
    visto.miniaturas = [_miniatura(img) for _, img in etapas]
    visto.preparado = True
    return visto


def _cruz(imagem: np.ndarray, resultado: Resultado, cfg: Config) -> tuple[float, float] | None:
    """Desenha uma cruz no centro da imagem; devolve o desvio do centro do saco, em mm."""
    altura, largura = imagem.shape[:2]
    cx, cy = (largura - 1) / 2, (altura - 1) / 2
    espessura = max(1, largura // 800)
    cv2.line(imagem, (round(cx), 0), (round(cx), altura - 1), _CRUZ_BGR, espessura)
    cv2.line(imagem, (0, round(cy)), (largura - 1, round(cy)), _CRUZ_BGR, espessura)
    if resultado.saco is None:
        return None
    sx, sy = resultado.saco.centro_px
    return cfg.px_para_mm(sx - cx), cfg.px_para_mm(sy - cy)


def _miniatura(imagem: np.ndarray) -> np.ndarray:
    altura, largura = imagem.shape[:2]
    return cv2.resize(imagem, (MINIATURA_PX, max(1, round(altura * MINIATURA_PX / largura))),
                      interpolation=cv2.INTER_AREA)


def _ms_desde(inicio: float) -> float:
    return (perf_counter() - inicio) * 1000


# --- O ciclo ------------------------------------------------------------------------------

class Ciclo(QThread):
    """A inspeção numa thread. A janela cria um por arranque e liga-se aos sinais."""

    registado = Signal(object)  # Visto por preparar, um por saco (Produção)
    visto = Signal(object)  # Visto preparado para o ecrã (só o mais recente)
    mensagem = Signal(str)  # para a barra de estado (incluindo porque parou)

    def __init__(self, pedido: Pedido) -> None:
        super().__init__()
        self.pedido = pedido
        self.etapas = True  # pedir as imagens das etapas (só a vista de Engenharia as mostra)
        self.congelado = False  # Ao vivo: continuar na mesma imagem (para afinar nela)
        self.rever = True  # Produção: parar em cada saco não OK até a janela decidir (prosseguir())
        self.descartadas = 0  # Produção: passagens que a análise não acompanhou
        self.pasta_resultados: Path | None = None  # Produção: a pasta desta execução
        self._continuar = threading.Event()
        self._trinco = threading.Lock()
        self._acordar = threading.Event()  # imagem nova, parâmetros novos, navegar ou parar
        self._parar = threading.Event()
        self._cfg_nova: Config | None = None
        self._captura: Captura | None = None  # Ao vivo: a imagem mais recente
        self._passo = 0  # Ao vivo com pasta: imagens a avançar
        self._fonte: FonteImagem | None = None
        self._erro_leitura: BaseException | None = None
        self._a_preparar: Visto | None = None
        self._ha_para_preparar = threading.Event()
        self._fim = threading.Event()  # o ciclo acabou: a preparação despacha o que falta e termina

    # --- Comandos (chamados pela janela) -------------------------------------------------
    def aplicar(self, cfg: Config) -> None:
        """Parâmetros novos: ao vivo, na análise seguinte; em produção, a partir do próximo saco."""
        with self._trinco:
            self._cfg_nova = cfg
        self._acordar.set()

    def navegar(self, passo: int) -> None:
        """Ao vivo com uma pasta: a imagem seguinte (+1) ou a anterior (−1)."""
        with self._trinco:
            self._passo += passo
        self._acordar.set()

    def congelar(self, sim: bool) -> None:
        self.congelado = sim
        self._acordar.set()

    def prosseguir(self) -> None:
        """Produção: o saco em revisão foi decidido; a inspeção continua."""
        self._continuar.set()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()
        self._continuar.set()
        if self._fonte is not None:
            self._fonte.parar()  # uma câmara à espera de um saco que não vem sai da espera

    # --- Thread ------------------------------------------------------------------------
    def run(self) -> None:
        preparacao = threading.Thread(target=self._preparar, name="preparar", daemon=True)
        preparacao.start()
        try:
            if self.pedido.modo == "producao":
                self._producao()
            else:
                self._ao_vivo()
        except NotImplementedError:
            self.mensagem.emit(f"A fonte '{self.pedido.fonte}' ainda não está implementada (M1 aquisicao).")
        except Exception as erro:  # a janela mostra-o; o traceback fica no log
            log.exception("O ciclo parou.")
            self.mensagem.emit(f"Parou: {erro}")
        finally:
            self._fim.set()
            self._ha_para_preparar.set()
            preparacao.join()

    def _entregar(self, visto: Visto) -> None:
        """Um saco decidido: já para os contadores; para o ecrã, depois de preparado."""
        if visto.modo == "producao":
            self.registado.emit(visto)
        with self._trinco:
            self._a_preparar = visto  # se o anterior ainda não foi preparado, salta-se
        self._ha_para_preparar.set()

    def _preparar(self) -> None:
        """Thread de preparação: prepara para o ecrã o saco mais recente."""
        while True:
            self._ha_para_preparar.wait()
            self._ha_para_preparar.clear()
            with self._trinco:
                visto, self._a_preparar = self._a_preparar, None
            if visto is not None:
                try:
                    self.visto.emit(preparar(visto))
                except Exception:
                    log.exception("Não foi possível preparar o saco %d para o ecrã.", visto.numero)
            if self._fim.is_set() and self._a_preparar is None:
                return

    def _producao(self) -> None:
        p, cfg = self.pedido, self.pedido.cfg
        mestre = Mestre(cfg)
        self._fonte = criar_fonte(p.fonte, cfg, pasta=p.caminho, video=p.caminho)
        aquisicao = AquisicaoEmParalelo(self._fonte, criar_disparo(cfg), cfg.disparo.fila_max)
        with aquisicao, Saida(cfg, p.saida, mestre.nomes) as saida:
            self.pasta_resultados = saida.pasta
            self.mensagem.emit(f"Resultados em {saida.pasta}")
            for numero, passagem in enumerate(aquisicao, start=1):
                with self._trinco:
                    nova, self._cfg_nova = self._cfg_nova, None
                if nova is not None:
                    cfg, mestre = self._trocar(cfg, mestre, nova)
                    # O desenho e o registo também usam a configuração (px_por_mm).
                    saida.cfg = cfg
                    if saida.depuracao is not None:
                        saida.depuracao.cfg = cfg
                inicio = perf_counter()
                intermedios = self.etapas or saida.depuracao is not None
                resultado = mestre.inspecionar_passagem(passagem, guardar_intermedios=intermedios)
                saida.registar(resultado)
                ciclo_ms = _ms_desde(inicio)
                self.descartadas = aquisicao.descartadas
                rever = self.rever and resultado.estado is not Estado.OK
                if rever:
                    self._continuar.clear()  # antes de entregar: a decisão pode chegar logo
                self._entregar(Visto(numero, "producao", resultado, cfg, ciclo_ms))
                if rever:
                    self._continuar.wait()  # até a janela decidir (ou parar)
                if not aquisicao.ao_vivo:
                    # Pasta ou vídeo: ao ritmo da linha (um saco por orçamento), para se ver cada um.
                    self._parar.wait(max(0.0, cfg.geral.orcamento_ms - ciclo_ms) / 1000)
                if self._parar.is_set():
                    break
        if not self._parar.is_set():
            self.mensagem.emit("Fim da fonte: não há mais sacos.")

    def _ao_vivo(self) -> None:
        cfg = self.pedido.cfg
        mestre = Mestre(cfg)
        ficheiros: list[Path] = []
        indice = 0
        if self.pedido.fonte == "pasta":
            ficheiros = FontePasta(self.pedido.caminho).ficheiros
            if not ficheiros:
                raise FileNotFoundError(f"Nenhuma imagem em {self.pedido.caminho}")
            self._carregar(ficheiros, indice)
        else:
            threading.Thread(target=self._ler, name="ao_vivo", daemon=True).start()
        numero = 0
        while True:
            self._acordar.wait()
            self._acordar.clear()
            if self._parar.is_set():
                return
            if self._erro_leitura is not None:
                raise self._erro_leitura
            with self._trinco:
                nova, self._cfg_nova = self._cfg_nova, None
                passo, self._passo = self._passo, 0
            if nova is not None:
                cfg, mestre = self._trocar(cfg, mestre, nova)
            if ficheiros and passo:
                indice = (indice + passo) % len(ficheiros)
                self._carregar(ficheiros, indice)
            with self._trinco:
                captura = self._captura
            if captura is None:
                continue
            inicio = perf_counter()
            resultado = mestre.inspecionar(captura.imagem, guardar_intermedios=self.etapas)
            resultado.captura = captura
            numero += 1
            self._entregar(Visto(numero, "ao_vivo", resultado, cfg, _ms_desde(inicio)))

    def _carregar(self, ficheiros: list[Path], indice: int) -> None:
        """Ao vivo com uma pasta: a imagem a analisar passa a ser ficheiros[indice]."""
        caminho = ficheiros[indice]
        imagem = ler_imagem(caminho)
        if imagem is None:
            self.mensagem.emit(f"Não é uma imagem válida: {caminho.name}")
            return
        with self._trinco:
            self._captura = Captura(imagem, str(caminho), time.time())
        self.mensagem.emit(f"Imagem {indice + 1} de {len(ficheiros)}: {caminho.name}")
        self._acordar.set()

    def _ler(self) -> None:
        """Ao vivo: lê a fonte sem parar e guarda só a imagem mais recente. Um vídeo recomeça no fim."""
        # Sem trigger: a câmara capta em contínuo (o disparo "virtual" põe o TriggerMode a Off).
        cfg = self.pedido.cfg
        cfg = dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo="virtual"))
        try:
            while not self._parar.is_set():
                self._fonte = criar_fonte(self.pedido.fonte, cfg, video=self.pedido.caminho)
                with self._fonte as fonte:
                    for captura in fonte:
                        if self._parar.is_set():
                            break
                        if not self.congelado:
                            with self._trinco:
                                self._captura = captura
                            self._acordar.set()
                        if not fonte.ao_vivo:  # vídeo: ao ritmo a que foi filmado
                            self._parar.wait(1 / fonte.fps)
                if fonte.ao_vivo and not self._parar.is_set():
                    raise RuntimeError("a câmara deixou de enviar imagens")
        except BaseException as erro:  # o ciclo principal mostra-o
            self._erro_leitura = erro
            self._acordar.set()

    def _trocar(self, cfg: Config, mestre: Mestre, nova: Config) -> tuple[Config, Mestre]:
        """Os parâmetros novos, se o Mestre os aceitar; senão fica tudo como estava."""
        try:
            novo = Mestre(nova)
        except (ValueError, FileNotFoundError) as erro:  # p.ex. localizacao.fundo_referencia em falta
            self.mensagem.emit(f"Parâmetros não aplicados: {erro}")
            return cfg, mestre
        if self.pedido.modo == "producao":
            self.mensagem.emit("Parâmetros aplicados a partir deste saco.")
        return nova, novo


class Previsao(QThread):
    """Volta a analisar um saco já visto com outros parâmetros (pré-visualização), fora da janela."""

    visto = Signal(object)  # Visto preparado, com previsao=True
    mensagem = Signal(str)

    def __init__(self, base: Visto, cfg: Config) -> None:
        super().__init__()
        self.base, self.cfg = base, cfg

    def run(self) -> None:
        captura = self.base.resultado.captura
        try:
            inicio = perf_counter()
            resultado = Mestre(self.cfg).inspecionar(captura.imagem, guardar_intermedios=True)
        except (ValueError, FileNotFoundError) as erro:
            self.mensagem.emit(f"Pré-visualização: {erro}")
            return
        resultado.captura, resultado.passagem = captura, self.base.resultado.passagem
        self.visto.emit(preparar(Visto(self.base.numero, self.base.modo, resultado, self.cfg,
                                       _ms_desde(inicio), previsao=True)))
