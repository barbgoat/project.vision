"""M7 Saída, registo: os CSV e as imagens anotadas de cada execução.

Para cada execução do main.py cria uma pasta resultados/<AAAAMMDD_HHMMSS>/ com:
    registo.csv   uma linha por saco (passagem): estado final, imagens da passagem,
                  estado + tempo de cada módulo, e a pasta de depuração (se houver)
    defeitos.csv  uma linha por defeito, com o mesmo número que aparece na imagem
    imagens/      imagens anotadas (conforme SAIDA_GUARDAR_IMAGENS)
    depuracao/    uma pasta por saco depurado (conforme SAIDA_DEPURAR / --depurar)

Os CSV usam ";" como separador e "utf-8-sig", para o Excel em português os
abrir diretamente. A ligação ao PLC fica para depois e entra aqui.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Self

import numpy as np

from modulos.m7_saida.depuracao import Depuracao, nome_curto
from modulos.m7_saida.desenho import desenhar
from nucleo.config import Config, GuardarImagens
from nucleo.contrato import Estado, Resultado
from nucleo.imagens import guardar_imagem

log = logging.getLogger(__name__)

COLUNAS_DEFEITOS = [
    "data_hora", "passagem", "origem", "numero", "modulo", "tipo", "x_mm", "y_mm", "comprimento_mm",
    "largura_mm", "area_mm2", "angulo_graus", "intensidade", "descricao",
]


class Saida:
    """O escravo M7. Usa a secção saida da configuração."""

    nome = "saida"
    pergunta = "Como mostrar e registar o resultado?"

    def __init__(self, cfg: Config, pasta: str | Path, modulos: list[str],
                 depurar: GuardarImagens | None = None) -> None:
        """Cria a pasta desta execução e abre os dois registos CSV.

        Recebe:
            cfg: configuração.
            pasta: pasta de resultados (é criada uma subpasta com a data e hora).
            modulos: nomes dos módulos executados pelo Mestre (Mestre.nomes),
                para as colunas de estado e de tempo de cada um.
            depurar: "nenhuma", "nok" ou "todas" (omissão: SAIDA_DEPURAR).
        """
        self.cfg = cfg
        self.modulos = modulos
        self.pasta = Path(pasta) / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._contador = 0
        modo = depurar or cfg.saida.depurar
        self.depuracao = Depuracao(cfg, self.pasta / "depuracao", modo) if modo != "nenhuma" else None

        self._ficheiro_registo = open(self.pasta / "registo.csv", "w", newline="", encoding="utf-8-sig")
        self._ficheiro_defeitos = open(self.pasta / "defeitos.csv", "w", newline="", encoding="utf-8-sig")
        self._registo = csv.writer(self._ficheiro_registo, delimiter=";")
        self._defeitos = csv.writer(self._ficheiro_defeitos, delimiter=";")
        self._registo.writerow(self.colunas_registo())
        self._defeitos.writerow(COLUNAS_DEFEITOS)

    def colunas_registo(self) -> list[str]:
        """Colunas do registo.csv: o saco (passagem), a decisão, e o estado e o tempo de cada módulo."""
        return [
            "data_hora", "passagem", "origem", "estado", "num_defeitos", "tipos", "tempo_ms",
            "imagens_passagem", "imagens_analisadas", "duracao_passagem_ms",
            *self.modulos,
            *(f"t_{m}_ms" for m in self.modulos),
            "mensagens", "imagem_anotada", "depuracao",
        ]

    # --- Imagem anotada ------------------------------------------------------------
    def anotar(self, resultado: Resultado) -> np.ndarray | None:
        """A imagem anotada (desenho.py). Se o desenho falhar, devolve a imagem sem anotações.

        None se o resultado não tem imagem (passagem descartada).
        """
        if resultado.imagem is None:
            return None
        try:
            return desenhar(resultado, self.cfg.geral.px_por_mm)
        except Exception:
            log.exception("M7 saida: o desenho falhou; esta imagem vai sem anotações.")
            return resultado.imagem.copy()

    # --- Registo ------------------------------------------------------------------
    def registar(self, resultado: Resultado) -> Path | None:
        """Escreve o saco nos dois CSV, guarda a imagem anotada e a depuração, se for caso disso.

        Recebe o Resultado do Mestre, com a captura analisada e a passagem.
        Devolve o caminho da imagem guardada, ou None.
        """
        self._contador += 1
        captura, passagem = resultado.captura, resultado.passagem
        instante = captura.instante if captura else passagem.inicio if passagem else datetime.now().timestamp()
        data_hora = datetime.fromtimestamp(instante).isoformat(sep=" ", timespec="milliseconds")
        origem = captura.origem if captura else f"passagem {passagem.numero}" if passagem else ""
        numero_passagem = passagem.numero if passagem else ""

        caminho_imagem = None
        anotada = self.anotar(resultado) if self._deve_guardar(resultado.estado) else None
        if anotada is not None:
            nome = f"{self._contador:06d}_{nome_curto(origem)}_{resultado.estado}{self.cfg.saida.formato_imagem}"
            caminho_imagem = self.pasta / "imagens" / nome
            guardar_imagem(caminho_imagem, anotada)
        pasta_depuracao = self.depuracao.processar(resultado, self._contador) if self.depuracao else None

        relatorios = {r.modulo: r for r in resultado.relatorios}
        tipos = ",".join(f"{tipo}:{n}" for tipo, n in sorted(resultado.contagem_tipos.items()))
        mensagens = " | ".join(
            [f"{r.modulo}: {r.mensagem}" for r in resultado.relatorios if r.mensagem]
            + ([f"disparo: {passagem.aviso}"] if passagem and passagem.aviso else [])
        )
        self._registo.writerow([
            data_hora, numero_passagem, origem, resultado.estado, len(resultado.defeitos), tipos,
            self._numero(resultado.tempo_ms, 1),
            passagem.num_imagens if passagem else "", resultado.imagens_analisadas,
            self._numero(passagem.duracao_ms, 0) if passagem else "",
            *(relatorios[m].resumo if m in relatorios else "" for m in self.modulos),
            *(self._numero(relatorios[m].tempo_ms, 1) if m in relatorios else "" for m in self.modulos),
            mensagens,
            caminho_imagem.relative_to(self.pasta) if caminho_imagem else "",
            pasta_depuracao.relative_to(self.pasta) if pasta_depuracao else "",
        ])
        for numero, d in enumerate(resultado.defeitos, start=1):
            self._defeitos.writerow([
                data_hora, numero_passagem, origem, numero, d.modulo, d.tipo,
                *(self._numero(v) for v in (d.x_mm, d.y_mm, d.comprimento_mm, d.largura_mm,
                                            d.area_mm2, d.angulo_graus, d.intensidade)),
                d.descricao,
            ])
        # Gravar já no disco: se o programa for interrompido, nada se perde.
        self._ficheiro_registo.flush()
        self._ficheiro_defeitos.flush()
        return caminho_imagem

    def _deve_guardar(self, estado: Estado) -> bool:
        modo = self.cfg.saida.guardar_imagens
        return modo == "todas" or (modo == "nok" and estado is not Estado.OK)

    def _numero(self, valor: float, casas: int = 2) -> str:
        return f"{valor:.{casas}f}".replace(".", self.cfg.saida.separador_decimal)

    # --- Fecho ---------------------------------------------------------------------
    def fechar(self) -> None:
        """Fecha os registos. Pode ser chamado mais de uma vez."""
        self._ficheiro_registo.close()
        self._ficheiro_defeitos.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *excecao: object) -> None:
        self.fechar()
