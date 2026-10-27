"""M1 Aquisição, disparo (trigger): "quando começa e acaba a passagem de cada saco?".

Recebe as imagens da fonte, uma a uma, e junta-as em Passagens: a sequência de
imagens de um saco, desde que entra até que sai. De cada passagem guarda as
melhores imagens (candidatas), da mais promissora para a menos, e o Mestre
analisa-as até uma ter o saco completo.

    "foto"     cada imagem é uma passagem (fotos; ou a câmara com um botão/sensor
               na Line0: cada vez que se carrega, uma imagem)
    "sensor"   a passagem são as imagens com o sensor ativo (Captura.sensor)
    "virtual"  a passagem são as imagens em que algo difere do fundo aprendido
               numa faixa no centro da imagem (barreira virtual, sem sensor)

Qual é a melhor imagem? A do saco mais centrado e inteiro no campo de visão.
Mede-se numa miniatura, comparada com o fundo (BarreiraVirtual), o que é muito
rápido. Sem fundo conhecido (p.ex. rajadas: a câmara só capta com saco), usam-se
as imagens do meio da passagem.

Uma passagem também fecha, com aviso, se demorar mais do que duracao_max_ms
(sacos encostados, tapete parado) ou se deixarem de chegar imagens durante mais
de intervalo_max_ms. Passagens com menos de imagens_min imagens são ignoradas.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Iterator

import cv2
import numpy as np

from nucleo.config import Config
from nucleo.contrato import Captura, Passagem

log = logging.getLogger(__name__)


def criar_disparo(cfg: Config) -> Disparo:
    """O disparo pedido em cfg.disparo.modo."""
    modo = cfg.disparo.modo
    if modo == "foto":
        return DisparoFoto(cfg)
    if modo == "sensor":
        return DisparoSensor(cfg)
    if modo == "virtual":
        return DisparoVirtual(cfg)
    raise ValueError(f"Modo de disparo desconhecido: {modo!r}")


class Disparo:
    """Base: junta as imagens em passagens. As subclasses dizem se há saco em cada imagem."""

    modo = ""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.p = cfg.disparo
        self._numero = 0  # nº da última passagem entregue
        self.ignoradas = 0  # passagens com menos de imagens_min imagens

    def passagens(self, capturas: Iterable[Captura]) -> Iterator[Passagem]:
        """Percorre as imagens e entrega cada passagem assim que ela termina."""
        aberta: _Recolha | None = None
        anterior: float | None = None
        for captura in capturas:
            if aberta is not None and anterior is not None:
                intervalo_ms = (captura.instante - anterior) * 1000
                if intervalo_ms > self.p.intervalo_max_ms:
                    yield from self._fechar(aberta, f"faltaram imagens ({intervalo_ms:.0f} ms sem imagens)")
                    aberta = None
            anterior = captura.instante

            ha_saco, pontuacao = self._avaliar(captura, dentro=aberta is not None)
            if ha_saco:
                if aberta is None:
                    aberta = _Recolha(captura.instante, self.p.candidatas, self.p.imagens_max)
                aberta.juntar(captura, pontuacao)
                if aberta.duracao_ms > self.p.duracao_max_ms:
                    yield from self._fechar(
                        aberta, f"passagem com mais de {self.p.duracao_max_ms:.0f} ms (sacos encostados? tapete parado?)"
                    )
                    aberta = None
            elif aberta is not None:
                yield from self._fechar(aberta)
                aberta = None
        if aberta is not None:
            yield from self._fechar(aberta, "a fonte terminou a meio da passagem")

    def _avaliar(self, captura: Captura, dentro: bool) -> tuple[bool, float | None]:
        """(há saco nesta imagem?, pontuação da posição: 0 = centrado, inf = cortado, None = desconhecida)."""
        raise NotImplementedError

    def _fechar(self, recolha: _Recolha, aviso: str = "") -> Iterator[Passagem]:
        if recolha.total < self.p.imagens_min:
            self.ignoradas += 1
            log.debug("Passagem ignorada: só %d imagem(ns) (mínimo %d).", recolha.total, self.p.imagens_min)
            return
        self._numero += 1
        candidatas, pontuacoes = recolha.melhores(self.p.candidatas)
        if aviso:
            log.warning("Passagem %d: %s", self._numero, aviso)
        yield Passagem(
            numero=self._numero,
            candidatas=candidatas,
            pontuacoes=pontuacoes,
            num_imagens=recolha.total,
            inicio=recolha.inicio,
            fim=recolha.fim,
            modo=self.modo,
            aviso=aviso,
        )


class DisparoFoto(Disparo):
    """Cada imagem é uma passagem (fotos soltas, ou a câmara com um botão/sensor na Line0).

    Imagens disparadas pela câmara (Captura.sensor = True) a menos de
    anti_ressalto_ms da anterior são ignoradas: são os "saltos" do botão. As
    fotos de uma pasta (sensor = None) nunca são ignoradas.
    """

    modo = "foto"

    def passagens(self, capturas: Iterable[Captura]) -> Iterator[Passagem]:
        ultimo_disparo: float | None = None
        for captura in capturas:
            if captura.sensor:
                if (ultimo_disparo is not None
                        and (captura.instante - ultimo_disparo) * 1000 < self.p.anti_ressalto_ms):
                    self.ignoradas += 1
                    log.debug("Disparo ignorado (ressalto do botão): %s", captura.origem)
                    continue
                ultimo_disparo = captura.instante
            self._numero += 1
            yield Passagem(self._numero, [captura], [None], 1, captura.instante, captura.instante, self.modo)


class DisparoSensor(Disparo):
    """A passagem são as imagens com o sensor de presença ativo (Captura.sensor).

    Se a câmara também capta sem saco (estratégia "janela"), essas imagens
    ensinam o fundo e as candidatas escolhem-se pela posição do saco; nas
    rajadas, escolhem-se as imagens do meio.
    """

    modo = "sensor"

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.barreira = BarreiraVirtual(cfg)

    def _avaliar(self, captura: Captura, dentro: bool) -> tuple[bool, float | None]:
        if captura.sensor is None:
            raise ValueError(
                "O disparo 'sensor' precisa de uma fonte com sensor (a câmara com o sensor na Line0); "
                "com pasta, vídeo ou webcam use o disparo 'virtual'."
            )
        if not captura.sensor:
            self.barreira.medir(captura.imagem)
            self.barreira.aprender()
            return False, None
        _, pontuacao = self.barreira.medir(captura.imagem)
        return True, pontuacao


class DisparoVirtual(Disparo):
    """Barreira virtual: há saco quando uma parte suficiente da faixa central difere do fundo.

    A faixa é estreita e perpendicular ao movimento do tapete, como uma barreira
    ótica: entre dois sacos fica livre, mesmo que a ponta do saco seguinte já
    esteja à vista noutro sítio da imagem.

    Usa histerese: o saco "entra" acima de virtual_ocupacao_entrada e só "sai"
    abaixo de virtual_ocupacao_saida, para não abrir e fechar passagens com o
    ruído. O fundo aprende-se com as primeiras imagens (tapete vazio) e
    continua a adaptar-se, devagar, enquanto não há saco.
    """

    modo = "virtual"

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.barreira = BarreiraVirtual(cfg)

    def _avaliar(self, captura: Captura, dentro: bool) -> tuple[bool, float | None]:
        ocupacao, pontuacao = self.barreira.medir(captura.imagem)
        if not self.barreira.pronta:
            self.barreira.aprender()
            return False, None
        limiar = self.p.virtual_ocupacao_saida if dentro else self.p.virtual_ocupacao_entrada
        ha_saco = ocupacao > limiar
        if not ha_saco and ocupacao < self.p.virtual_ocupacao_saida:
            self.barreira.aprender()
        return ha_saco, pontuacao


class BarreiraVirtual:
    """Compara uma miniatura de cada imagem com o fundo aprendido (tapete vazio).

    Mede duas coisas, em menos de um milissegundo:
        ocupação   fração da FAIXA CENTRAL que difere do fundo (o saco está a passar?)
        pontuação  distância ao centro da imagem do objeto mais centrado:
                   0 = centrado, 1 = no canto, inf = cortado pela borda (incompleto)
    """

    def __init__(self, cfg: Config) -> None:
        p = cfg.disparo
        self.horizontal = p.virtual_direcao == "horizontal"
        self.faixa_px = p.virtual_faixa_mm * min(p.virtual_resolucao_px_por_mm, cfg.geral.px_por_mm)
        self.escala = min(1.0, p.virtual_resolucao_px_por_mm / cfg.geral.px_por_mm)
        self.delta_e = p.virtual_delta_e
        self.taxa = p.virtual_aprendizagem
        self.imagens_fundo = p.virtual_imagens_fundo
        self.fundo: np.ndarray | None = None
        self._iniciais: list[np.ndarray] = []
        self._ultima: np.ndarray | None = None
        self._so_fundo: np.ndarray | None = None  # píxeis da última imagem que são mesmo fundo (uint8 0/1)

    @property
    def pronta(self) -> bool:
        """Já tem um fundo aprendido?"""
        return self.fundo is not None

    def medir(self, imagem: np.ndarray) -> tuple[float, float | None]:
        """(ocupação, pontuação) da imagem; (0, None) enquanto não houver fundo."""
        mini = cv2.resize(imagem, None, fx=self.escala, fy=self.escala, interpolation=cv2.INTER_AREA)
        self._ultima = cv2.cvtColor(mini.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)
        if self.fundo is None or self.fundo.shape != self._ultima.shape:
            self.fundo = None
            return 0.0, None
        diferente = np.linalg.norm(self._ultima - self.fundo, axis=2) > self.delta_e
        # Para aprender o fundo, só os píxeis que não diferem, com uma margem à
        # volta dos objetos: as pontas de sacos à vista não "mancham" o fundo.
        self._so_fundo = (cv2.dilate(diferente.astype(np.uint8), np.ones((5, 5), np.uint8)) == 0).astype(np.uint8)
        return float(self._faixa(diferente).mean()), _pontuacao(diferente)

    def _faixa(self, mascara: np.ndarray) -> np.ndarray:
        """A faixa central, perpendicular ao movimento (colunas do meio se o tapete anda na horizontal)."""
        comprimento = mascara.shape[1] if self.horizontal else mascara.shape[0]
        meio, metade = comprimento // 2, max(1, round(self.faixa_px / 2))
        corte = slice(max(0, meio - metade), meio + metade)
        return mascara[:, corte] if self.horizontal else mascara[corte, :]

    def aprender(self) -> None:
        """Junta a última imagem medida ao fundo, só nos píxeis que são fundo (chamar quando não há saco)."""
        if self._ultima is None:
            return
        if self.fundo is None:
            self._iniciais.append(self._ultima)
            if len(self._iniciais) >= self.imagens_fundo:
                # Mediana: um objeto que passe numa das imagens iniciais não fica no fundo.
                self.fundo = np.median(np.stack(self._iniciais), axis=0).astype(np.float32)
                self._iniciais = []
                log.info("Barreira virtual: fundo aprendido com %d imagens.", self.imagens_fundo)
        else:
            cv2.accumulateWeighted(self._ultima, self.fundo, self.taxa, mask=self._so_fundo)


def _pontuacao(diferente: np.ndarray) -> float | None:
    """Distância ao centro da imagem do objeto mais centrado (fração da meia-diagonal); inf se tocar na borda.

    Só contam objetos com pelo menos 20 % da área do maior (o resto é ruído);
    assim, a ponta do saco seguinte a entrar não é confundida com o saco.
    """
    n, _, estatisticas, centros = cv2.connectedComponentsWithStats(diferente.astype(np.uint8), connectivity=8)
    if n <= 1:
        return None
    areas = estatisticas[1:, cv2.CC_STAT_AREA]
    altura, largura = diferente.shape
    centro_x, centro_y = (largura - 1) / 2, (altura - 1) / 2
    objetos = [int(i) + 1 for i in np.nonzero(areas >= 0.2 * areas.max())[0]]
    escolhido = min(objetos, key=lambda i: math.hypot(centros[i][0] - centro_x, centros[i][1] - centro_y))
    x, y, w, h = estatisticas[escolhido, :4]
    if x == 0 or y == 0 or x + w >= largura or y + h >= altura:
        return math.inf
    cx, cy = centros[escolhido]
    return math.hypot(cx - centro_x, cy - centro_y) / math.hypot(largura / 2, altura / 2)


class _Recolha:
    """As imagens de uma passagem em curso, com memória limitada.

    Com pontuação (posição conhecida): fica só com as melhores.
    Sem pontuação: fica com uma amostra uniforme no tempo (quando enche, deita
    fora uma em cada duas e passa a guardar só uma em cada duas), para no fim
    poder escolher as do meio da passagem.
    """

    def __init__(self, inicio: float, candidatas: int, maximo: int) -> None:
        self.inicio = self.fim = inicio
        self.total = 0
        self._itens: list[tuple[int, Captura, float | None]] = []  # (índice, captura, pontuação)
        self._candidatas = candidatas
        self._maximo = maximo
        self._passo = 1

    @property
    def duracao_ms(self) -> float:
        return (self.fim - self.inicio) * 1000

    def juntar(self, captura: Captura, pontuacao: float | None) -> None:
        indice = self.total
        self.total += 1
        self.fim = captura.instante
        if pontuacao is not None:
            self._itens.append((indice, captura, pontuacao))
            com_pontuacao = [i for i in self._itens if i[2] is not None]
            if len(com_pontuacao) > self._candidatas:
                self._itens.remove(max(com_pontuacao, key=lambda i: i[2]))
        elif indice % self._passo == 0:
            self._itens.append((indice, captura, None))
            sem_pontuacao = [i for i in self._itens if i[2] is None]
            if len(sem_pontuacao) > self._maximo:
                self._itens = [i for i in self._itens if i[2] is not None] + sem_pontuacao[::2]
                self._passo *= 2

    def melhores(self, n: int) -> tuple[list[Captura], list[float | None]]:
        """As n melhores imagens: as de saco mais centrado ou, sem posição, as do meio da passagem."""
        meio = (self.total - 1) / 2
        ordenados = sorted(
            self._itens,
            key=lambda i: (i[2] is None, i[2] if i[2] is not None else abs(i[0] - meio)),
        )[:n]
        return [c for _, c, _ in ordenados], [p for _, _, p in ordenados]
