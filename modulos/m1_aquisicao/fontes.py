"""M1 Aquisição, fontes: de onde vêm as imagens. Só câmaras.

    FonteBasler     câmara Basler acA1300-30gm (GigE legacy, partilhada), através do pypylon
    (Blackfly)      câmara FLIR Blackfly S, através do PySpin: comentada no fim deste
                    ficheiro, para ligar mais tarde

Todas as fontes têm a mesma interface (FonteImagem) e entregam Capturas, uma
imagem de cada vez, em BGR uint8 (a convenção do OpenCV). Quem junta as imagens
em passagens de sacos é o disparo (disparo.py). A câmara é configurada conforme
disparo.modo: "ao_vivo" (contínuo, a disparo.fps) ou "trigger" (uma imagem por
cada sinal na entrada disparo.trigger_linha).

O pypylon só é importado dentro da FonteBasler: o resto do projeto (e os testes)
funcionam numa máquina sem ele.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar, Self

from modulos.m1_aquisicao.preprocessamento import carregar_calibracao, preparar
from nucleo.config import Config, ConfigAquisicao
from nucleo.contrato import Captura

log = logging.getLogger(__name__)


class FonteImagem(ABC):
    """Base de todas as fontes de imagem.

    Uma fonte abre o dispositivo no construtor e liberta-o em fechar(). É
    iterável (a iteração termina quando ler() devolve None ou depois de
    parar()) e é um gestor de contexto, o que garante que fechar() é chamado
    mesmo que haja uma exceção.
    """

    # True nas câmaras: as imagens chegam ao ritmo da linha e não se pode esperar por elas.
    ao_vivo: ClassVar[bool] = True
    # Posto a True por parar(), noutra thread. Uma fonte que espera por imagens
    # (câmara com trigger, sem nenhum saco a passar) verifica-o entre esperas.
    parada: bool = False

    @abstractmethod
    def ler(self) -> Captura | None:
        """Adquire a imagem seguinte.

        Devolve:
            A Captura seguinte, ou None quando a fonte terminou (câmara desligada,
            fonte fechada). Falhas pontuais (p.ex. uma imagem incompleta) devem ser
            resolvidas cá dentro, tentando de novo.
        """

    @abstractmethod
    def fechar(self) -> None:
        """Liberta os recursos da fonte. Tem de poder ser chamado mais de uma vez."""

    def parar(self) -> None:
        """Pede à fonte que termine; pode ser chamado de outra thread.

        Um ler() à espera de uma imagem que não vem tem de ver self.parada
        entre esperas e devolver None; senão a thread de aquisição nunca
        termina e fechar() nunca chega a ser chamado.
        """
        self.parada = True

    def ajustar(self, aquisicao: ConfigAquisicao) -> bool:
        """Muda a exposição e o ganho sem parar de captar (para afinar a câmara ao vivo).

        Devolve False se esta fonte não o sabe fazer: quem chama fecha-a e abre outra.
        """
        return False

    def __iter__(self) -> Iterator[Captura]:
        while not self.parada and (captura := self.ler()) is not None:
            yield captura

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *excecao: object) -> None:
        self.fechar()


def criar_fonte(cfg: Config, numero_serie: str | None = None) -> FonteImagem:
    """A câmara, já aberta e configurada para cfg.disparo.modo.

    numero_serie: o da câmara a usar (None = a primeira encontrada).
    Para passar à Blackfly: descomentar a FonteSpinnaker (no fim deste ficheiro) e devolvê-la aqui.
    """
    return FonteBasler(cfg, numero_serie)


# --- Câmara Basler acA1300-30gm (pypylon) ------------------------------------------------

class FonteBasler(FonteImagem):
    """Câmara Basler GigE legacy (acA1300-30gm) através do pypylon. A câmara é PARTILHADA.

    Abre-se pelo nº de série (camara.serial); se não aparecer na procura e houver camara.ip,
    liga-se por esse IP e confirma o nº de série. Muda só a exposição (ExposureTimeAbs), o
    ganho (GainRaw) e o trigger, guardando antes o valor original de cada um, e repõe-os ao
    fechar (o trigger primeiro). Nunca grava nem carrega User Sets.

    Modos (cfg.disparo.modo):
        "ao_vivo"  TriggerMode = Off; a câmara capta em contínuo e ler() dá a imagem mais
                   recente (GrabStrategy_LatestImageOnly). Captura.sensor = None.
        "trigger"  TriggerMode = On na entrada disparo.trigger_linha: uma imagem por sinal, pela
                   ordem (GrabStrategy_OneByOne). Captura.sensor = True e Captura.instante pelo
                   relógio da câmara, para o disparo ignorar os ressaltos do botão.
    """

    def __init__(self, cfg: Config, numero_serie: str | None = None) -> None:
        self.cfg = cfg
        self.numero_serie = numero_serie or cfg.camara.serial
        self.trigger = cfg.disparo.modo == "trigger"
        self.calibracao = carregar_calibracao(cfg.aquisicao.calibracao) if cfg.aquisicao.calibracao else None
        self._camara: Any = None  # pylon.InstantCamera
        self._originais: list[tuple[str, Any]] = []  # (parâmetro, valor antes de o mudar), pela ordem
        self._relogio: tuple[int, float] | None = None  # (1.º timestamp da câmara, time.time() nesse momento)
        self._frequencia: float | None = None  # ticks por segundo do relógio da câmara (None = usar o do PC)
        self._abrir()

    def _abrir(self) -> None:
        try:
            from pypylon import genicam, pylon
        except ImportError as erro:
            raise RuntimeError("Câmara Basler: falta o pypylon (pip install pypylon).") from erro
        if not self.numero_serie:
            raise RuntimeError("Câmara Basler: falta o nº de série no perfil (camara.serial).")
        fabrica = pylon.TlFactory.GetInstance()
        info = next((d for d in fabrica.EnumerateDevices() if d.GetSerialNumber() == self.numero_serie), None)
        if info is None and self.cfg.camara.ip:  # p.ex. noutra sub-rede: ligar diretamente pelo IP
            info = pylon.DeviceInfo()
            info.SetDeviceClass("BaslerGigE")
            info.SetIpAddress(self.cfg.camara.ip)
        if info is None:
            raise RuntimeError(f"Câmara Basler {self.numero_serie} não encontrada (cabo, alimentação, IP da placa de "
                               "rede; ou pôr o IP da câmara em camara.ip).")
        try:
            self._camara = pylon.InstantCamera(fabrica.CreateDevice(info))
            self._camara.Open()
        except genicam.GenericException as erro:
            self._camara = None
            raise RuntimeError(f"Câmara Basler {self.numero_serie}: não abriu; está em uso por outra aplicação "
                               f"(p.ex. o pylon Viewer) ou inacessível ({erro}).") from erro
        serie = self._camara.GetDeviceInfo().GetSerialNumber()
        if serie != self.numero_serie:  # pelo IP respondeu outra câmara
            self.fechar()
            raise RuntimeError(f"Câmara Basler: em {self.cfg.camara.ip} está a {serie}, não a {self.numero_serie}.")
        try:
            self._configurar()
            tick = self._camara.GetNodeMap().GetNode("GevTimestampTickFrequency")
            self._frequencia = tick.GetValue() if genicam.IsReadable(tick) else None  # ticks por segundo
            self._camara.StartGrabbing(pylon.GrabStrategy_OneByOne if self.trigger
                                       else pylon.GrabStrategy_LatestImageOnly)
        except genicam.GenericException as erro:
            self.fechar()
            raise RuntimeError(f"Câmara Basler {self.numero_serie}: {erro}") from erro
        log.info("Câmara Basler %s (nº %s), modo %s", self._camara.GetDeviceInfo().GetModelName(),
                 self.numero_serie, self.cfg.disparo.modo)

    def _mudar(self, nome: str, valor: Any) -> None:
        """Muda um parâmetro da câmara, guardando antes o valor original (reposto em fechar()).
        Um número fora da gama da câmara (p.ex. GainRaw 0 com mínimo 300) fica no limite, com um aviso."""
        no = getattr(self._camara, nome)
        if isinstance(valor, int | float):
            minimo, maximo = no.GetMin(), no.GetMax()
            if not minimo <= valor <= maximo:
                limitado = type(valor)(min(max(valor, minimo), maximo))
                log.warning("Câmara Basler: %s = %s fora da gama da câmara (%s a %s); usado %s.",
                            nome, valor, minimo, maximo, limitado)
                valor = limitado
        if all(n != nome for n, _ in self._originais):  # o original é o de antes da 1.ª mudança
            self._originais.append((nome, no.GetValue()))
        no.SetValue(valor)

    def _configurar(self) -> None:
        """Exposição, ganho e trigger (nomes do modelo legacy). Nunca User Sets: a câmara é partilhada."""
        c, a, d = self._camara, self.cfg.aquisicao, self.cfg.disparo
        if c.TriggerMode.GetValue() == "On":
            log.warning("A câmara %s já vinha com TriggerMode=On (deixado por outra aplicação?).", self.numero_serie)
        self._mudar("ExposureTimeAbs", float(a.exposicao_us))
        if a.ganho_raw >= 0:
            self._mudar("GainRaw", int(a.ganho_raw))
        self._mudar("TriggerSelector", "FrameStart")
        if self.trigger:
            self._mudar("TriggerSource", d.trigger_linha)
            self._mudar("TriggerActivation", d.trigger_ativacao)
            self._mudar("TriggerMode", "On")
        else:
            self._mudar("TriggerMode", "Off")  # ao vivo: a captar em contínuo

    def ajustar(self, aquisicao: ConfigAquisicao) -> bool:
        """A exposição e o ganho, com a câmara a captar; fechar() continua a repor os originais."""
        from pypylon import genicam

        if self._camara is None:
            return False
        try:
            self._mudar("ExposureTimeAbs", float(aquisicao.exposicao_us))
            if aquisicao.ganho_raw >= 0:
                self._mudar("GainRaw", int(aquisicao.ganho_raw))
        except genicam.GenericException as erro:  # p.ex. fora dos limites da câmara
            raise RuntimeError(f"Câmara Basler {self.numero_serie}: {erro}") from erro
        self.cfg = dataclasses.replace(self.cfg, aquisicao=aquisicao)
        return True

    def adquirir(self) -> Captura | None:
        """Uma só imagem (GrabOne), p.ex. para ver se a câmara responde; None se não chegar a tempo.
        Para a aquisição contínua (é para uma imagem solta, não para usar com ler())."""
        from pypylon import pylon

        self._camara.StopGrabbing()
        resultado = self._camara.GrabOne(self.cfg.aquisicao.timeout_ms, pylon.TimeoutHandling_Return)
        try:
            return self._captura(resultado) if resultado.IsValid() and resultado.GrabSucceeded() else None
        finally:
            resultado.Release()

    def ler(self) -> Captura | None:
        """A imagem seguinte. Com trigger, espera pelo sinal (sem erro) até parar()."""
        from pypylon import genicam, pylon

        while not self.parada and self._camara is not None and self._camara.IsGrabbing():
            try:
                resultado = self._camara.RetrieveResult(self.cfg.aquisicao.timeout_ms, pylon.TimeoutHandling_Return)
            except genicam.GenericException as erro:  # a câmara desligou-se
                log.error("Câmara Basler: %s", erro)
                return None
            if not resultado.IsValid():
                continue  # nada no tempo limite: normal com trigger (não passou nenhum saco)
            try:
                if resultado.GrabSucceeded():
                    return self._captura(resultado)
                log.warning("Câmara Basler: imagem incompleta (%s)", resultado.GetErrorDescription())
            finally:
                resultado.Release()
        return None

    def _captura(self, resultado: Any) -> Captura:
        """A Captura de uma imagem da câmara, com o instante pelo relógio da câmara (se o tiver)."""
        bruta = resultado.GetArray().copy()  # copy: o array aponta para memória do pylon
        if self._frequencia is None:
            instante = time.time()
        else:
            if self._relogio is None:
                self._relogio = (resultado.GetTimeStamp(), time.time())
            instante = self._relogio[1] + (resultado.GetTimeStamp() - self._relogio[0]) / self._frequencia
        cal = self.calibracao
        return Captura(preparar(bruta, self.cfg.camara.formato_pixel, cal, self.cfg.aquisicao.roi()),
                       f"basler:{self.numero_serie}#{resultado.GetBlockID()}", instante,
                       sensor=True if self.trigger else None, fonte="basler",
                       px_por_mm=cal.px_por_mm if cal else None, calibracao_id=cal.id if cal else None)

    def fechar(self) -> None:
        """Para a aquisição e repõe os parâmetros pela ordem inversa (o trigger primeiro, o
        TriggerSelector no fim dele); cada um com o seu try. Pode ser chamado várias vezes."""
        camara, self._camara = self._camara, None
        if camara is None:
            return
        try:
            camara.StopGrabbing()
        except Exception as erro:
            log.warning("Câmara Basler: ao parar: %s", erro)
        while self._originais:
            nome, valor = self._originais.pop()
            try:
                getattr(camara, nome).SetValue(valor)
            except Exception as erro:
                log.warning("Câmara Basler: não foi possível repor %s = %s: %s", nome, valor, erro)
        try:
            camara.Close()
        except Exception as erro:
            log.warning("Câmara Basler: ao fechar: %s", erro)


# --- Câmara FLIR Blackfly S (Spinnaker): para ligar mais tarde ------------------------------
# Para a usar: instalar o PySpin (wheel do Spinnaker em instaladores\), descomentar esta classe
# e devolvê-la em criar_fonte(). A Blackfly (BFS-PGE-50S5C-C) tem a entrada opto-isolada na
# Line0 (disparo.trigger_linha = "Line0") e o padrão de cor BayerRG8.
#
# class FonteSpinnaker(FonteImagem):
#     """Câmara FLIR Blackfly S (PoE GigE) através do PySpin."""
#
#     def __init__(self, cfg: Config, numero_serie: str | None = None) -> None:
#         import PySpin
#
#         self.cfg, self.timeout_ms, self.parada = cfg, cfg.aquisicao.timeout_ms, False
#         self.trigger = cfg.disparo.modo == "trigger"
#         self.calibracao = carregar_calibracao(cfg.aquisicao.calibracao) if cfg.aquisicao.calibracao else None
#         self._sistema = PySpin.System.GetInstance()
#         self._lista = self._sistema.GetCameras()
#         if self._lista.GetSize() == 0:
#             self.fechar()
#             raise RuntimeError("Blackfly: nenhuma câmara encontrada (placa de rede, IP).")
#         self._camara = self._lista.GetBySerial(numero_serie) if numero_serie else self._lista.GetByIndex(0)
#         self._camara.Init()
#         c, a, d = self._camara, cfg.aquisicao, cfg.disparo
#         c.TriggerMode.SetValue(PySpin.TriggerMode_Off)
#         c.GevSCPSPacketSize.SetValue(cfg.camara.pacote_bytes)
#         c.PixelFormat.SetValue(getattr(PySpin, f"PixelFormat_{cfg.camara.formato_pixel}"))
#         c.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
#         c.ExposureTime.SetValue(a.exposicao_us)
#         c.GainAuto.SetValue(PySpin.GainAuto_Off)
#         c.Gain.SetValue(a.ganho_db)
#         c.BalanceWhiteAuto.SetValue(PySpin.BalanceWhiteAuto_Off)
#         if self.trigger:
#             c.LineSelector.SetValue(getattr(PySpin, f"LineSelector_{d.trigger_linha}"))
#             c.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
#             c.TriggerSource.SetValue(getattr(PySpin, f"TriggerSource_{d.trigger_linha}"))
#             c.TriggerActivation.SetValue(getattr(PySpin, f"TriggerActivation_{d.trigger_ativacao}"))
#             c.TriggerMode.SetValue(PySpin.TriggerMode_On)
#         else:
#             c.AcquisitionFrameRateEnable.SetValue(True)
#             c.AcquisitionFrameRate.SetValue(d.fps)
#         c.TLStream.StreamBufferHandlingMode.SetValue(PySpin.StreamBufferHandlingMode_OldestFirst)
#         c.BeginAcquisition()
#         self.numero_serie = c.DeviceSerialNumber.GetValue()
#
#     def ler(self) -> Captura | None:
#         import PySpin
#
#         while not self.parada and self._camara is not None:
#             try:
#                 img = self._camara.GetNextImage(self.timeout_ms)
#             except PySpin.SpinnakerException:
#                 continue  # sem imagem no tempo limite: normal com trigger
#             try:
#                 if img.IsIncomplete():
#                     continue
#                 bruta, numero = img.GetNDArray().copy(), img.GetFrameID()
#             finally:
#                 img.Release()
#             bgr = preparar(bruta, self.cfg.camara.formato_pixel, self.calibracao, self.cfg.aquisicao.roi())
#             cal = self.calibracao
#             return Captura(bgr, f"spinnaker:{self.numero_serie}#{numero}", time.time(),
#                            sensor=True if self.trigger else None, fonte="spinnaker",
#                            px_por_mm=cal.px_por_mm if cal else None, calibracao_id=cal.id if cal else None)
#         return None
#
#     def fechar(self) -> None:
#         if getattr(self, "_camara", None) is not None:
#             try:
#                 self._camara.EndAcquisition()
#             except Exception:
#                 pass
#             self._camara.TriggerMode.SetValue(0)
#             self._camara.DeInit()
#             self._camara = None
#         if getattr(self, "_lista", None) is not None:
#             self._lista.Clear()
#             self._lista = None
#         if getattr(self, "_sistema", None) is not None:
#             self._sistema.ReleaseInstance()
#             self._sistema = None
