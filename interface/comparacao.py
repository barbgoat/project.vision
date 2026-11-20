"""Interface, comparação: o mesmo saco antes e depois de mudar parâmetros (a pré-visualização).

Lê os dois resultados que já existem, o do saco (Antes) e o da pré-visualização (Depois), e diz o
que mudou: a decisão, o estado de cada módulo, o tamanho do saco, o tempo e os parâmetros mudados.
Não volta a analisar nada: é texto feito na janela, uma vez por pré-visualização.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass

from interface.editor import SECOES_DEFINICOES, diferencas
from interface.estilo import CORES_ESTADO
from nucleo.config import Config
from nucleo.contrato import Estado, Resultado

PARAMETROS_MAX = 3  # parâmetros mudados no painel (todos na dica)
MEDIDAS_MAX = 30  # medidas mudadas na dica
TAMANHO_MIN_MM = 0.005  # o comprimento ou a largura só "mudam" acima disto (arredondam a 0,01 mm)


@dataclass(frozen=True)
class Comparacao:
    """O que mudou do Antes para o Depois."""

    texto: str  # HTML curto, para o painel lateral
    detalhe: str  # texto simples, para a dica: todos os parâmetros e medidas que mudaram
    decisao_mudou: bool


def comparar(antes: Resultado, depois: Resultado, cfg_antes: Config, cfg_depois: Config) -> Comparacao:
    """Antes: o saco tal como foi analisado (cfg_antes). Depois: o mesmo saco com cfg_depois."""
    parametros = [linha for linha in diferencas(cfg_antes, cfg_depois)
                  if linha.split(".", 1)[0] in SECOES_DEFINICOES]  # o modo de disparo e a câmara não contam aqui
    medidas = _medidas(antes, depois)
    linhas = [f"<b>Antes → Depois: {_estado(antes.estado)} → {_estado(depois.estado)}</b>"]
    modulos = _modulos(antes, depois)
    linhas.append("módulos: " + html.escape(" · ".join(modulos)) if modulos else "módulos: iguais")
    if (tamanho := _tamanho(antes, depois)):
        linhas.append(html.escape(tamanho))
    linhas.append(f"tempo {antes.tempo_ms:.0f} → {depois.tempo_ms:.0f} ms · "
                  + (f"{len(medidas)} medida(s) mudaram (ver a dica)" if medidas else "medidas iguais"))
    if parametros:
        mais = f" · e mais {len(parametros) - PARAMETROS_MAX}" if len(parametros) > PARAMETROS_MAX else ""
        linhas.append("mudado: " + html.escape("; ".join(parametros[:PARAMETROS_MAX])) + mais)
    else:
        linhas.append("os parâmetros são os do saco: o Depois é o Antes")
    detalhe = ["Parâmetros mudados:", *(parametros or ["nenhum"]), "", "Medidas que mudaram:",
               *(medidas[:MEDIDAS_MAX] or ["nenhuma"])]
    if len(medidas) > MEDIDAS_MAX:
        detalhe.append(f"… e mais {len(medidas) - MEDIDAS_MAX}")
    detalhe += ["", "O tempo do Depois é indicativo: a pré-visualização corre ao lado da linha."]
    return Comparacao("<br>".join(linhas), "\n".join(detalhe), antes.estado is not depois.estado)


def _estado(estado: Estado) -> str:
    return f"<span style='color:{CORES_ESTADO.get(estado, '#424242')}'>{estado}</span>"


def _modulos(antes: Resultado, depois: Resultado) -> list[str]:
    """Os módulos que mudaram, p.ex. "dimensoes NOK(1) → OK" (— se um dos lados não o executou)."""
    a = {r.modulo: r.resumo for r in antes.relatorios}
    d = {r.modulo: r.resumo for r in depois.relatorios}
    nomes = list(a) + [m for m in d if m not in a]
    return [f"{m} {a.get(m, '—')} → {d.get(m, '—')}" for m in nomes if a.get(m) != d.get(m)]


def _tamanho(antes: Resultado, depois: Resultado) -> str:
    """O comprimento × largura do saco, se mudou (vazio se não)."""
    def texto(r: Resultado) -> str:
        return "—" if r.saco is None else f"{r.saco.comprimento_mm:.2f} × {r.saco.largura_mm:.2f}"
    a, d = antes.saco, depois.saco
    if a is not None and d is not None and abs(a.comprimento_mm - d.comprimento_mm) < TAMANHO_MIN_MM \
            and abs(a.largura_mm - d.largura_mm) < TAMANHO_MIN_MM:
        return ""
    if a is None and d is None:
        return ""
    return f"saco {texto(antes)} → {texto(depois)} mm"


def _medidas(antes: Resultado, depois: Resultado) -> list[str]:
    """Cada medida de um módulo que mudou, p.ex. "dimensoes.comprimento_mm: 158.10 → 158.12". Os tempos
    (…_ms) mudam sempre: ficam de fora (o tempo do saco tem linha própria)."""
    a = {(r.modulo, k): v for r in antes.relatorios for k, v in r.medidas.items() if not k.endswith("_ms")}
    d = {(r.modulo, k): v for r in depois.relatorios for k, v in r.medidas.items() if not k.endswith("_ms")}
    chaves = list(a) + [c for c in d if c not in a]
    return [f"{m}.{k}: {_numero(a.get((m, k)))} → {_numero(d.get((m, k)))}"
            for m, k in chaves if not _iguais(a.get((m, k)), d.get((m, k)))]


def _iguais(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _numero(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e6:
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.2f}" if abs(v) >= 0.01 else f"{v:.3g}"
    return str(v)
