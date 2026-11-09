"""M7 Saída, registo: os CSV e as imagens anotadas de cada execução.

Para cada execução do main.py cria uma pasta resultados/<AAAAMMDD_HHMMSS>/ com:
    registo.csv   uma linha por saco (passagem): OF e produto, estado final, imagens da passagem,
                  estado + tempo de cada módulo, o que ficou por verificar, as medidas atípicas
                  face aos sacos OK, e a pasta de depuração (se houver)
    defeitos.csv  uma linha por defeito, com o mesmo número que aparece na imagem
    imagens/      imagens anotadas, …_anotada.jpg (conforme SAIDA_GUARDAR_IMAGENS)
    originais/    as imagens da câmara, …_original.png, com o mesmo nome base (SAIDA_GUARDAR_ORIGINAIS;
                  noutra pasta com SAIDA_ORIGINAIS_PASTA)
    depuracao/    uma pasta por saco depurado (conforme SAIDA_DEPURAR / --depurar)

Os CSV usam ";" como separador e "utf-8-sig", para o Excel em português os
abrir diretamente. A ligação ao PLC fica para depois e entra aqui.
"""

from __future__ import annotations

import csv
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Self

import numpy as np

from modulos.m7_saida.depuracao import Depuracao, nome_curto
from modulos.m7_saida.desenho import desenhar
from nucleo.config import RAIZ, Config, GuardarImagens
from nucleo.contrato import Estado, Resultado
from nucleo.imagens import guardar_imagem

log = logging.getLogger(__name__)

COLUNAS_DEFEITOS = [
    "data_hora", "of", "produto", "passagem", "origem", "numero", "modulo", "tipo", "x_mm", "y_mm", "comprimento_mm",
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

        s = cfg.saida
        raiz = Path(s.originais_pasta) if s.originais_pasta else None
        if raiz is not None and not raiz.is_absolute():
            raiz = RAIZ / raiz
        # Com uma pasta própria (p.ex. outro disco), uma subpasta por sessão; o limite vale para a pasta toda.
        self._raiz_originais = raiz or self.pasta
        self.pasta_originais = raiz / self.pasta.name if raiz else self.pasta / "originais"
        self._originais_vistos = 0
        self._originais: deque[tuple[Path, int]] = deque()  # (ficheiro, bytes), do mais antigo ao mais novo
        if s.guardar_originais != "nenhuma" and s.originais_max_mb > 0 and self._raiz_originais.is_dir():
            antigos = sorted(self._raiz_originais.rglob("*_original.png"), key=lambda p: p.stat().st_mtime)
            self._originais.extend((p, p.stat().st_size) for p in antigos)

    def colunas_registo(self) -> list[str]:
        """Colunas do registo.csv: a OF e o produto, o saco (passagem), a decisão, e o estado e o tempo de cada módulo."""
        return [
            "data_hora", "of", "produto", "passagem", "origem", "estado", "num_defeitos", "tipos", "tempo_ms",
            "imagens_passagem", "imagens_analisadas", "duracao_passagem_ms",
            *self.modulos,
            *(f"t_{m}_ms" for m in self.modulos),
            "nao_verificado", "atipico", "mensagens", "imagem_anotada", "imagem_original", "depuracao",
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
        base = f"{self._contador:06d}_{nome_curto(origem)}_{resultado.estado}"  # o mesmo na anotada e na original
        anotada = self.anotar(resultado) if self._deve_guardar(resultado.estado) else None
        if anotada is not None:
            caminho_imagem = self.pasta / "imagens" / f"{base}_anotada{self.cfg.saida.formato_imagem}"
            guardar_imagem(caminho_imagem, anotada)
        caminho_original = self._guardar_original(resultado, base)
        pasta_depuracao = self.depuracao.processar(resultado, self._contador) if self.depuracao else None

        relatorios = {r.modulo: r for r in resultado.relatorios}
        tipos = ",".join(f"{tipo}:{n}" for tipo, n in sorted(resultado.contagem_tipos.items()))
        mensagens = " | ".join(
            [f"{r.modulo}: {r.mensagem}" for r in resultado.relatorios if r.mensagem]
            + ([f"disparo: {passagem.aviso}"] if passagem and passagem.aviso else [])
        )
        self._registo.writerow([
            data_hora, resultado.of, resultado.produto, numero_passagem, origem, resultado.estado,
            len(resultado.defeitos), tipos,
            self._numero(resultado.tempo_ms, 1),
            passagem.num_imagens if passagem else "", resultado.imagens_analisadas,
            self._numero(passagem.duracao_ms, 0) if passagem else "",
            *(relatorios[m].resumo if m in relatorios else "" for m in self.modulos),
            *(self._numero(relatorios[m].tempo_ms, 1) if m in relatorios else "" for m in self.modulos),
            ",".join(resultado.nao_verificado), "; ".join(a.texto for a in resultado.atipicas), mensagens,
            caminho_imagem.relative_to(self.pasta) if caminho_imagem else "",
            (caminho_original.relative_to(self.pasta) if caminho_original.is_relative_to(self.pasta)
             else caminho_original) if caminho_original else "",
            pasta_depuracao.relative_to(self.pasta) if pasta_depuracao else "",
        ])
        for numero, d in enumerate(resultado.defeitos, start=1):
            self._defeitos.writerow([
                data_hora, resultado.of, resultado.produto, numero_passagem, origem, numero, d.modulo, d.tipo,
                *(self._numero(v) for v in (d.x_mm, d.y_mm, d.comprimento_mm, d.largura_mm,
                                            d.area_mm2, d.angulo_graus, d.intensidade)),
                d.descricao,
            ])
        # Gravar já no disco: se o programa for interrompido, nada se perde.
        self._ficheiro_registo.flush()
        self._ficheiro_defeitos.flush()
        return caminho_imagem

    def _deve_guardar(self, estado: Estado, modo: GuardarImagens | None = None) -> bool:
        modo = modo or self.cfg.saida.guardar_imagens
        return modo == "todas" or (modo == "nok" and estado is not Estado.OK)

    # --- Originais -----------------------------------------------------------------
    def _guardar_original(self, resultado: Resultado, base: str) -> Path | None:
        """A imagem que a inspeção analisou, tal como veio da câmara: PNG sem perdas, mono num só canal."""
        s = self.cfg.saida
        if resultado.imagem is None or not self._deve_guardar(resultado.estado, s.guardar_originais):
            return None
        self._originais_vistos += 1
        if (self._originais_vistos - 1) % s.originais_cada:
            return None
        imagem = resultado.imagem
        if imagem.ndim == 3 and (imagem == imagem[..., :1]).all():
            imagem = imagem[..., 0]  # câmara mono: os mesmos 8 bits, num só canal
        caminho = self.pasta_originais / f"{base}_original.png"
        try:
            guardar_imagem(caminho, imagem)
        except OSError as erro:  # p.ex. disco cheio: a inspeção continua, o registo diz que não ficou
            log.error("M7 saida: original não guardado (%s): %s", caminho, erro)
            return None
        self._limitar_originais(caminho)
        return caminho

    def _limitar_originais(self, novo: Path) -> None:
        """Buffer circular: acima de originais_max_mb, apaga os originais mais antigos (só …_original.png)."""
        limite = self.cfg.saida.originais_max_mb * 1e6
        if limite <= 0:
            return
        self._originais.append((novo, novo.stat().st_size))
        total = sum(tamanho for _, tamanho in self._originais)
        while total > limite and len(self._originais) > 1:
            antigo, tamanho = self._originais.popleft()
            antigo.unlink(missing_ok=True)
            total -= tamanho

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
