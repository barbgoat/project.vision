r"""Testa a comunicação Modbus TCP com o PLC (Delta DVP-12SE), sem a BIapp.

    python -m ferramentas.plc_teste                         # 192.168.1.5:502, unidade 1
    python -m ferramentas.plc_teste --so-ler                # só lê: não escreve nada no PLC
    python -m ferramentas.plc_teste --ler "Y1, Y2, M10" --unidade 0 --ciclos 20

No arranque mostra o endereço Modbus de cada variável e que unidades respondem (a pedida, 1, 0
e 255), para confirmar com o ISPSoft.

A cada ciclo (500 ms):
    1. lê D100..D103 numa só leitura: o D100 é o ID do saco (escrito pelo PLC); D101..D103
       confirmam o que o PC escreveu no ciclo anterior;
    2. lê as variáveis de --ler (omissão: Y1 e Y2), para comparar com o ISPSoft; são lidas antes
       da escrita, por isso mostram a reação do PLC ao D102 do ciclo ANTERIOR;
    3. escreve D101 = D100 e D102 = 1 ou 2, alternado, numa só escrita (como em produção);
    4. escreve D103 = contador, incrementado a cada ciclo (como o heartbeat).
Com --so-ler faz só 1 e 2.

Uma linha por ciclo: hora, valores, tempo de cada operação e do ciclo (ms), e "confirma" se a
leitura bate com a escrita anterior. Se a ligação cair mostra "desligado" e tenta religar no
ciclo seguinte. Ctrl+C sai e mostra um resumo.

Os endereços são os de nucleo/plc.py (os mesmos da BIapp): D0 = 4096, X0 = 1024, Y0 = 1280,
M0 = 2048, com os X e os Y em octal (Y10 = 1280 + 8).
"""

from __future__ import annotations

import argparse
import logging
import signal
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\plc_teste.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nucleo.plc import D_HEARTBEAT, D_ID_PC, D_ID_PLC, Variavel, variaveis, variavel  # noqa: E402


def agora() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class FalhaPLC(Exception):
    """O PLC respondeu com erro, ou não respondeu."""


def _verificar(resposta, operacao: str):
    if resposta.isError():
        raise FalhaPLC(f"{operacao}: {resposta}")
    return resposta


def ler(cliente: ModbusTcpClient, v: Variavel, unidade: int) -> int:
    """O valor de uma variável: um registo D (função 3), um bit Y ou M (função 1) ou um X (função 2)."""
    if not v.e_bit:
        return _verificar(cliente.read_holding_registers(v.endereco, count=1, device_id=unidade), f"ler {v.nome}").registers[0]
    funcao = cliente.read_discrete_inputs if v.tipo == "X" else cliente.read_coils
    return int(_verificar(funcao(v.endereco, count=1, device_id=unidade), f"ler {v.nome}").bits[0])


def procurar_unidades(cliente: ModbusTcpClient, pedida: int) -> None:
    """Mostra que unidades (endereços de estação) respondem à leitura do D100."""
    d100 = variavel(f"D{D_ID_PLC}")
    respondem = []
    for unidade in dict.fromkeys((pedida, 1, 0, 255)):
        t0 = time.perf_counter()
        try:
            if not cliente.connected and not cliente.connect():
                raise FalhaPLC("sem ligação")
            valor = ler(cliente, d100, unidade)
            texto = f"responde (D100 = {valor}, {(time.perf_counter() - t0) * 1000:.0f} ms)"
            respondem.append(unidade)
        except (FalhaPLC, ModbusException, OSError) as e:
            texto = f"não responde ({e})"
            cliente.close()  # uma resposta atrasada não pode ser lida como a do pedido seguinte
        print(f"  unidade {unidade:3d}: {texto}{'   <- a pedida' if unidade == pedida else ''}")
    if len(respondem) > 1:
        print("  O PLC responde a mais do que uma unidade: qualquer destas serve.")
    elif respondem and pedida not in respondem:
        print(f"  A unidade pedida não responde: usar --unidade {respondem[0]} (e plc.unidade no perfil).")


def ciclo(cliente: ModbusTcpClient, unidade: int, extra: list[Variavel], resultado: int, heartbeat: int,
          so_ler: bool) -> tuple[list[int], list[int], dict]:
    """Um ciclo. Devolve D100..D103 lidos, os valores de `extra` e os tempos (ms) de cada operação."""
    tempos = {}
    t0 = time.perf_counter()
    lidos = _verificar(cliente.read_holding_registers(variavel(f"D{D_ID_PLC}").endereco, count=4, device_id=unidade),
                       "ler D100..D103").registers
    t1 = time.perf_counter()
    tempos["ler"] = (t1 - t0) * 1000
    valores = [ler(cliente, v, unidade) for v in extra]
    t2 = time.perf_counter()
    tempos["extra"] = (t2 - t1) * 1000
    if not so_ler:
        _verificar(cliente.write_registers(variavel(f"D{D_ID_PC}").endereco, [lidos[0], resultado], device_id=unidade),
                   "escrever D101..D102")
        t3 = time.perf_counter()
        tempos["escrever"] = (t3 - t2) * 1000
        _verificar(cliente.write_register(variavel(f"D{D_HEARTBEAT}").endereco, heartbeat, device_id=unidade),
                   "escrever D103")
        tempos["hb"] = (time.perf_counter() - t3) * 1000
    tempos["total"] = (time.perf_counter() - t0) * 1000
    return lidos, valores, tempos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default="192.168.1.5")
    p.add_argument("--porta", type=int, default=502)
    p.add_argument("--unidade", type=int, default=1, help="endereço de estação do PLC (omissão 1)")
    p.add_argument("--ler", default="Y1, Y2", metavar="LISTA",
                   help='variáveis a ler além do D100..D103: D, X, Y ou M, p.ex. "Y1, Y2, M10" (omissão: "Y1, Y2")')
    p.add_argument("--so-ler", action="store_true", help="não escrever nada no PLC (D101..D103 ficam como estão)")
    p.add_argument("--ciclos", type=int, default=0, help="parar ao fim de tantos ciclos (omissão: até Ctrl+C)")
    p.add_argument("--periodo", type=int, default=500, help="ms entre ciclos (omissão 500)")
    p.add_argument("--timeout", type=int, default=300,
                   help="ms de espera por cada resposta (omissão 300; a BIapp usa plc.timeout_ms = 100)")
    args = p.parse_args()
    try:
        extra = variaveis(args.ler)
    except ValueError as e:
        p.error(f"--ler: {e}")

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # as falhas mostram-se na linha do ciclo
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break sai como Ctrl+C, com o resumo
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    cliente = ModbusTcpClient(args.ip, port=args.porta, timeout=args.timeout / 1000, retries=0)

    print(f"PLC {args.ip}:{args.porta}, unidade {args.unidade}, ciclo {args.periodo} ms, timeout {args.timeout} ms."
          f"{' SÓ LEITURA: nada é escrito no PLC.' if args.so_ler else ''} Ctrl+C para sair.")
    registos = [variavel(f"D{n}") for n in (D_ID_PLC, D_ID_PC, D_ID_PC + 1, D_HEARTBEAT)]
    print("Endereços Modbus: " + ", ".join(f"{v.nome} = {v.endereco}" for v in registos + extra))
    print("Unidades:")
    procurar_unidades(cliente, args.unidade)
    print()

    resultado, heartbeat = 1, 0
    escrito = None  # (D101, D102, D103) escritos no último ciclo bom, para confirmar na leitura seguinte
    ciclos = falhas = religacoes = 0
    totais: list[float] = []
    ligado = ja_ligou = False
    proximo = time.monotonic()
    try:
        while not args.ciclos or ciclos < args.ciclos:
            ciclos += 1
            try:
                if not cliente.connected:
                    t0 = time.perf_counter()
                    if not cliente.connect():
                        raise FalhaPLC("sem ligação")
                    religacoes += ja_ligou
                    ja_ligou = True
                    print(f"{agora()}  ligado ({(time.perf_counter() - t0) * 1000:.0f} ms)")
                    escrito = None
                ligado = True
                lidos, valores, tempos = ciclo(cliente, args.unidade, extra, resultado, (heartbeat + 1) % 65536, args.so_ler)
                totais.append(tempos["total"])
                bits = "  " + " ".join(f"{v.nome}={valor}" for v, valor in zip(extra, valores)) if extra else ""
                if args.so_ler:
                    print(f"{agora()}  D100={lidos[0]:5d} D101={lidos[1]:5d} D102={lidos[2]} D103={lidos[3]:5d}{bits}"
                          f"  | ler {tempos['ler']:4.0f}  extra {tempos['extra']:4.0f}  total {tempos['total']:4.0f} ms")
                    continue
                heartbeat = (heartbeat + 1) % 65536  # só avança se a escrita correu bem
                confirma = ""
                if escrito is not None:
                    confirma = "confirma" if tuple(lidos[1:]) == escrito else f"NÃO confirma (lido {lidos[1:]})"
                print(f"{agora()}  D100={lidos[0]:5d} -> D101={lidos[0]:5d} D102={resultado} D103={heartbeat:5d}{bits}"
                      f"  | ler {tempos['ler']:4.0f}  extra {tempos['extra']:4.0f}  escrever {tempos['escrever']:4.0f}"
                      f"  hb {tempos['hb']:4.0f}  total {tempos['total']:4.0f} ms  {confirma}")
                escrito = (lidos[0], resultado, heartbeat)
                resultado = 2 if resultado == 1 else 1
            except (FalhaPLC, ModbusException, OSError) as e:
                falhas += 1
                print(f"{agora()}  desligado{'' if ligado else ', a religar'}: {e}")
                ligado = False
                cliente.close()
            finally:
                proximo += args.periodo / 1000
                espera = proximo - time.monotonic()
                if espera > 0 and (not args.ciclos or ciclos < args.ciclos):
                    time.sleep(espera)
                elif espera <= 0:
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
