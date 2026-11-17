"""Ligação ao PLC (Delta DVP-12SE) por Modbus TCP: o ID de cada saco e o resultado da inspeção.

Endereços Modbus dos Delta DVP: D0 = 4096, X0 = 1024, Y0 = 1280, M0 = 2048 (X e Y em octal).
Registos D usados:
    D100  ID do saco, escrito pelo PLC
    D101  ID a que o PC responde
    D102  resultado: 1 OK, 2 NOK, 3 ERRO, 4 SEM_SACO
    D103  heartbeat do PC (contador)

Uso:
    plc = criar_ligacao(cfg)        # None se plc.ativo = False: sem PLC, nada disto corre
    plc.abrir()                     # ao Iniciar a inspeção
    id_saco = plc.ler_id()          # D100, ou None se o PLC não respondeu
    plc.enviar_resultado(id_saco, Estado.OK)   # D101 e D102 numa só escrita
    plc.fechar()                    # ao Parar

Garantias, para a inspeção nunca depender do PLC:
    - só fala com o PLC entre abrir() e fechar(); fora disso (estado "parado") nada se liga nem
      se escreve, e as chamadas devolvem logo None ou False;
    - nenhuma chamada levanta exceções: se o PLC falhar, devolve None ou False;
    - cada chamada espera no máximo plc.timeout_ms pela resposta;
    - se a ligação cair religa sozinha: uma tentativa logo na chamada seguinte e depois, no
      máximo, uma a cada plc.religar_ms (entretanto as chamadas devolvem logo);
    - pode ser usada por várias threads (uma operação de cada vez).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nucleo.config import Config, ConfigPLC
from nucleo.contrato import Estado

log = logging.getLogger("plc")

# Endereço Modbus do primeiro elemento de cada tipo nos Delta DVP. Os X e os Y numeram-se em
# octal, como no PLC: Y10 é a 9.ª saída (1280 + 8) e Y8 não existe.
BASES: dict[str, int] = {"D": 4096, "X": 1024, "Y": 1280, "M": 2048}
OCTAIS = "XY"
# Até onde vale a base de cada tipo (acima disto, os D e os M estão noutra zona de endereços).
MAXIMOS: dict[str, int] = {"D": 4095, "X": 255, "Y": 255, "M": 1535}

D_BASE = BASES["D"]
D_ID_PLC = 100
D_ID_PC = 101
D_RESULTADO = 102
D_HEARTBEAT = 103

# O que vai no D102 para cada estado do saco.
CODIGOS: dict[Estado, int] = {Estado.OK: 1, Estado.NOK: 2, Estado.ERRO: 3, Estado.SEM_SACO: 4}


@dataclass(frozen=True)
class Variavel:
    """Um registo (D) ou um bit (X, Y, M) do PLC, com o nome que tem no ISPSoft."""

    tipo: str  # "D", "X", "Y" ou "M"
    indice: int  # posição a contar do primeiro do tipo (Y10 → 8)

    @property
    def nome(self) -> str:
        """Como no ISPSoft: "D100", "Y10" (os X e os Y em octal)."""
        return f"{self.tipo}{self.indice:o}" if self.tipo in OCTAIS else f"{self.tipo}{self.indice}"

    @property
    def endereco(self) -> int:
        """O endereço Modbus."""
        return BASES[self.tipo] + self.indice

    @property
    def e_bit(self) -> bool:
        return self.tipo != "D"


def variavel(texto: str) -> Variavel:
    """"y10" → o Y10 (endereço 1288). ValueError se não for um D, X, Y ou M que exista."""
    nome = texto.strip().upper()
    partes = re.fullmatch(r"([DXYM])(\d+)", nome)
    if partes is None:
        raise ValueError(f"'{texto.strip()}' não é um registo D nem um bit X, Y ou M (p.ex. D100, Y1, M10)")
    tipo, digitos = partes.groups()
    if tipo in OCTAIS and set(digitos) & {"8", "9"}:
        raise ValueError(f"'{nome}' não existe: os {tipo} numeram-se em octal (..., {tipo}7, {tipo}10, {tipo}11, ...)")
    indice = int(digitos, 8 if tipo in OCTAIS else 10)
    if indice > MAXIMOS[tipo]:
        raise ValueError(f"'{nome}' está fora da zona conhecida: os {tipo} vão até {Variavel(tipo, MAXIMOS[tipo]).nome}")
    return Variavel(tipo, indice)


def variaveis(lista: str) -> list[Variavel]:
    """"D100-D103, Y1, Y2" → as variáveis, pela ordem e sem repetidas. Um intervalo é do mesmo tipo
    (D100-D103 ou D100-103); nos X e nos Y segue o octal (Y6-Y11 = Y6, Y7, Y10, Y11)."""
    encontradas: dict[Variavel, None] = {}
    for parte in re.split(r"[,;\s]+", re.sub(r"\s*-\s*", "-", lista.strip())):
        if not parte:
            continue
        if "-" not in parte:
            encontradas[variavel(parte)] = None
            continue
        inicio, fim = parte.split("-", 1)
        primeira = variavel(inicio)
        ultima = variavel(primeira.tipo + fim if fim.isdigit() else fim)
        if ultima.tipo != primeira.tipo or ultima.indice < primeira.indice:
            raise ValueError(f"'{parte}': um intervalo vai de um elemento a outro maior do mesmo tipo (p.ex. D100-D103)")
        for indice in range(primeira.indice, ultima.indice + 1):
            encontradas[Variavel(primeira.tipo, indice)] = None
    return list(encontradas)


class EstadoPLC(StrEnum):
    """O estado da ligação, para a luz da interface."""

    PARADO = "parado"  # fechada: a inspeção não está a correr, nada se liga
    LIGADO = "ligado"  # o PLC respondeu à última operação
    A_RELIGAR = "a religar"  # aberta, mas sem ligação: volta a tentar sozinha


class LigacaoPLC:
    """Ligação persistente ao PLC, com religação automática (ver as garantias no cimo do ficheiro)."""

    def __init__(self, cfg: ConfigPLC) -> None:
        # Só quem usa o PLC precisa do pymodbus instalado.
        from pymodbus.client import ModbusTcpClient

        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # as falhas são registadas aqui, uma vez
        self.cfg = cfg
        self._cliente = ModbusTcpClient(cfg.ip, port=cfg.porta, timeout=cfg.timeout_ms / 1000, retries=0)
        self._lock = threading.Lock()
        self._aberta = False
        self._estado = EstadoPLC.PARADO
        self._proxima_tentativa = 0.0  # time.monotonic() a partir do qual se pode tentar ligar
        self._ja_ligou = False  # desde abrir(): para contar as religações
        self.falhas = 0  # operações e tentativas de ligação falhadas desde abrir()
        self.religacoes = 0  # vezes que a ligação voltou depois de cair, desde abrir()
        self.ultimo_erro = ""
        self.ultimo_tempo_ms = 0.0  # duração da última operação que o PLC respondeu

    # --- Estado ---------------------------------------------------------------------
    @property
    def estado(self) -> EstadoPLC:
        return self._estado

    @property
    def endereco(self) -> str:
        return f"{self.cfg.ip}:{self.cfg.porta}"

    # --- Abrir e fechar ---------------------------------------------------------------
    def abrir(self) -> bool:
        """Começa a falar com o PLC (ao Iniciar). Devolve se ficou ligado; se não, religa sozinha."""
        with self._lock:
            self._aberta = True
            self._proxima_tentativa = 0.0
            self.falhas = self.religacoes = 0
            self.ultimo_erro = ""
            self._estado = EstadoPLC.A_RELIGAR
            self._ja_ligou = False
            return self._ligar()

    def fechar(self) -> None:
        """Deixa de falar com o PLC (ao Parar): fecha a ligação e não volta a tentar até abrir()."""
        with self._lock:
            estava_aberta, self._aberta = self._aberta, False
            self._fechar_cliente()
            self._estado = EstadoPLC.PARADO
            if estava_aberta:
                log.info("PLC parado (%s): %d falha(s), %d religação(ões).", self.endereco, self.falhas, self.religacoes)

    # --- Operações --------------------------------------------------------------------
    def ler_registos(self, primeiro: int, quantos: int = 1) -> list[int] | None:
        """Lê `quantos` registos D seguidos a partir de D`primeiro`. None se o PLC não respondeu."""
        resposta = self._pedir(
            lambda: self._cliente.read_holding_registers(D_BASE + primeiro, count=quantos, device_id=self.cfg.unidade),
            f"ler D{primeiro}")
        return None if resposta is None else list(resposta.registers)

    def escrever_registos(self, primeiro: int, valores: Sequence[int]) -> bool:
        """Escreve registos D seguidos a partir de D`primeiro`, numa só escrita. False se falhou."""
        valores = [int(v) for v in valores]
        if not valores or any(not 0 <= v <= 0xFFFF for v in valores):
            log.error("PLC: valores fora de 0..65535 para D%d: %s (nada foi escrito).", primeiro, valores)
            return False
        resposta = self._pedir(
            lambda: self._cliente.write_registers(D_BASE + primeiro, valores, device_id=self.cfg.unidade),
            f"escrever D{primeiro}")
        return resposta is not None

    def ler_id(self) -> int | None:
        """O ID do saco que o PLC pôs no D100. None se o PLC não respondeu."""
        valores = self.ler_registos(D_ID_PLC)
        return valores[0] if valores else None

    def enviar_resultado(self, id_saco: int, resultado: Estado | int) -> bool:
        """Responde ao PLC: D101 = ID do saco e D102 = resultado, numa só escrita. False se falhou.

        `resultado` é o estado do saco (OK, NOK, ERRO, SEM_SACO) ou já o código (1 a 4).
        """
        codigo = CODIGOS.get(resultado) if isinstance(resultado, Estado) else resultado
        if codigo not in CODIGOS.values():
            log.error("PLC: resultado sem código para o D102: %r (nada foi enviado).", resultado)
            return False
        return self.escrever_registos(D_ID_PC, [id_saco, codigo])

    # --- Interno (sempre com o lock) -----------------------------------------------------
    def _pedir(self, pedido: Callable[[], Any], descricao: str) -> Any | None:
        """Faz um pedido ao PLC. Devolve a resposta, ou None se está parado ou o PLC falhou."""
        with self._lock:
            if not self._aberta or not self._ligar():
                return None
            t0 = time.perf_counter()
            try:
                resposta = pedido()
            except Exception as e:  # rede, timeout, ou o que o pymodbus levantar: a inspeção não pode cair
                self._perdida(f"{descricao}: {e}", tentar_ja=True)
                return None
            if resposta.isError():  # o PLC respondeu, mas recusou (p.ex. endereço que não existe)
                self.falhas += 1
                if self.ultimo_erro != (erro := f"{descricao}: o PLC recusou ({resposta})"):
                    log.warning("PLC: %s.", erro)
                self.ultimo_erro = erro
                return None
            self.ultimo_tempo_ms = (time.perf_counter() - t0) * 1000
            return resposta

    def _ligar(self) -> bool:
        """Garante a ligação. Sem ela, tenta ligar se já passou o intervalo entre tentativas."""
        if self._cliente.connected:
            return True
        if time.monotonic() < self._proxima_tentativa:
            return False
        t0 = time.perf_counter()
        try:
            ligou = self._cliente.connect()
        except Exception:  # p.ex. sem rede nenhuma
            ligou = False
        if not ligou:
            self._perdida("sem resposta à ligação", tentar_ja=False)
            return False
        if self._ja_ligou:
            self.religacoes += 1
        self._ja_ligou = True
        self._estado = EstadoPLC.LIGADO
        log.info("PLC ligado (%s, %.0f ms).", self.endereco, (time.perf_counter() - t0) * 1000)
        return True

    def _perdida(self, motivo: str, tentar_ja: bool) -> None:
        """Regista a falha e fecha a ligação; a próxima chamada (ou a seguinte ao intervalo) religa."""
        self.falhas += 1
        self.ultimo_erro = motivo
        self._fechar_cliente()
        self._proxima_tentativa = 0.0 if tentar_ja else time.monotonic() + self.cfg.religar_ms / 1000
        if self._estado is EstadoPLC.LIGADO or self.falhas == 1:  # uma linha por queda, não uma por tentativa
            log.warning("PLC sem ligação (%s; %s): a religar sozinho.", self.endereco, motivo)
        self._estado = EstadoPLC.A_RELIGAR

    def _fechar_cliente(self) -> None:
        try:
            self._cliente.close()
        except Exception:  # fechar uma ligação já morta nunca pode dar erro cá para fora
            pass


def criar_ligacao(cfg: Config) -> LigacaoPLC | None:
    """A ligação ao PLC do perfil, ainda parada (falta abrir()). None se plc.ativo = False."""
    return LigacaoPLC(cfg.plc) if cfg.plc.ativo else None
