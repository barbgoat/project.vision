r"""Testa a comunicação Modbus TCP com o PLC (Delta DVP-12SE), sem a BIapp.

    python -m ferramentas.plc_teste                         # 192.168.1.5:502
    python -m ferramentas.plc_teste --ip 192.168.1.5 --porta 502 --timeout 100

A cada ciclo (500 ms):
    1. lê D100..D103 numa só leitura: o D100 é o ID do saco (escrito pelo PLC); D101..D103
       confirmam o que o PC escreveu no ciclo anterior;
    2. escreve D101 = D100 e D102 = 1 ou 2, alternado, numa só escrita (como em produção);
    3. escreve D103 = contador, incrementado a cada ciclo (como o heartbeat).

Uma linha por ciclo: hora, valores, tempo de cada operação e do ciclo (ms), e "confirma" se a
leitura bate com a escrita anterior. Se a ligação cair mostra "desligado" e tenta religar no
ciclo seguinte. Ctrl+C sai e mostra um resumo.

Registos D do Delta: endereço Modbus = 4096 + n (D100 = 4196).
"""

from __future__ import annotations

import argparse
import logging
import signal
import statistics
import time
from datetime import datetime

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

D_BASE = 4096  # endereço Modbus do D0
D_ID_PLC = 100  # ID do saco (PLC)
D_ID_PC = 101  # ID a que o PC responde (PC)
D_RESULTADO = 102  # 1 OK, 2 NOK, 3 ERRO, 4 SEM_SACO (PC)
D_HEARTBEAT = 103  # contador (PC)


def endereco(d: int) -> int:
    """O endereço Modbus do registo Dn."""
    return D_BASE + d


def agora() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class FalhaPLC(Exception):
    """O PLC respondeu com erro, ou não respondeu."""


def _verificar(resposta, operacao: str):
    if resposta.isError():
        raise FalhaPLC(f"{operacao}: {resposta}")
    return resposta


def ciclo(cliente: ModbusTcpClient, unidade: int, resultado: int, heartbeat: int) -> tuple[list[int], dict]:
    """Um ciclo de leitura e escrita. Devolve D100..D103 lidos e os tempos (ms) de cada operação."""
    tempos = {}
    t0 = time.perf_counter()
    lidos = _verificar(cliente.read_holding_registers(endereco(D_ID_PLC), count=4, device_id=unidade),
                       "ler D100..D103").registers
    t1 = time.perf_counter()
    tempos["ler"] = (t1 - t0) * 1000
    _verificar(cliente.write_registers(endereco(D_ID_PC), [lidos[0], resultado], device_id=unidade),
               "escrever D101..D102")
    t2 = time.perf_counter()
    tempos["escrever"] = (t2 - t1) * 1000
    _verificar(cliente.write_register(endereco(D_HEARTBEAT), heartbeat, device_id=unidade), "escrever D103")
    t3 = time.perf_counter()
    tempos["hb"] = (t3 - t2) * 1000
    tempos["total"] = (t3 - t0) * 1000
    return lidos, tempos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default="192.168.1.5")
    p.add_argument("--porta", type=int, default=502)
    p.add_argument("--unidade", type=int, default=1, help="endereço de estação do PLC (omissão 1)")
    p.add_argument("--periodo", type=int, default=500, help="ms entre ciclos (omissão 500)")
    p.add_argument("--timeout", type=int, default=300,
                   help="ms de espera por cada resposta (omissão 300; a BIapp usará ~100)")
    args = p.parse_args()

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # as falhas mostram-se na linha do ciclo
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break sai como Ctrl+C, com o resumo
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    cliente = ModbusTcpClient(args.ip, port=args.porta, timeout=args.timeout / 1000, retries=0)

    print(f"PLC {args.ip}:{args.porta}, unidade {args.unidade}, ciclo {args.periodo} ms, "
          f"timeout {args.timeout} ms. Ctrl+C para sair.")
    resultado, heartbeat = 1, 0
    escrito = None  # (D101, D102, D103) escritos no último ciclo bom, para confirmar na leitura seguinte
    ciclos = falhas = religacoes = 0
    totais: list[float] = []
    ligado = False
    proximo = time.monotonic()
    try:
        while True:
            ciclos += 1
            try:
                if not cliente.connected:
                    t0 = time.perf_counter()
                    if not cliente.connect():
                        raise FalhaPLC("sem ligação")
                    if ciclos > 1:
                        religacoes += 1
                    print(f"{agora()}  ligado ({(time.perf_counter() - t0) * 1000:.0f} ms)")
                    escrito = None
                ligado = True
                lidos, tempos = ciclo(cliente, args.unidade, resultado, (heartbeat + 1) % 65536)
                heartbeat = (heartbeat + 1) % 65536  # só avança se a escrita correu bem
                totais.append(tempos["total"])
                confirma = ""
                if escrito is not None:
                    confirma = "confirma" if tuple(lidos[1:]) == escrito else f"NÃO confirma (lido {lidos[1:]})"
                print(f"{agora()}  D100={lidos[0]:5d} -> D101={lidos[0]:5d} D102={resultado} D103={heartbeat:5d}"
                      f"  | ler {tempos['ler']:4.0f}  escrever {tempos['escrever']:4.0f}  hb {tempos['hb']:4.0f}"
                      f"  total {tempos['total']:4.0f} ms  {confirma}")
                escrito = (lidos[0], resultado, heartbeat)
                resultado = 2 if resultado == 1 else 1
            except (FalhaPLC, ModbusException, OSError) as e:
                falhas += 1
                if ligado:
                    print(f"{agora()}  desligado: {e}")
                else:
                    print(f"{agora()}  desligado, a religar: {e}")
                ligado = False
                cliente.close()
            proximo += args.periodo / 1000
            espera = proximo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            else:
                proximo = time.monotonic()  # atrasado: não acumula ciclos em atraso
    except KeyboardInterrupt:
        pass
    finally:
        cliente.close()
    print(f"\n{ciclos} ciclos, {len(totais)} bons, {falhas} falhas, {religacoes} religações.")
    if totais:
        print(f"Ciclo: média {statistics.mean(totais):.1f} ms, mediana {statistics.median(totais):.1f}, "
              f"máximo {max(totais):.1f} ms.")


if __name__ == "__main__":
    main()
