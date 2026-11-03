"""Interface, ciclo: a inspeção a correr numa thread, como o main.py, num de dois modos.

    Ao vivo    a câmara capta em contínuo e a barreira virtual separa os sacos
    Trigger    a câmara só capta com o sinal do botão ou do sensor: um saco por sinal

Nos dois: câmara → disparo → uma decisão por saco → M7 (registo.csv, imagens) e a
base de dados das OFs. Os parâmetros e a OF mudados só entram entre sacos, com
aplicar(): um saco já em análise acaba com a OF com que começou. Um saco não OK
para a inspeção até a janela decidir (descartar ou NOK) e chamar prosseguir().

A janela recebe cada saco duas vezes:
    registado  logo a seguir à decisão: para os contadores e o histórico;
    visto      já com a imagem anotada e as etapas prontas a mostrar.
A preparação para o ecrã corre noutra thread e só trata o saco mais recente: a
inspeção nunca espera pela janela, e o tempo de cada saco não muda por ela existir.

Antes de produzir, a Afinacao mostra só a câmara (sem inspeção nem registo), para
acertar a luz, o foco e a posição; a exposição e o ganho mudam sem parar de captar.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, perf_counter

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from mestre import Mestre
from modulos.m1_aquisicao import AquisicaoEmParalelo, criar_disparo, criar_fonte
from modulos.m1_aquisicao.fontes import FonteImagem
from modulos.m7_saida import Depuracao, Saida
from nucleo.config import RAIZ, Config, ConfigAquisicao, ModoDisparo
from nucleo.contrato import Captura, Estado, Resultado
from nucleo.especificacoes import ErroRepositorio, Repositorio


log = logging.getLogger("interface")

MINIATURA_PX = 200  # largura das miniaturas das etapas


@dataclass
class Pedido:
    """O que a janela pede ao ciclo."""

    modo: ModoDisparo  # "ao_vivo" ou "trigger" (sobrepõe-se a cfg.disparo.modo)
    cfg: Config  # já com o produto e a OF por cima da montagem
    saida: Path  # pasta dos resultados
    of: str = ""  # a ordem de fabrico e o produto ("" = sem OF)
    produto: str = ""
    repositorio: Repositorio | None = None  # onde registar cada saco


@dataclass
class Visto:
    """Um saco e o que é preciso para o mostrar."""

    numero: int  # 1, 2, 3... desde o arranque
    resultado: Resultado
    cfg: Config  # a configuração com que foi analisado
    ciclo_ms: float = 0.0  # inspeção + registo (o que tem de caber no orçamento)
    previsao: bool = False  # analisado com parâmetros ainda não aplicados
    # Preenchidos por preparar(), fora da janela:
    preparado: bool = False
    anotada: np.ndarray | None = None  # None numa passagem descartada
    etapas: list[tuple[str, np.ndarray]] = field(default_factory=list)
    miniaturas: list[np.ndarray] = field(default_factory=list)  # uma por etapa


def preparar(visto: Visto) -> Visto:
    """Prepara o Visto para o ecrã: imagem anotada, etapas e miniaturas. Devolve o mesmo Visto."""
    r, cfg = visto.resultado, visto.cfg
    etapas = Depuracao(cfg, RAIZ).etapas(r)  # "original", "anotada", candidatas, intermédias
    anotada = next((img for nome, img in etapas if nome == "anotada"), None)
    if anotada is None and r.imagem is not None:  # o desenho falhou (já está no log)
        anotada = r.imagem.copy()
    visto.anotada, visto.etapas = anotada, etapas
    visto.miniaturas = [_miniatura(img) for _, img in etapas]
    visto.preparado = True
    return visto


def _miniatura(imagem: np.ndarray) -> np.ndarray:
    altura, largura = imagem.shape[:2]
    return cv2.resize(imagem, (MINIATURA_PX, max(1, round(altura * MINIATURA_PX / largura))),
                      interpolation=cv2.INTER_AREA)


def _ms_desde(inicio: float) -> float:
    return (perf_counter() - inicio) * 1000


QUADRO_INTERVALO_S = 0.1  # intervalo entre as imagens mostradas ao vivo
MEDIDAS_INTERVALO_S = 0.5  # intervalo entre as medidas da imagem ao afinar (brilho, nitidez...)


# --- O ciclo ------------------------------------------------------------------------------

class Ciclo(QThread):
    """A inspeção numa thread. A janela cria um por arranque e liga-se aos sinais."""

    registado = Signal(object)  # Visto por preparar, um por saco
    visto = Signal(object)  # Visto preparado para o ecrã (só o mais recente)
    mensagem = Signal(str)  # para a barra de estado (incluindo porque parou)
    quadro = Signal(object)  # a imagem da câmara tal como chega (BGR), umas 10 por segundo, para a ver ao vivo

    def __init__(self, pedido: Pedido) -> None:
        super().__init__()
        self.pedido = pedido
        self.etapas = True  # pedir as imagens das etapas (só a vista de Engenharia as mostra)
        self.rever = True  # parar em cada saco não OK até a janela decidir (prosseguir())
        self.descartadas = 0  # passagens que a análise não acompanhou
        self.pasta_resultados: Path | None = None  # a pasta desta execução
        self._continuar = threading.Event()
        self._trinco = threading.Lock()
        self._parar = threading.Event()
        self._cfg_nova: Config | None = None
        self._ordem_nova: tuple[str, str] | None = None  # (of, produto)
        self._fonte: FonteImagem | None = None
        self._a_preparar: Visto | None = None
        self._ha_para_preparar = threading.Event()
        self._fim = threading.Event()  # o ciclo acabou: a preparação despacha o que falta e termina
        self._ultimo_quadro = 0.0

    # --- Comandos (chamados pela janela) -------------------------------------------------
    def aplicar(self, cfg: Config, of: str | None = None, produto: str | None = None) -> None:
        """Parâmetros novos (e a OF, se dada): a partir do próximo saco."""
        with self._trinco:
            self._cfg_nova = cfg
            if of is not None:
                self._ordem_nova = (of, produto or "")

    def prosseguir(self) -> None:
        """O saco em revisão foi decidido; a inspeção continua."""
        self._continuar.set()

    def parar(self) -> None:
        self._parar.set()
        self._continuar.set()
        if self._fonte is not None:
            self._fonte.parar()  # uma câmara à espera de um saco que não vem sai da espera

    # --- Thread ------------------------------------------------------------------------
    def run(self) -> None:
        preparacao = threading.Thread(target=self._preparar, name="preparar", daemon=True)
        preparacao.start()
        try:
            self._inspecionar()
        except Exception as erro:  # a janela mostra-o; o traceback fica no log
            log.exception("O ciclo parou.")
            self.mensagem.emit(f"Parou: {erro}")
        finally:
            self._fim.set()
            self._ha_para_preparar.set()
            preparacao.join()

    def _mostrar_quadro(self, captura: Captura) -> None:
        """Chamada a cada imagem da câmara: manda uma ao ecrã, no máximo ~10 por segundo."""
        agora = monotonic()
        if agora - self._ultimo_quadro >= QUADRO_INTERVALO_S:
            self._ultimo_quadro = agora
            self.quadro.emit(next(iter(captura.imagens.values())))

    def _entregar(self, visto: Visto) -> None:
        """Um saco decidido: já para os contadores; para o ecrã, depois de preparado."""
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

    def _inspecionar(self) -> None:
        p = self.pedido
        cfg = no_modo(p.cfg, p.modo)
        ordem = (p.of, p.produto)
        mestre = Mestre(cfg, of=p.of, produto=p.produto)
        self._fonte = criar_fonte(cfg)
        aquisicao = AquisicaoEmParalelo(self._fonte, criar_disparo(cfg), cfg.disparo.fila_max, self._mostrar_quadro)
        with aquisicao, Saida(cfg, p.saida, mestre.nomes) as saida:
            self.pasta_resultados = saida.pasta
            self.mensagem.emit(f"Resultados em {saida.pasta}")
            for numero, passagem in enumerate(aquisicao, start=1):
                with self._trinco:
                    nova, self._cfg_nova = self._cfg_nova, None
                    ordem_nova, self._ordem_nova = self._ordem_nova, None
                if nova is not None:
                    cfg, mestre, ordem = self._trocar(cfg, mestre, no_modo(nova, p.modo), ordem, ordem_nova or ordem)
                    # O desenho e o registo também usam a configuração (px_por_mm).
                    saida.cfg = cfg
                    if saida.depuracao is not None:
                        saida.depuracao.cfg = cfg
                inicio = perf_counter()
                intermedios = self.etapas or saida.depuracao is not None
                resultado = mestre.inspecionar_passagem(passagem, guardar_intermedios=intermedios)
                saida.registar(resultado)
                if p.repositorio is not None:
                    try:
                        p.repositorio.registar(resultado, saida.pasta.name, numero, cfg)
                    except ErroRepositorio as erro:  # sem base de dados a linha continua: o registo.csv tem tudo
                        log.warning("%s", erro)
                ciclo_ms = _ms_desde(inicio)
                self.descartadas = aquisicao.descartadas
                rever = self.rever and resultado.estado is not Estado.OK
                if rever:
                    self._continuar.clear()  # antes de entregar: a decisão pode chegar logo
                self._entregar(Visto(numero, resultado, cfg, ciclo_ms))
                if rever:
                    self._continuar.wait()  # até a janela decidir (ou parar)
                if self._parar.is_set():
                    break
        if not self._parar.is_set():
            self.mensagem.emit("A câmara deixou de enviar imagens.")

    def _trocar(self, cfg: Config, mestre: Mestre, nova: Config, ordem: tuple[str, str],
                ordem_nova: tuple[str, str]) -> tuple[Config, Mestre, tuple[str, str]]:
        """Os parâmetros (e a OF) novos, se o Mestre os aceitar; senão fica tudo como estava."""
        try:
            novo = Mestre(nova, of=ordem_nova[0], produto=ordem_nova[1])
        except (ValueError, FileNotFoundError) as erro:  # p.ex. localizacao.fundo_referencia em falta
            self.mensagem.emit(f"Parâmetros não aplicados: {erro}")
            return cfg, mestre, ordem
        if ordem_nova != ordem:
            self.mensagem.emit(f"{f'OF {ordem_nova[0]}' if ordem_nova[0] else 'Sem OF'} a partir deste saco.")
        else:
            self.mensagem.emit("Parâmetros aplicados a partir deste saco.")
        return nova, novo, ordem_nova


def no_modo(cfg: Config, modo: ModoDisparo) -> Config:
    """A configuração com o modo de disparo escolhido na janela (Ao vivo ou Trigger)."""
    return dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo=modo))


class Afinacao(QThread):
    """A câmara sem inspeção nem registo: para acertar a luz, o foco, a posição e o trigger antes de produzir.

    Ao vivo    a câmara capta em contínuo (umas 10 imagens por segundo no ecrã)
    Trigger    uma imagem por sinal do botão ou do sensor (os ressaltos, a menos de
               disparo.anti_ressalto_ms do anterior, são ignorados como na inspeção)
    """

    quadro = Signal(object)  # a imagem (BGR): ao vivo umas 10 por segundo; em Trigger, uma por sinal
    medidas = Signal(str)  # brilho, saturados, nitidez (medidas_imagem) e imagens por segundo ou nº do sinal
    mensagem = Signal(str)

    def __init__(self, cfg: Config, modo: ModoDisparo = "ao_vivo") -> None:
        super().__init__()
        self.modo = modo
        self._cfg = no_modo(cfg, modo)
        self._nova: Config | None = None
        self._trinco = threading.Lock()
        self._parar = threading.Event()
        self._fonte: FonteImagem | None = None

    def aplicar(self, cfg: Config) -> None:
        """Valores novos: a exposição e o ganho mudam já; o resto da câmara, reabrindo-a.
        Em Trigger a câmara está à espera do sinal: sai da espera, para a imagem seguinte já os usar."""
        nova = no_modo(cfg, self.modo)
        with self._trinco:
            self._nova = nova
        if self.modo == "trigger" and self._fonte is not None and _da_camara(nova) != _da_camara(self._cfg):
            self._fonte.parar()

    def _ha_valores_novos(self) -> bool:
        with self._trinco:
            return self._nova is not None

    def parar(self) -> None:
        self._parar.set()
        if self._fonte is not None:
            self._fonte.parar()

    def run(self) -> None:
        try:
            self._fonte = criar_fonte(self._cfg)
            try:
                self._mostrar()
            finally:
                self._fonte.fechar()  # repõe os parâmetros originais (a câmara é partilhada)
        except Exception as erro:  # a janela mostra-o; o traceback fica no log
            log.exception("A afinação parou.")
            self.mensagem.emit(f"Afinar a câmara: {erro}")

    def _mostrar(self) -> None:
        ultimo_quadro = ultima_medida = monotonic()
        imagens = sinais = 0
        ultimo_sinal: float | None = None
        while not self._parar.is_set():
            self._trocar()
            captura = self._fonte.ler()
            if captura is None:
                if self._parar.is_set() or self._ha_valores_novos():
                    continue  # a parar, ou saiu da espera do sinal para aplicar valores novos
                self.mensagem.emit("A câmara deixou de enviar imagens.")
                return
            imagem = next(iter(captura.imagens.values()))
            if self.modo == "trigger":
                if ultimo_sinal is not None and \
                        (captura.instante - ultimo_sinal) * 1000 < self._cfg.disparo.anti_ressalto_ms:
                    continue  # ressalto do botão
                ultimo_sinal = captura.instante
                sinais += 1
                self.quadro.emit(imagem)
                self.medidas.emit(f"{medidas_imagem(imagem)}   ·   sinal n.º {sinais}")
                continue
            imagens += 1
            agora = monotonic()
            if agora - ultimo_quadro >= QUADRO_INTERVALO_S:
                ultimo_quadro = agora
                self.quadro.emit(imagem)
            if agora - ultima_medida >= MEDIDAS_INTERVALO_S:
                self.medidas.emit(f"{medidas_imagem(imagem)}   ·   {imagens / (agora - ultima_medida):.0f} img/s")
                ultima_medida, imagens = agora, 0

    def _trocar(self) -> None:
        """Os valores pedidos com aplicar(), se os houver."""
        with self._trinco:
            nova, self._nova = self._nova, None
        if nova is None or (_da_camara(nova) == _da_camara(self._cfg) and not self._fonte.parada):
            return
        so_luz = (nova.camara == self._cfg.camara and nova.disparo == self._cfg.disparo
                  and _sem_luz(nova) == _sem_luz(self._cfg) and not self._fonte.parada)
        try:
            if not (so_luz and self._fonte.ajustar(nova.aquisicao)):
                self._fonte.fechar()
                self._fonte = criar_fonte(nova)
                if self._parar.is_set():  # parar() chegou durante a reabertura: foi à câmara antiga
                    self._fonte.parar()
            self._cfg = nova
        except RuntimeError as erro:
            self.mensagem.emit(f"Câmara: valores não aplicados ({erro})")


def _da_camara(cfg: Config) -> tuple:
    """O que a fonte usa: só uma mudança nisto mexe na câmara (as do M3, p.ex., não)."""
    return cfg.camara, cfg.aquisicao, cfg.disparo


def _sem_luz(cfg: Config) -> ConfigAquisicao:
    """A aquisição sem a exposição e o ganho: se só esses mudaram, a câmara não precisa de ser reaberta."""
    return dataclasses.replace(cfg.aquisicao, exposicao_us=1.0, ganho_raw=0)


def medidas_imagem(imagem: np.ndarray) -> str:
    """Para afinar: o brilho médio (0 a 255), os píxeis saturados e a nitidez (variância do Laplaciano;
    só serve para comparar com ela própria: quanto maior, mais focada)."""
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    saturados = 100 * np.count_nonzero(cinza == 255) / cinza.size
    nitidez = cv2.Laplacian(cinza, cv2.CV_64F).var()
    return f"brilho {cinza.mean():.0f}   ·   saturados {saturados:.1f} %   ·   nitidez {nitidez:.0f}"


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
        self.visto.emit(preparar(Visto(self.base.numero, resultado, self.cfg, _ms_desde(inicio), previsao=True)))
