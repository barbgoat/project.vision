"""M1 Aquisição em paralelo: captar o saco seguinte enquanto o anterior é analisado.

Numa linha a 2 sacos/s, a análise de um saco (M2 a M6) demora uma parte dos
500 ms disponíveis. Se a aquisição esperasse pela análise, as imagens do saco
seguinte acumulavam-se no buffer da câmara (ou perdiam-se). Por isso:

    thread de aquisição:  fonte → disparo → passagens → fila
    programa principal:   fila → Mestre (análise) → M7 Saída

Fontes ao vivo (câmaras): a aquisição nunca espera. Se já houver fila_max
passagens à espera, a nova é descartada (sem imagens, marcada como
"descartada") e o Mestre regista-a como ERRO: um saco que passou sem ser
inspecionado nunca é dado como OK, e o problema fica no registo.
Fontes gravadas (pasta, vídeo): a aquisição espera pela análise; não se perde nada.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterator
from typing import Self

from modulos.m1_aquisicao.disparo import Disparo
from modulos.m1_aquisicao.fontes import FonteImagem
from nucleo.contrato import Captura, Passagem

log = logging.getLogger(__name__)

_FIM = object()  # marca o fim da fonte na fila


class AquisicaoEmParalelo:
    """Corre a fonte e o disparo numa thread e entrega as passagens pela ordem.

    Uso:
        with AquisicaoEmParalelo(fonte, disparo, fila_max=4) as aquisicao:
            for passagem in aquisicao:
                resultado = mestre.inspecionar_passagem(passagem)
    A fonte é fechada pela thread de aquisição, quando termina.
    """

    def __init__(self, fonte: FonteImagem, disparo: Disparo, fila_max: int) -> None:
        self.fonte = fonte
        self.disparo = disparo
        self.fila_max = fila_max
        self.ao_vivo = fonte.ao_vivo
        self.descartadas = 0  # passagens que não puderam ser analisadas
        self.imagens = 0  # imagens recebidas da fonte
        self._fila: queue.Queue[object] = queue.Queue()
        self._pendentes = 0  # passagens com imagens à espera de análise
        self._condicao = threading.Condition()
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._trabalhar, name="aquisicao", daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *excecao: object) -> None:
        self._parar.set()
        with self._condicao:
            self._condicao.notify_all()
        self._thread.join(timeout=5)

    def __iter__(self) -> Iterator[Passagem]:
        while True:
            item = self._fila.get()
            if item is _FIM:
                return
            if isinstance(item, BaseException):
                raise item
            assert isinstance(item, Passagem)
            if not item.descartada:
                with self._condicao:
                    self._pendentes -= 1
                    self._condicao.notify_all()
            yield item

    # --- Thread de aquisição -----------------------------------------------------------
    def _trabalhar(self) -> None:
        try:
            with self.fonte:
                for passagem in self.disparo.passagens(self._capturas()):
                    self._entregar(passagem)
        except BaseException as erro:  # passa o erro ao programa principal, que o mostra
            self._fila.put(erro)
        finally:
            self._fila.put(_FIM)

    def _capturas(self) -> Iterator[Captura]:
        for captura in self.fonte:
            if self._parar.is_set():
                return
            self.imagens += 1
            yield captura

    def _entregar(self, passagem: Passagem) -> None:
        with self._condicao:
            if self.ao_vivo:
                if self._pendentes >= self.fila_max:
                    self.descartadas += 1
                    passagem.candidatas, passagem.pontuacoes = [], []
                    passagem.descartada = True
                    passagem.aviso = (f"não analisada: {self._pendentes} passagens já à espera "
                                      "(a análise não está a acompanhar a linha)")
                    log.error("Passagem %d %s", passagem.numero, passagem.aviso)
                else:
                    self._pendentes += 1
            else:
                while self._pendentes >= self.fila_max and not self._parar.is_set():
                    self._condicao.wait(timeout=0.1)
                self._pendentes += 1
        self._fila.put(passagem)
