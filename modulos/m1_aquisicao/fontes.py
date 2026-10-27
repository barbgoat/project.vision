"""M1 Aquisição, fontes: de onde vêm as imagens.

Todas as fontes têm a mesma interface (FonteImagem) e entregam Capturas, uma
imagem de cada vez. Quem junta as imagens em passagens de sacos é o disparo
(disparo.py).

As imagens são sempre entregues em BGR uint8 (a convenção do OpenCV).
O PySpin só é importado dentro de FonteSpinnaker, para que o resto do projeto
funcione em máquinas sem o Spinnaker SDK.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Literal, Self

import cv2

from nucleo.config import Config
from nucleo.contrato import Captura
from nucleo.imagens import ler_imagem

log = logging.getLogger(__name__)

EXTENSOES_IMAGEM = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")

TipoFonte = Literal["pasta", "video", "webcam", "spinnaker"]


class FonteImagem(ABC):
    """Base de todas as fontes de imagem.

    Uma fonte abre o dispositivo no construtor e liberta-o em fechar(). É
    iterável (a iteração termina quando ler() devolve None) e é um gestor de
    contexto, o que garante que fechar() é chamado mesmo que haja uma exceção.
    """

    # True nas fontes ao vivo (câmaras): as imagens chegam ao ritmo da linha e
    # não se pode esperar por elas; False nas gravadas (pasta, vídeo).
    ao_vivo: ClassVar[bool] = False

    @abstractmethod
    def ler(self) -> Captura | None:
        """Adquire a imagem seguinte.

        Devolve:
            A Captura seguinte, ou None quando a fonte terminou (fim da pasta,
            câmara desligada, fonte fechada). Falhas pontuais (p.ex. uma imagem
            incompleta da câmara) devem ser resolvidas cá dentro, tentando de novo.
        """

    @abstractmethod
    def fechar(self) -> None:
        """Liberta os recursos da fonte. Tem de poder ser chamado mais de uma vez."""

    def __iter__(self) -> Iterator[Captura]:
        while (captura := self.ler()) is not None:
            yield captura

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *excecao: object) -> None:
        self.fechar()


def criar_fonte(
    tipo: TipoFonte,
    cfg: Config,
    pasta: str | Path | None = None,
    video: str | Path | None = None,
    indice: int = 0,
    numero_serie: str | None = None,
) -> FonteImagem:
    """Cria a fonte pedida pelo Mestre.

    Recebe:
        tipo: "pasta", "video", "webcam" ou "spinnaker".
        cfg: configuração (usa as secções aquisicao e disparo).
        pasta: para "pasta", a pasta com as imagens (inclui subpastas).
        video: para "video", o ficheiro de vídeo.
        indice: para "webcam", o número da câmara no sistema.
        numero_serie: para "spinnaker", o nº de série (None = a primeira).
    Devolve:
        A fonte, já aberta.
    """
    if tipo == "pasta":
        if pasta is None:
            raise ValueError("A fonte 'pasta' precisa do caminho da pasta.")
        # Com fotos soltas o instante não importa; numa sequência (modos sensor/virtual)
        # as imagens ficam espaçadas como se tivessem sido tiradas a disparo.fps.
        fps = None if cfg.disparo.modo == "foto" else cfg.disparo.fps
        return FontePasta(pasta, fps=fps)
    if tipo == "video":
        if video is None:
            raise ValueError("A fonte 'video' precisa do caminho do ficheiro de vídeo.")
        return FonteVideo(video)
    if tipo == "webcam":
        return FonteWebcam(indice)
    if tipo == "spinnaker":
        return FonteSpinnaker(cfg, numero_serie)
    raise ValueError(f"Tipo de fonte desconhecido: {tipo!r}")


# --- Pasta de fotografias ----------------------------------------------------------

class FontePasta(FonteImagem):
    """Imagens de uma pasta (e subpastas), por ordem alfabética do caminho.

    Captura.origem é o caminho do ficheiro; no dataset, o nome da pasta-mãe
    (ok, risco, rugas...) é a classe real da imagem.
    """

    def __init__(self, pasta: str | Path, recursiva: bool = True, fps: float | None = None) -> None:
        """Lista as imagens da pasta.

        Recebe:
            pasta: a pasta.
            recursiva: incluir as subpastas.
            fps: se dado, as imagens são tratadas como uma sequência filmada a
                esta cadência (Captura.instante = início + n/fps), para testar
                o disparo com imagens tiradas de um vídeo.
        """
        self.pasta = Path(pasta)
        if not self.pasta.is_dir():
            raise FileNotFoundError(f"Pasta não encontrada: {self.pasta}")
        padrao = "**/*" if recursiva else "*"
        self.ficheiros = sorted(
            p for p in self.pasta.glob(padrao) if p.suffix.lower() in EXTENSOES_IMAGEM and p.is_file()
        )
        if not self.ficheiros:
            log.warning("Nenhuma imagem (%s) em %s", " ".join(EXTENSOES_IMAGEM), self.pasta)
        self.fps = fps
        self._inicio = time.time()
        self._seguinte = 0

    def __len__(self) -> int:
        return len(self.ficheiros)

    def ler(self) -> Captura | None:
        while self._seguinte < len(self.ficheiros):
            caminho = self.ficheiros[self._seguinte]
            indice = self._seguinte
            self._seguinte += 1
            imagem = ler_imagem(caminho)
            if imagem is not None:
                instante = self._inicio + indice / self.fps if self.fps else time.time()
                return Captura(imagem, str(caminho), instante)
            log.warning("Ficheiro ignorado (não é uma imagem válida): %s", caminho)
        return None

    def fechar(self) -> None:
        # Não há nada aberto; basta terminar a iteração.
        self._seguinte = len(self.ficheiros)


# --- Vídeo ---------------------------------------------------------------------------

class FonteVideo(FonteImagem):
    """Um ficheiro de vídeo, imagem a imagem (p.ex. sacos filmados com o telemóvel).

    Serve para testar o disparo antes de haver câmara: filmar os sacos a passar
    e usar o modo "virtual". No telemóvel, filmar em H.264 ("Mais compatível"
    no iPhone): o OpenCV pode não ler vídeos HEVC.
    """

    def __init__(self, caminho: str | Path) -> None:
        self.caminho = Path(caminho)
        if not self.caminho.is_file():
            raise FileNotFoundError(f"Vídeo não encontrado: {self.caminho}")
        self._copia: Path | None = None
        self._video = cv2.VideoCapture(str(self.caminho))
        if not self._video.isOpened() and not str(self.caminho).isascii():
            # O OpenCV não abre vídeos com acentos no caminho, no Windows: usar uma cópia temporária.
            self._copia = Path(tempfile.mkdtemp()) / f"video{self.caminho.suffix}"
            shutil.copyfile(self.caminho, self._copia)
            self._video = cv2.VideoCapture(str(self._copia))
        if not self._video.isOpened():
            self.fechar()
            raise RuntimeError(f"Não foi possível abrir o vídeo (formato não suportado?): {self.caminho}")
        self.fps = self._video.get(cv2.CAP_PROP_FPS) or 30.0
        self._inicio = time.time()
        self._contador = 0

    def ler(self) -> Captura | None:
        if self._video is None:
            return None
        ok, imagem = self._video.read()
        if not ok:
            return None
        self._contador += 1
        # Instante do vídeo (não do relógio): as durações das passagens ficam as reais.
        instante = self._inicio + (self._contador - 1) / self.fps
        return Captura(imagem, f"{self.caminho.name}#{self._contador}", instante)

    def fechar(self) -> None:
        if self._video is not None:
            self._video.release()
            self._video = None
        if self._copia is not None:
            shutil.rmtree(self._copia.parent, ignore_errors=True)
            self._copia = None


# --- Webcam -------------------------------------------------------------------------

class FonteWebcam(FonteImagem):
    """Webcam USB, para testes de bancada, através de cv2.VideoCapture.

    A webcam tem outra resolução e outra distância ao tapete: usar um
    config.json próprio, com o px_por_mm calibrado para ela. Não tem entrada
    para sensor: usar o disparo "virtual".
    """

    ao_vivo = True

    def __init__(self, indice: int = 0, largura: int | None = None, altura: int | None = None) -> None:
        """Abre a webcam.

        Recebe:
            indice: número da câmara no sistema (0 = a primeira).
            largura, altura: resolução a pedir à câmara (None = manter a atual).
        """
        self.indice = indice
        self.largura = largura
        self.altura = altura
        self._video: cv2.VideoCapture | None = None
        self._contador = 0  # nº de imagens lidas, para a origem "webcam<índice>#<n>"
        self._abrir()

    def _abrir(self) -> None:
        """Liga-se à webcam.

        O que vai fazer:
            1. cv2.VideoCapture(indice, cv2.CAP_DSHOW): no Windows, o DirectShow
               abre muito mais depressa do que o backend por omissão (MSMF).
            2. Se pedida, definir a resolução (CAP_PROP_FRAME_WIDTH/HEIGHT).
            3. Se isOpened() for falso, levantar RuntimeError com o índice.
        """
        raise NotImplementedError("FonteWebcam ainda não implementada.")

    def ler(self) -> Captura | None:
        """Lê a imagem seguinte da webcam.

        Devolve:
            Captura com origem "webcam<índice>#<n>" (sensor = None), ou None se
            a webcam deixar de responder.
        O que vai fazer:
            ok, imagem = self._video.read(); tolerar algumas falhas seguidas
            antes de desistir e devolver None.
        """
        raise NotImplementedError("FonteWebcam ainda não implementada.")

    def fechar(self) -> None:
        """Liberta a webcam (self._video.release()), se estiver aberta."""
        raise NotImplementedError("FonteWebcam ainda não implementada.")


# --- Câmara FLIR Blackfly S (Spinnaker) --------------------------------------------

class FonteSpinnaker(FonteImagem):
    """Câmara FLIR Blackfly S BFS-PGE-50S5C-C (PoE GigE, 5 MP, cor, Sony IMX264) através do PySpin.

    Ensaios: ver as fases (sem tapete, tapete parado, tapete a andar) em
    modulos/m1_aquisicao/__init__.py. Enquanto não houver sensor de presença,
    usar o disparo "virtual".

    Rede (GigE, ~120 MB/s): a 24 FPS a resolução total ocupa a ligação toda.
        - Uma placa de rede só para a câmara (sem switch partilhado), com
          "Jumbo Packet" = 9014 nas propriedades da placa no Windows.
        - Câmara e placa na mesma sub-rede: IP fixo na placa (p.ex. 169.254.0.1 /
          255.255.0.0) e, no SpinView, "Auto Force IP" na câmara se ela aparecer
          noutra sub-rede.
        - Alimentação: por PoE (switch ou injetor PoE entre a câmara e o PC),
          ou pelo conector GPIO.
        - Ao depurar com pontos de paragem, a câmara desliga-se ao fim do
          GevHeartbeatTimeout; aumentá-lo só durante a depuração.
    Botão ou sensor de presença: liga-se à entrada opto-isolada Line0 do
    conector GPIO (Hirose HR10 de 6 pinos). A entrada opto-isolada precisa de
    uma fonte de tensão externa: botão normalmente aberto em série entre essa
    fonte e a Line0, retorno no OPTO_GND. Ligar primeiro o OPTO_GND e só
    depois aplicar tensão. Pinos, tensão máxima e resistência (se for precisa):
    confirmar no guia de instalação da BFS-PGE antes de ligar. O botão de agora
    e o sensor de mais tarde usam os mesmos fios.
    Ligado à câmara, o sinal fica com a precisão do relógio da câmara; ligá-lo
    a um PLC que depois avisa o PC acrescenta atrasos variáveis.
    """

    ao_vivo = True

    def __init__(self, cfg: Config, numero_serie: str | None = None, timeout_ms: int = 1000) -> None:
        """Abre e configura a câmara (incluindo o trigger) e inicia a aquisição.

        Recebe:
            cfg: configuração; usa as secções camara (características),
                aquisicao (exposição, ganho) e disparo (modo, fps, sensor, rajada).
            numero_serie: nº de série da câmara (None = a primeira encontrada).
            timeout_ms: tempo máximo de espera por cada imagem em ler().
        """
        self.cfg = cfg
        self.numero_serie = numero_serie
        self.timeout_ms = timeout_ms
        self._sistema = None  # PySpin.System
        self._lista = None  # PySpin.CameraList
        self._camara = None  # PySpin.CameraPtr
        self._processador = None  # PySpin.ImageProcessor (Bayer -> BGR)
        self._abrir()

    def _abrir(self) -> None:
        """Liga-se à câmara, configura-a e começa a adquirir.

        O que vai fazer:
            1. sistema = PySpin.System.GetInstance(); lista = sistema.GetCameras();
               câmara = lista.GetBySerial(nº) ou lista.GetByIndex(0); erro claro
               se não houver nenhuma (lembrar a placa de rede e o IP). câmara.Init().
               Registar DeviceModelName e DeviceSerialNumber e avisar se o
               modelo não contiver camara.modelo ("BFS-PGE-50S5C") ou se
               WidthMax × HeightMax não for camara.largura_px × altura_px
               (câmara diferente da configurada).
            1b. Rede GigE: GevSCPSPacketSize = camara.pacote_bytes (jumbo frames;
               se a placa não os aceitar, as imagens chegam incompletas) e
               DeviceLinkThroughputLimit no máximo.
            2. Imagem (nós QuickSpin, p.ex. câmara.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)):
               - Width/Height = camara.largura_px/altura_px, OffsetX/Y = 0;
               - ExposureAuto = Off, ExposureTime = aquisicao.exposicao_us
                 (tem de caber em 1/fps);
               - GainAuto = Off, Gain = aquisicao.ganho_db;
               - BalanceWhiteAuto = Off (o M4 Cor precisa de cores estáveis);
               - PixelFormat = camara.formato_pixel ("BayerRG8", o padrão de
                 cor da BFS-PGE-50S5C-C; é o que permite os 24 FPS; a cor é
                 reconstruída no PC no passo 6) e
                 AdcBitDepth = Bit8 se o formato for de 8 bits;
               - no fim, ler AcquisitionResultingFrameRate e avisar se for
                 menor do que disparo.fps (exposição longa ou rede lenta).
            3. Entrada do sensor: LineSelector = Line0, LineMode = Input,
               LineInverter = disparo.sensor_invertido, LineFilterWidth =
               disparo.sensor_filtro_us (ignora impulsos curtos).
            4. Trigger, conforme disparo.modo (mudar TriggerMode para Off antes
               de mexer no TriggerSelector/TriggerSource e só no fim pôr On):
               - "sensor" + "janela": TriggerMode = Off (contínuo);
                 AcquisitionFrameRateEnable = True, AcquisitionFrameRate = fps;
                 chunk data para gravar o sensor em cada imagem:
                 ChunkModeActive = True, ChunkSelector =
                 ExposureEndLineStatusAll → ChunkEnable = True (e o mesmo para
                 FrameID e Timestamp).
               - "sensor" + "rajada": TriggerSelector = FrameBurstStart,
                 TriggerSource = Line0, TriggerActivation = RisingEdge,
                 TriggerDelay = rajada_atraso_us, AcquisitionBurstFrameCount =
                 rajada_imagens, AcquisitionFrameRate = fps (espaçamento dentro
                 da rajada), TriggerOverlap = ReadOut, TriggerMode = On.
               - "foto" (botão ou sensor na Line0: uma imagem por disparo):
                 TriggerSelector = FrameStart, TriggerSource = Line0,
                 TriggerActivation = RisingEdge (botão normalmente aberto: o
                 sinal sobe quando se carrega), TriggerDelay = rajada_atraso_us
                 (0 com o botão), TriggerOverlap = Off (os saltos do botão
                 durante a leitura da imagem são ignorados pela câmara; os
                 restantes, pelo anti-ressalto do disparo), TriggerMode = On.
                 Sem botão: TriggerSource = Software e
                 câmara.TriggerSoftware.Execute() quando se carrega numa tecla.
               - "virtual": TriggerMode = Off, contínuo a fps.
            5. TLStream: StreamBufferHandlingMode = OldestFirst e
               StreamBufferCountManual ≈ 2 × fps: a aquisição corre numa
               thread própria (ver em_paralelo.py) que esvazia o buffer
               depressa, por isso não se deve perder nenhuma imagem.
            6. processador = PySpin.ImageProcessor() com
               SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR; câmara.BeginAcquisition().
        """
        import PySpin  # só aqui: o resto do projeto não depende do Spinnaker SDK

        raise NotImplementedError("FonteSpinnaker ainda não implementada (ver teste_camara.py).")

    def ler(self) -> Captura | None:
        """Adquire a imagem seguinte da câmara.

        Devolve:
            Captura com a imagem BGR, origem "spinnaker:<nº de série>#<FrameID>",
            instante e o estado do sensor; ou None se a câmara deixar de responder.
        O que vai fazer:
            1. img = câmara.GetNextImage(timeout_ms); se img.IsIncomplete(),
               img.Release() e tentar de novo. Um timeout é normal nos modos
               com trigger (ninguém carregou no botão, não passou nenhum saco):
               tentar outra vez, sem erro.
            2. bgr = processador.Convert(img, PySpin.PixelFormat_BGR8).GetNDArray().copy()
               (copy: o array aponta para memória gerida pelo SDK).
            3. Sensor:
               - "sensor" + "janela": bit 0 (Line0) de
                 img.GetChunkData().GetExposureEndLineStatusAll() (já com o
                 LineInverter aplicado; confirmar no SpinView);
               - "sensor" + "rajada" e "foto": True (só há imagens quando o
                 botão/sensor dispara; o disparo usa isto para o anti-ressalto);
               - "virtual": None.
            4. Instante: img.GetChunkData().GetTimestamp() (relógio da câmara,
               em ns) convertido para o relógio do PC, ou time.time().
            5. Guardar img.GetFrameID() (saltos = imagens perdidas → aviso) e img.Release().
            6. PySpin.SpinnakerException grave: registar no log e devolver None.
        """
        raise NotImplementedError("FonteSpinnaker ainda não implementada.")

    def fechar(self) -> None:
        """Termina a aquisição e liberta a câmara e o sistema Spinnaker.

        O que vai fazer (a ordem importa, senão o ReleaseInstance falha):
            câmara.EndAcquisition(); repor TriggerMode = Off; câmara.DeInit();
            apagar todas as referências à câmara (self._camara = None);
            lista.Clear(); sistema.ReleaseInstance(). Só desfazer os passos que
            chegaram a ser feitos (a abertura pode ter falhado a meio) e aceitar
            ser chamado duas vezes.
        """
        raise NotImplementedError("FonteSpinnaker ainda não implementada.")
