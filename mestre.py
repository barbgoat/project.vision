"""MESTRE: coordena os escravos e decide OK/NOK. Não analisa imagens.

Para cada saco (uma passagem do M1, com as suas melhores imagens):
    0. escolhe a imagem a analisar: a primeira candidata em que o M2 encontra
       o saco completo (inspecionar_passagem);
    1. pede ao M2 Localização o Saco;
    2. entrega o mesmo Saco, só de leitura, a cada escravo de inspeção ativo
       (M3 Dimensões, M4 Cor, M5 Relevo, M6 Manchas);
    3. protege cada chamada: um escravo que rebente fica com ERRO no seu
       relatório (com o ficheiro e a linha onde falhou) e os outros continuam;
    4. junta os relatórios e decide o estado do saco.

Tempo real: cada relatório traz o tempo do módulo (e o registo.csv também). Um escravo
com <secção>.timeout_ms > 0 que demore mais fica com ERRO (a sua thread, daemon e só deste
saco, acaba sozinha em segundo plano: em Python não se interrompe uma thread). Com geral.paralelo, os escravos
correm ao mesmo tempo em threads (o OpenCV e o NumPy libertam o GIL); são sem estado,
e o Saco, a Captura e a configuração são imutáveis.

Regra da decisão:
    - algum módulo NOK                         → NOK
    - algum módulo ERRO (em_caso_de_erro =
      "rejeitar")                              → ERRO (o saco é rejeitado)
    - nenhum escravo de inspeção concluiu      → ERRO (não se aprova um saco
      (todos desligados ou por implementar)       que ninguém inspecionou)
    - caso contrário                           → OK
Um módulo POR_IMPLEMENTAR não conta para a decisão: permite desenvolver o
sistema um módulo de cada vez.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as TempoEsgotado
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np

from modulos.m2_localizacao import Diagnostico, Localizador
from modulos.m3_dimensoes import Dimensoes
from modulos.m4_cor import Cor
from modulos.m5_relevo import Relevo
from modulos.m6_manchas import Manchas
from nucleo.config import RAIZ, Config
from nucleo.contrato import Captura, Escravo, Estado, Passagem, Relatorio, Resultado, Saco, SemSaco

log = logging.getLogger("mestre")


def no_espaco_metrico(img: np.ndarray, px_por_mm: float | None, canonico: float) -> np.ndarray:
    """A imagem à escala canónica (geral.px_por_mm), se a sua escala for conhecida e diferente."""
    if px_por_mm is None or abs(px_por_mm - canonico) <= 1e-6 * canonico:
        return img
    fator = canonico / px_por_mm
    return cv2.resize(img, None, fx=fator, fy=fator,
                      interpolation=cv2.INTER_AREA if fator < 1 else cv2.INTER_LINEAR)

# Escravos de inspeção, pela ordem em que são chamados. Para acrescentar um
# módulo novo (selagem, furos...): criar o ficheiro em modulos/, a secção em
# nucleo/config.py (com os valores em nucleo/defines.py) e juntá-lo aqui.
ESCRAVOS: tuple[type[Escravo], ...] = (Dimensoes, Cor, Relevo, Manchas)


class Mestre:
    """Coordena o M2 e os escravos de inspeção M3–M6."""

    def __init__(self, cfg: Config, apenas: Iterable[str] | None = None, of: str = "", produto: str = "") -> None:
        """Prepara os escravos.

        Recebe:
            cfg: configuração.
            apenas: se dado, corre só estes escravos de inspeção (pelo nome),
                mesmo que estejam desligados na configuração; útil para testar
                um módulo sozinho. Por omissão corre os que têm "ativo": true.
            of, produto: a ordem de fabrico e o produto, que ficam em cada
                Resultado. Mudar de OF é criar outro Mestre: um saco que já está
                a ser analisado acaba sempre com a OF com que começou.
        """
        self.cfg = cfg
        self.of, self.produto = of, produto
        self.localizador = Localizador(cfg)
        if apenas is None:
            classes = [e for e in ESCRAVOS if getattr(cfg, e.nome).ativo]
        else:
            pedidos = set(apenas)
            desconhecidos = pedidos - {e.nome for e in ESCRAVOS}
            if desconhecidos:
                raise ValueError(f"Módulos desconhecidos: {sorted(desconhecidos)}; existem: {self.disponiveis()}")
            classes = [e for e in ESCRAVOS if e.nome in pedidos]
        self.escravos: list[Escravo] = [classe() for classe in classes]
        self._avisados: set[str] = set()

    @staticmethod
    def disponiveis() -> list[str]:
        """Nomes de todos os escravos de inspeção que existem."""
        return [e.nome for e in ESCRAVOS]

    @property
    def nomes(self) -> list[str]:
        """Nomes dos módulos que este Mestre executa, pela ordem: localizacao, ..."""
        return [Localizador.nome, *(e.nome for e in self.escravos)]

    # --- Inspeção ---------------------------------------------------------------------
    def inspecionar(self, img: np.ndarray, guardar_intermedios: bool = False,
                    px_por_mm: float | None = None, captura: Captura | None = None) -> Resultado:
        """Inspeciona uma imagem: localiza o saco, distribui-o pelos escravos e decide.

        Recebe:
            img: imagem BGR uint8 (Captura.imagem). Passa a ser só de leitura.
            guardar_intermedios: pedir a cada escravo as imagens das suas etapas.
            px_por_mm: a escala da imagem, se já for conhecida (Captura.px_por_mm, câmara
                calibrada). Se for diferente de geral.px_por_mm, a imagem é reamostrada para
                essa escala, a do espaço métrico onde tudo é feito. None (telemóvel): a
                imagem fica como está (a escala vem da folha ou dos marcadores).
            captura: a Captura de onde a imagem veio (com as outras iluminações); sem ela, é
                criada uma só com esta imagem.
        Devolve:
            Resultado com o estado do saco, o relatório de cada módulo, a OF e o produto.
        """
        if captura is None:
            captura = Captura(img, "imagem", time.time(), px_por_mm=px_por_mm)
        resultado = self._inspecionar(no_espaco_metrico(img, px_por_mm, self.cfg.geral.px_por_mm), captura,
                                      guardar_intermedios)
        resultado.of, resultado.produto = self.of, self.produto
        return resultado

    def _inspecionar(self, img: np.ndarray, captura: Captura, guardar_intermedios: bool) -> Resultado:
        inicio = perf_counter()
        img.flags.writeable = False
        endireitada = False

        # 0. Referência (fotos à mão): daqui em diante a imagem é a folha, ou o que está entre os
        # marcadores, vista de cima
        ms_folha = 0.0
        if self.cfg.localizacao.referencia != "nenhuma":
            retificada, rel_folha = self._executar(Localizador.nome, lambda: self.localizador.retificar(img))
            if rel_folha.estado is Estado.SEM_SACO:  # sem a referência: região e medidas em px
                return self._sem_escala(img, rel_folha, inicio, guardar_intermedios)
            if rel_folha.estado is not Estado.OK:
                return Resultado(img, Estado.ERRO, None, [rel_folha], _ms_desde(inicio),
                                 f"{Localizador.nome}: {rel_folha.mensagem}")
            img, ms_folha, endireitada = retificada, rel_folha.tempo_ms, True
            img.flags.writeable = False

        # 1. M2 Localização
        diag = Diagnostico()
        saco, rel_localizacao = self._executar(
            Localizador.nome, lambda: self.localizador.localizar(img, guardar_intermedios, diag)
        )
        rel_localizacao = replace(rel_localizacao, tempo_ms=rel_localizacao.tempo_ms + ms_folha,
                                  medidas=diag.medidas, intermedios=diag.intermedios)
        if rel_localizacao.estado is Estado.OK:
            if saco is None:
                rel_localizacao = replace(rel_localizacao, estado=Estado.SEM_SACO,
                                          mensagem="nenhum saco completo na imagem")
            elif not isinstance(saco, Saco):
                rel_localizacao = self._violacao(rel_localizacao,
                                                 f"devolveu {type(saco).__name__} em vez de Saco ou None")
        if rel_localizacao.estado is not Estado.OK:
            estado = Estado.SEM_SACO if rel_localizacao.estado is Estado.SEM_SACO else Estado.ERRO
            return Resultado(img, estado, None, [rel_localizacao], _ms_desde(inicio),
                             f"{Localizador.nome}: {rel_localizacao.mensagem}")
        _so_leitura(saco)
        rel_localizacao = replace(rel_localizacao, medidas={
            **rel_localizacao.medidas, "comprimento_mm": saco.comprimento_mm, "largura_mm": saco.largura_mm,
            "angulo_graus": saco.angulo_graus})
        relatorios = [rel_localizacao]

        # 2. Escravos de inspeção, cada um protegido, com as imagens no espaço métrico
        metrica = _captura_metrica(captura, img, endireitada, self.cfg.geral.px_por_mm)
        relatorios += self._correr_escravos(saco, metrica, guardar_intermedios)

        # 3. Decisão
        estado, mensagem = self._decidir(relatorios[1:])
        return Resultado(img, estado, saco, relatorios, _ms_desde(inicio), mensagem)

    def _sem_escala(self, img: np.ndarray, rel_referencia: Relatorio, inicio: float,
                    guardar_intermedios: bool) -> Resultado:
        """Foto sem a referência (folha ou marcadores): sem mm, o saco não se pode inspecionar.

        Localiza-o à mesma, para se ver a região e as medidas em px, e dá ERRO (nunca é
        aprovado). Se nem assim houver saco, SEM_SACO com o motivo da referência.
        """
        diag = Diagnostico()
        devolvido, rel = self._executar(
            Localizador.nome, lambda: self.localizador.localizar_sem_escala(img, guardar_intermedios, diag))
        rel = replace(rel, tempo_ms=rel.tempo_ms + rel_referencia.tempo_ms, intermedios=diag.intermedios)
        if rel.estado is not Estado.OK or devolvido is None:
            return Resultado(img, Estado.SEM_SACO, None, [replace(rel_referencia, tempo_ms=rel.tempo_ms)],
                             _ms_desde(inicio), f"{Localizador.nome}: {rel_referencia.mensagem}")
        saco, ppm = devolvido
        comprimento_px, largura_px = saco.comprimento_mm * ppm, saco.largura_mm * ppm
        # Para o desenho (que usa geral.px_por_mm) e o ecrã, as medidas à escala do perfil;
        # nunca são registadas como mm (ERRO).
        escala = ppm / self.cfg.geral.px_por_mm
        saco = replace(saco, comprimento_mm=saco.comprimento_mm * escala, largura_mm=saco.largura_mm * escala)
        _so_leitura(saco)
        rel = replace(rel, estado=Estado.ERRO,
                      medidas={"comprimento_px": comprimento_px, "largura_px": largura_px,
                               "angulo_graus": saco.angulo_graus},
                      mensagem=(f"{rel_referencia.mensagem}: sem escala, o saco tem {comprimento_px:.0f} x "
                                f"{largura_px:.0f} px (não são mm) e não foi inspecionado"))
        return Resultado(img, Estado.ERRO, saco, [rel], _ms_desde(inicio), f"{Localizador.nome}: {rel.mensagem}")

    # --- Passagem de um saco (várias imagens) ---------------------------------------------
    def inspecionar_passagem(self, passagem: Passagem, guardar_intermedios: bool = False) -> Resultado:
        """Inspeciona um saco a partir da sua passagem: UMA decisão por saco.

        Experimenta as imagens candidatas pela ordem (a mais promissora
        primeiro) até o M2 encontrar o saco completo numa delas, e analisa
        essa. Como o M2 desiste depressa quando o saco está cortado, tentar
        a seguinte custa pouco.

        Recebe:
            passagem: do M1 (disparo).
        Devolve:
            O Resultado da imagem analisada, com a passagem e a captura usadas.
            Uma passagem descartada (a análise não acompanhou a linha), ou em
            que nenhuma candidata tinha o saco completo, dá ERRO: um saco que
            passou e não foi inspecionado nunca é aprovado.
        """
        inicio = perf_counter()
        if passagem.descartada or not passagem.candidatas:
            mensagem = passagem.aviso or "passagem sem imagens"
            return Resultado(None, Estado.ERRO, None, [], _ms_desde(inicio), mensagem, passagem=passagem,
                             of=self.of, produto=self.produto)

        resultado: Resultado | None = None
        for tentativa, captura in enumerate(passagem.candidatas, start=1):
            resultado = self.inspecionar(captura.imagem, guardar_intermedios, captura.px_por_mm, captura)
            resultado.captura, resultado.imagens_analisadas = captura, tentativa
            if resultado.estado is not Estado.SEM_SACO:
                break
        assert resultado is not None
        resultado.passagem = passagem
        resultado.tempo_ms = _ms_desde(inicio)
        # Numa passagem (sensor/virtual) passou mesmo um saco: não o ter visto inteiro é um erro.
        if resultado.estado is Estado.SEM_SACO and passagem.modo != "foto":
            resultado.estado = Estado.ERRO
            resultado.mensagem = (f"nenhuma das {len(passagem.candidatas)} imagens candidatas tinha o saco "
                                  f"completo ({resultado.mensagem})")
        if passagem.aviso:
            resultado.mensagem = " | ".join(filter(None, [resultado.mensagem, f"disparo: {passagem.aviso}"]))
        return resultado

    # --- Escravos: em sequência, com tempo limite ou em paralelo -----------------------------
    def _correr_escravos(self, saco: Saco, captura: Captura, guardar_intermedios: bool) -> list[Relatorio]:
        """Os relatórios dos escravos, pela ordem de self.escravos."""
        chamadas = [(e, lambda e=e: e.inspecionar(saco, captura, self.cfg, guardar_intermedios))
                    for e in self.escravos]
        paralelo = self.cfg.geral.paralelo
        if not paralelo and not any(self._limite_ms(e) for e in self.escravos):  # o caso simples: sem threads
            return [self._concluir(e, *self._executar(e.nome, chamada)) for e, chamada in chamadas]
        inicio = perf_counter()
        futuros: dict[int, Future] = {}
        if paralelo:  # todos ao mesmo tempo; cada um conta o seu tempo limite desde o início
            futuros = {i: self._em_thread(e.nome, c) for i, (e, c) in enumerate(chamadas)}
        relatorios = []
        for i, (escravo, chamada) in enumerate(chamadas):
            limite = self._limite_ms(escravo)
            if not paralelo:
                if not limite:
                    relatorios.append(self._concluir(escravo, *self._executar(escravo.nome, chamada)))
                    continue
                inicio = perf_counter()
                futuros[i] = self._em_thread(escravo.nome, chamada)
            espera = None if not limite else max(0.0, limite / 1000 - (perf_counter() - inicio))
            try:
                relatorios.append(self._concluir(escravo, *futuros[i].result(timeout=espera)))
            except TempoEsgotado:
                log.error("Módulo %s excedeu o tempo limite de %g ms", escravo.nome, limite)
                relatorios.append(Relatorio(escravo.nome, Estado.ERRO, tempo_ms=_ms_desde(inicio),
                                            mensagem=f"excedeu o tempo limite de {limite:g} ms "
                                                     f"({escravo.nome}.timeout_ms)"))
        return relatorios

    def _em_thread(self, nome: str, chamada: Callable[[], Any]) -> Future:
        """Corre _executar numa thread daemon nova, só deste saco: um escravo encravado não ocupa
        o lugar dos sacos seguintes nem impede o programa de fechar."""
        futuro: Future = Future()

        def correr() -> None:
            try:
                futuro.set_result(self._executar(nome, chamada))
            except BaseException as erro:
                futuro.set_exception(erro)

        threading.Thread(target=correr, name=f"escravo-{nome}", daemon=True).start()
        return futuro

    def _limite_ms(self, escravo: Escravo) -> float:
        """O tempo limite do escravo (secção.timeout_ms); 0 = sem limite."""
        return float(getattr(getattr(self.cfg, escravo.nome, None), "timeout_ms", 0.0))

    def _concluir(self, escravo: Escravo, devolvido: Any, execucao: Relatorio) -> Relatorio:
        """O relatório final de um escravo: o que ele devolveu, com o tempo e o módulo de cada defeito."""
        if execucao.estado is not Estado.OK:
            return execucao
        if not (isinstance(devolvido, Relatorio) and devolvido.modulo == escravo.nome):
            return self._violacao(execucao, f"devolveu {devolvido!r:.80} em vez do seu Relatorio")
        relatorio = replace(devolvido, tempo_ms=execucao.tempo_ms,
                            defeitos=[replace(d, modulo=escravo.nome) for d in devolvido.defeitos])
        if relatorio.estado is Estado.POR_IMPLEMENTAR:
            self._avisar_por_implementar(escravo.nome, relatorio.mensagem)
        return relatorio

    # --- Proteção de cada chamada ------------------------------------------------------
    def _executar(self, nome: str, chamada: Callable[[], Any]) -> tuple[Any, Relatorio]:
        """Corre um escravo e devolve (o que ele devolveu, relatório da execução).

        O relatório da execução tem estado OK (correu), SEM_SACO (o M2 diz
        porquê), POR_IMPLEMENTAR ou ERRO, o tempo gasto e, nos três últimos,
        a explicação.
        """
        inicio = perf_counter()
        try:
            devolvido = chamada()
        except SemSaco as motivo:
            return None, Relatorio(nome, Estado.SEM_SACO, tempo_ms=_ms_desde(inicio), mensagem=str(motivo))
        except NotImplementedError as erro:  # um esqueleto que ainda não devolve por_implementar
            onde = _onde(erro)
            self._avisar_por_implementar(nome, onde)
            return None, Relatorio(nome, Estado.POR_IMPLEMENTAR, tempo_ms=_ms_desde(inicio),
                                   mensagem=f"por implementar: {onde}")
        except Exception as erro:
            onde = _onde(erro)
            sitio = onde if onde == _FORA_DO_PROJETO else f"em {onde}"
            log.error("Módulo %s falhou (%s): %s: %s", nome, sitio, type(erro).__name__, erro)
            log.debug("Traceback completo:", exc_info=erro)
            return None, Relatorio(nome, Estado.ERRO, tempo_ms=_ms_desde(inicio),
                                   mensagem=f"{type(erro).__name__}: {erro} ({sitio})",
                                   detalhe="".join(traceback.format_exception(erro)))
        return devolvido, Relatorio(nome, Estado.OK, tempo_ms=_ms_desde(inicio))

    def _avisar_por_implementar(self, nome: str, onde: str) -> None:
        if nome not in self._avisados:
            self._avisados.add(nome)
            log.warning("Módulo %s ainda por implementar (%s); não conta para a decisão.", nome, onde)

    @staticmethod
    def _violacao(relatorio: Relatorio, problema: str) -> Relatorio:
        """O relatório de um módulo que não cumpriu o contrato (ERRO)."""
        log.error("Módulo %s: contrato não cumprido: %s", relatorio.modulo, problema)
        return replace(relatorio, estado=Estado.ERRO, mensagem=f"contrato não cumprido: {problema}")

    # --- Decisão ------------------------------------------------------------------------
    def _decidir(self, relatorios: list[Relatorio]) -> tuple[Estado, str]:
        """Junta os relatórios dos escravos de inspeção no estado final do saco."""
        com_erro = [r for r in relatorios if r.estado is Estado.ERRO]
        mensagem = " | ".join(f"{r.modulo}: {r.mensagem}" for r in com_erro)
        if any(r.estado is Estado.NOK for r in relatorios):
            return Estado.NOK, mensagem
        if com_erro and self.cfg.geral.em_caso_de_erro == "rejeitar":
            return Estado.ERRO, mensagem
        if not any(r.estado in (Estado.OK, Estado.NOK) for r in relatorios):
            return Estado.ERRO, mensagem or "nenhum escravo de inspeção concluiu a análise"
        return Estado.OK, mensagem


# --- Auxiliares ------------------------------------------------------------------------

_FORA_DO_PROJETO = "fora do código do projeto"


def _ms_desde(inicio: float) -> float:
    return (perf_counter() - inicio) * 1000


def _captura_metrica(captura: Captura, img: np.ndarray, endireitada: bool, px_por_mm: float) -> Captura:
    """A Captura que os escravos recebem: as imagens no espaço métrico, como a do Saco.

    A difusa é a imagem analisada. As outras iluminações são reamostradas da mesma maneira;
    numa foto endireitada pela folha ou pelos marcadores só fica a difusa (a homografia
    não é guardada).
    """
    outras = {} if endireitada else {nome: no_espaco_metrico(i, captura.px_por_mm, px_por_mm)
                                     for nome, i in captura.imagens.items() if nome != "difusa"}
    return replace(captura, imagens={"difusa": img, **outras}, px_por_mm=px_por_mm)


def _so_leitura(saco: Saco) -> None:
    """Bloqueia a escrita nos arrays do Saco antes de o entregar aos escravos."""
    for array in (saco.contorno, saco.matriz, saco.imagem, saco.mascara, saco.mascara_util):
        array.flags.writeable = False


def _onde(erro: BaseException) -> str:
    """Último ponto do código do projeto por onde a exceção passou: "ficheiro:linha (função)"."""
    quadros = [q for q in traceback.extract_tb(erro.__traceback__) if _no_projeto(q.filename)]
    if not quadros:
        return _FORA_DO_PROJETO
    q = quadros[-1]
    return f"{_relativo(q.filename)}:{q.lineno} ({q.name})"


def _no_projeto(caminho: str) -> bool:
    if caminho.startswith("<"):  # código gerado pelo Python (p.ex. "<string>" das dataclasses)
        return False
    try:
        relativo = _relativo(caminho)
    except ValueError:
        return False
    return not relativo.startswith((".venv", "mestre.py"))


def _relativo(caminho: str) -> str:
    return Path(caminho).resolve().relative_to(RAIZ).as_posix()
