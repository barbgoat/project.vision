"""Configuração: os valores dos defines, organizados por módulo, validados.

    nucleo/defines.py   ← os VALORES (é aí que se mudam)
    nucleo/config.py    ← este ficheiro: agrupa-os por módulo e verifica-os
    config.json         ← opcional: só o que muda numa montagem/produto

Cada módulo só lê a sua secção (e a geral): mudar um valor da cor nunca mexe
no relevo. Cada parâmetro tem um define com o mesmo nome em maiúsculas
(localizacao.comprimento_min_mm ↔ LOCALIZACAO_COMPRIMENTO_MIN_MM).

A configuração é só de leitura (frozen), como o Saco: todos os escravos recebem
a mesma e nenhum a pode alterar para os outros. Para mudar um valor cria-se
outra com dataclasses.replace(), que volta a validar tudo.

O config.json, se existir, só tem os valores que diferem dos defines, p.ex.

    {"geral": {"px_por_mm": 12.4}}

Para ver todos os valores em uso (defines + config.json):

    python -m nucleo.config
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, get_args

from nucleo import defines as D

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_POR_OMISSAO = RAIZ / "config.json"

# Opções válidas dos parâmetros de texto.
Canal = Literal["cinzento", "b", "g", "r", "h", "s", "v"]
Polaridade = Literal["ambas", "clara", "escura"]
PoliticaErro = Literal["rejeitar", "ignorar"]
GuardarImagens = Literal["nenhuma", "nok", "todas"]
FormatoImagem = Literal[".jpg", ".png"]
SeparadorDecimal = Literal[",", "."]
ModoDisparo = Literal["foto", "sensor", "virtual"]
EstrategiaSensor = Literal["janela", "rajada"]
Direcao = Literal["horizontal", "vertical"]
FormatoPixel = Literal["BayerRG8", "BayerRG12p", "BGR8"]


def nome_define(secao: str, parametro: str) -> str:
    """Nome do define de um parâmetro, p.ex. ("geral", "px_por_mm") → "GERAL_PX_POR_MM"."""
    return f"{secao}_{parametro}".upper()


def _verificar(regras: list[tuple[bool, str]]) -> list[str]:
    """Devolve as mensagens das regras que não se cumprem."""
    return [mensagem for cumpre, mensagem in regras if not cumpre]


def _opcao(secao: object, nome: str, tipo: object) -> tuple[bool, str]:
    """Regra: o campo `nome` tem de ser uma das opções do Literal `tipo`."""
    opcoes = get_args(tipo)
    return getattr(secao, nome) in opcoes, f"{nome} tem de ser um de {opcoes}"


# --- Secções (os comentários de cada valor estão em nucleo/defines.py) ------------------

@dataclass(frozen=True)
class ConfigGeral:
    """Parâmetros comuns a todos os módulos."""

    px_por_mm: float = D.GERAL_PX_POR_MM
    velocidade_mm_s: float = D.GERAL_VELOCIDADE_MM_S
    em_caso_de_erro: PoliticaErro = D.GERAL_EM_CASO_DE_ERRO
    orcamento_ms: float = D.GERAL_ORCAMENTO_MS

    def erros(self) -> list[str]:
        return _verificar([
            (self.px_por_mm > 0, "px_por_mm tem de ser > 0"),
            (self.velocidade_mm_s > 0, "velocidade_mm_s tem de ser > 0"),
            _opcao(self, "em_caso_de_erro", PoliticaErro),
            (self.orcamento_ms > 0, "orcamento_ms tem de ser > 0"),
        ])


@dataclass(frozen=True)
class ConfigCamara:
    """Características fixas da câmara (Blackfly S GigE 5 MP); só mudam se mudar a câmara."""

    modelo: str = D.CAMARA_MODELO
    largura_px: int = D.CAMARA_LARGURA_PX
    altura_px: int = D.CAMARA_ALTURA_PX
    pixel_um: float = D.CAMARA_PIXEL_UM
    fps_max: float = D.CAMARA_FPS_MAX
    formato_pixel: FormatoPixel = D.CAMARA_FORMATO_PIXEL
    exposicao_min_us: float = D.CAMARA_EXPOSICAO_MIN_US
    exposicao_max_us: float = D.CAMARA_EXPOSICAO_MAX_US
    pacote_bytes: int = D.CAMARA_PACOTE_BYTES

    def erros(self) -> list[str]:
        return _verificar([
            (self.largura_px > 0 and self.altura_px > 0, "largura_px e altura_px têm de ser > 0"),
            (self.pixel_um > 0, "pixel_um tem de ser > 0"),
            (self.fps_max > 0, "fps_max tem de ser > 0"),
            _opcao(self, "formato_pixel", FormatoPixel),
            (0 < self.exposicao_min_us < self.exposicao_max_us, "tem de ser 0 < exposicao_min_us < exposicao_max_us"),
            (576 <= self.pacote_bytes <= 9000, "pacote_bytes tem de estar entre 576 e 9000"),
        ])


@dataclass(frozen=True)
class ConfigLente:
    """Lente (Basler C23-0828-16M): fixa, exceto a distância ao tapete e a abertura."""

    modelo: str = D.LENTE_MODELO
    focal_mm: float = D.LENTE_FOCAL_MM
    circulo_imagem_mm: float = D.LENTE_CIRCULO_IMAGEM_MM
    abertura_f: float = D.LENTE_ABERTURA_F
    distancia_mm: float = D.LENTE_DISTANCIA_MM

    def erros(self) -> list[str]:
        return _verificar([
            (self.focal_mm > 0, "focal_mm tem de ser > 0"),
            (self.circulo_imagem_mm > 0, "circulo_imagem_mm tem de ser > 0"),
            (self.abertura_f >= 1, "abertura_f tem de ser ≥ 1"),
            (self.distancia_mm > self.focal_mm, "distancia_mm tem de ser maior do que focal_mm"),
        ])

    def campo_mm(self, sensor_mm: float) -> float:
        """Largura do campo de visão para uma largura de sensor (lente fina: sensor × (d − f) / f)."""
        return sensor_mm * (self.distancia_mm - self.focal_mm) / self.focal_mm


@dataclass(frozen=True)
class ConfigAquisicao:
    """M1 Aquisição: parâmetros da câmara Blackfly S."""

    exposicao_us: float = D.AQUISICAO_EXPOSICAO_US
    ganho_db: float = D.AQUISICAO_GANHO_DB

    def erros(self) -> list[str]:
        return _verificar([
            (self.exposicao_us > 0, "exposicao_us tem de ser > 0"),
            (self.ganho_db >= 0, "ganho_db tem de ser ≥ 0"),
        ])


@dataclass(frozen=True)
class ConfigDisparo:
    """M1 Aquisição, disparo (trigger): quando começa e acaba a passagem de cada saco."""

    modo: ModoDisparo = D.DISPARO_MODO
    fps: float = D.DISPARO_FPS
    sensor_estrategia: EstrategiaSensor = D.DISPARO_SENSOR_ESTRATEGIA
    sensor_invertido: bool = D.DISPARO_SENSOR_INVERTIDO
    sensor_filtro_us: float = D.DISPARO_SENSOR_FILTRO_US
    rajada_imagens: int = D.DISPARO_RAJADA_IMAGENS
    rajada_atraso_us: float = D.DISPARO_RAJADA_ATRASO_US
    anti_ressalto_ms: float = D.DISPARO_ANTI_RESSALTO_MS
    virtual_direcao: Direcao = D.DISPARO_VIRTUAL_DIRECAO
    virtual_faixa_mm: float = D.DISPARO_VIRTUAL_FAIXA_MM
    virtual_resolucao_px_por_mm: float = D.DISPARO_VIRTUAL_RESOLUCAO_PX_POR_MM
    virtual_delta_e: float = D.DISPARO_VIRTUAL_DELTA_E
    virtual_ocupacao_entrada: float = D.DISPARO_VIRTUAL_OCUPACAO_ENTRADA
    virtual_ocupacao_saida: float = D.DISPARO_VIRTUAL_OCUPACAO_SAIDA
    virtual_imagens_fundo: int = D.DISPARO_VIRTUAL_IMAGENS_FUNDO
    virtual_aprendizagem: float = D.DISPARO_VIRTUAL_APRENDIZAGEM
    imagens_min: int = D.DISPARO_IMAGENS_MIN
    duracao_max_ms: float = D.DISPARO_DURACAO_MAX_MS
    intervalo_max_ms: float = D.DISPARO_INTERVALO_MAX_MS
    imagens_max: int = D.DISPARO_IMAGENS_MAX
    candidatas: int = D.DISPARO_CANDIDATAS
    fila_max: int = D.DISPARO_FILA_MAX

    def erros(self) -> list[str]:
        return _verificar([
            _opcao(self, "modo", ModoDisparo),
            _opcao(self, "sensor_estrategia", EstrategiaSensor),
            _opcao(self, "virtual_direcao", Direcao),
            (self.virtual_faixa_mm > 0, "virtual_faixa_mm tem de ser > 0"),
            (self.fps > 0, "fps tem de ser > 0"),
            (self.sensor_filtro_us >= 0, "sensor_filtro_us tem de ser ≥ 0"),
            (self.rajada_imagens >= 1, "rajada_imagens tem de ser ≥ 1"),
            (self.rajada_atraso_us >= 0, "rajada_atraso_us tem de ser ≥ 0"),
            (self.anti_ressalto_ms >= 0, "anti_ressalto_ms tem de ser ≥ 0"),
            (self.virtual_resolucao_px_por_mm > 0, "virtual_resolucao_px_por_mm tem de ser > 0"),
            (self.virtual_delta_e > 0, "virtual_delta_e tem de ser > 0"),
            (0 <= self.virtual_ocupacao_saida < self.virtual_ocupacao_entrada <= 1,
             "tem de ser 0 ≤ virtual_ocupacao_saida < virtual_ocupacao_entrada ≤ 1"),
            (self.virtual_imagens_fundo >= 1, "virtual_imagens_fundo tem de ser ≥ 1"),
            (0 < self.virtual_aprendizagem <= 1, "virtual_aprendizagem tem de estar entre 0 e 1"),
            (self.imagens_min >= 1, "imagens_min tem de ser ≥ 1"),
            (self.duracao_max_ms > 0, "duracao_max_ms tem de ser > 0"),
            (self.intervalo_max_ms > 1000 / self.fps if self.fps > 0 else True,
             "intervalo_max_ms tem de ser maior do que o intervalo entre imagens (1000 / fps)"),
            (1 <= self.candidatas <= self.imagens_max, "tem de ser 1 ≤ candidatas ≤ imagens_max"),
            (self.rajada_imagens <= self.imagens_max, "rajada_imagens não pode ser maior do que imagens_max"),
            (self.fila_max >= 1, "fila_max tem de ser ≥ 1"),
        ])


@dataclass(frozen=True)
class ConfigLocalizacao:
    """M2 Localização: separar o saco do fundo, medi-lo e normalizá-lo."""

    resolucao_px_por_mm: float = D.LOCALIZACAO_RESOLUCAO_PX_POR_MM
    fundo_referencia: str = D.LOCALIZACAO_FUNDO_REFERENCIA
    faixa_fundo_mm: float = D.LOCALIZACAO_FAIXA_FUNDO_MM
    limiar_delta_e: float = D.LOCALIZACAO_LIMIAR_DELTA_E
    delta_e_min: float = D.LOCALIZACAO_DELTA_E_MIN
    peso_luminosidade: float = D.LOCALIZACAO_PESO_LUMINOSIDADE
    usar_arestas: bool = D.LOCALIZACAO_USAR_ARESTAS
    arestas_fator: float = D.LOCALIZACAO_ARESTAS_FATOR
    fecho_mm: float = D.LOCALIZACAO_FECHO_MM
    abertura_mm: float = D.LOCALIZACAO_ABERTURA_MM
    rejeitar_borda: bool = D.LOCALIZACAO_REJEITAR_BORDA
    comprimento_min_mm: float = D.LOCALIZACAO_COMPRIMENTO_MIN_MM
    comprimento_max_mm: float = D.LOCALIZACAO_COMPRIMENTO_MAX_MM
    largura_min_mm: float = D.LOCALIZACAO_LARGURA_MIN_MM
    largura_max_mm: float = D.LOCALIZACAO_LARGURA_MAX_MM
    folga_mm: float = D.LOCALIZACAO_FOLGA_MM
    margem_borda_mm: float = D.LOCALIZACAO_MARGEM_BORDA_MM
    margem_extremidades_mm: float = D.LOCALIZACAO_MARGEM_EXTREMIDADES_MM

    def erros(self) -> list[str]:
        return _verificar([
            (self.resolucao_px_por_mm > 0, "resolucao_px_por_mm tem de ser > 0"),
            (self.faixa_fundo_mm > 0, "faixa_fundo_mm tem de ser > 0"),
            (self.limiar_delta_e >= 0, "limiar_delta_e tem de ser ≥ 0 (0 = automático)"),
            (self.delta_e_min >= 0, "delta_e_min tem de ser ≥ 0"),
            (self.peso_luminosidade >= 0, "peso_luminosidade tem de ser ≥ 0"),
            (self.arestas_fator > 0, "arestas_fator tem de ser > 0"),
            (self.fecho_mm >= 0, "fecho_mm tem de ser ≥ 0"),
            (self.abertura_mm >= 0, "abertura_mm tem de ser ≥ 0"),
            (0 < self.comprimento_min_mm < self.comprimento_max_mm,
             "tem de ser 0 < comprimento_min_mm < comprimento_max_mm"),
            (0 < self.largura_min_mm < self.largura_max_mm, "tem de ser 0 < largura_min_mm < largura_max_mm"),
            (self.folga_mm >= 0, "folga_mm tem de ser ≥ 0"),
            (self.margem_borda_mm >= 0, "margem_borda_mm tem de ser ≥ 0"),
            (self.margem_extremidades_mm >= 0, "margem_extremidades_mm tem de ser ≥ 0"),
        ])


@dataclass(frozen=True)
class ConfigDimensoes:
    """M3 Dimensões: tamanho e forma face ao nominal."""

    ativo: bool = D.DIMENSOES_ATIVO
    comprimento_nominal_mm: float = D.DIMENSOES_COMPRIMENTO_NOMINAL_MM
    largura_nominal_mm: float = D.DIMENSOES_LARGURA_NOMINAL_MM
    tolerancia_comprimento_mm: float = D.DIMENSOES_TOLERANCIA_COMPRIMENTO_MM
    tolerancia_largura_mm: float = D.DIMENSOES_TOLERANCIA_LARGURA_MM
    forma_folga_mm: float = D.DIMENSOES_FORMA_FOLGA_MM
    forma_area_min_mm2: float = D.DIMENSOES_FORMA_AREA_MIN_MM2

    def erros(self) -> list[str]:
        return _verificar([
            (self.comprimento_nominal_mm > 0, "comprimento_nominal_mm tem de ser > 0"),
            (self.largura_nominal_mm > 0, "largura_nominal_mm tem de ser > 0"),
            (self.tolerancia_comprimento_mm >= 0, "tolerancia_comprimento_mm tem de ser ≥ 0"),
            (self.tolerancia_largura_mm >= 0, "tolerancia_largura_mm tem de ser ≥ 0"),
            (self.forma_folga_mm >= 0, "forma_folga_mm tem de ser ≥ 0"),
            (self.forma_area_min_mm2 > 0, "forma_area_min_mm2 tem de ser > 0"),
        ])


@dataclass(frozen=True)
class ConfigCor:
    """M4 Cor: cor média do saco face à referência."""

    ativo: bool = D.COR_ATIVO
    referencia_lab: list[float] = field(default_factory=lambda: list(D.COR_REFERENCIA_LAB))
    delta_e_max: float = D.COR_DELTA_E_MAX

    def erros(self) -> list[str]:
        return _verificar([
            (len(self.referencia_lab) == 3, "referencia_lab tem de ter 3 valores (L*, a*, b*)"),
            (len(self.referencia_lab) == 3 and 0 <= self.referencia_lab[0] <= 100,
             "o L* de referencia_lab tem de estar entre 0 e 100"),
            (self.delta_e_max > 0, "delta_e_max tem de ser > 0"),
        ])


@dataclass(frozen=True)
class ConfigRelevo:
    """M5 Relevo: riscos, dobras, rugas e amassos (cristas na imagem)."""

    ativo: bool = D.RELEVO_ATIVO
    canal: Canal = D.RELEVO_CANAL
    fundo_sigma_mm: float = D.RELEVO_FUNDO_SIGMA_MM
    crista_sigmas_mm: list[float] = field(default_factory=lambda: list(D.RELEVO_CRISTA_SIGMAS_MM))
    crista_polaridade: Polaridade = D.RELEVO_CRISTA_POLARIDADE
    crista_limiar_alto: float = D.RELEVO_CRISTA_LIMIAR_ALTO
    crista_limiar_baixo: float = D.RELEVO_CRISTA_LIMIAR_BAIXO
    linha_comprimento_min_mm: float = D.RELEVO_LINHA_COMPRIMENTO_MIN_MM
    linha_alongamento_min: float = D.RELEVO_LINHA_ALONGAMENTO_MIN
    risco_largura_max_mm: float = D.RELEVO_RISCO_LARGURA_MAX_MM
    dobra_largura_max_mm: float = D.RELEVO_DOBRA_LARGURA_MAX_MM
    linha_ligar_distancia_mm: float = D.RELEVO_LINHA_LIGAR_DISTANCIA_MM
    linha_ligar_angulo_graus: float = D.RELEVO_LINHA_LIGAR_ANGULO_GRAUS
    zona_janela_mm: float = D.RELEVO_ZONA_JANELA_MM
    zona_densidade_min: float = D.RELEVO_ZONA_DENSIDADE_MIN
    zona_area_min_mm2: float = D.RELEVO_ZONA_AREA_MIN_MM2
    zona_linhas_min: int = D.RELEVO_ZONA_LINHAS_MIN
    rugas_coerencia_min: float = D.RELEVO_RUGAS_COERENCIA_MIN

    def erros(self) -> list[str]:
        return _verificar([
            _opcao(self, "canal", Canal),
            _opcao(self, "crista_polaridade", Polaridade),
            (self.fundo_sigma_mm > 0, "fundo_sigma_mm tem de ser > 0"),
            (len(self.crista_sigmas_mm) > 0 and all(s > 0 for s in self.crista_sigmas_mm),
             "crista_sigmas_mm tem de ter pelo menos uma escala e todas > 0"),
            (0 <= self.crista_limiar_baixo <= self.crista_limiar_alto,
             "tem de ser 0 ≤ crista_limiar_baixo ≤ crista_limiar_alto"),
            (self.linha_comprimento_min_mm > 0, "linha_comprimento_min_mm tem de ser > 0"),
            (self.linha_alongamento_min >= 1, "linha_alongamento_min tem de ser ≥ 1"),
            (0 < self.risco_largura_max_mm < self.dobra_largura_max_mm,
             "tem de ser 0 < risco_largura_max_mm < dobra_largura_max_mm"),
            (self.linha_ligar_distancia_mm >= 0, "linha_ligar_distancia_mm tem de ser ≥ 0"),
            (0 <= self.linha_ligar_angulo_graus <= 90, "linha_ligar_angulo_graus tem de estar entre 0 e 90"),
            (self.zona_janela_mm > 0, "zona_janela_mm tem de ser > 0"),
            (0 < self.zona_densidade_min < 1, "zona_densidade_min tem de estar entre 0 e 1"),
            (self.zona_area_min_mm2 >= 0, "zona_area_min_mm2 tem de ser ≥ 0"),
            (self.zona_linhas_min >= 1, "zona_linhas_min tem de ser ≥ 1"),
            (0 <= self.rugas_coerencia_min <= 1, "rugas_coerencia_min tem de estar entre 0 e 1"),
        ])


@dataclass(frozen=True)
class ConfigManchas:
    """M6 Manchas: zonas mais claras ou mais escuras do que a vizinhança."""

    ativo: bool = D.MANCHAS_ATIVO
    canal: Canal = D.MANCHAS_CANAL
    fundo_sigma_mm: float = D.MANCHAS_FUNDO_SIGMA_MM
    polaridade: Polaridade = D.MANCHAS_POLARIDADE
    contraste_min: float = D.MANCHAS_CONTRASTE_MIN
    area_min_mm2: float = D.MANCHAS_AREA_MIN_MM2
    alongamento_max: float = D.MANCHAS_ALONGAMENTO_MAX

    def erros(self) -> list[str]:
        return _verificar([
            _opcao(self, "canal", Canal),
            _opcao(self, "polaridade", Polaridade),
            (self.fundo_sigma_mm > 0, "fundo_sigma_mm tem de ser > 0"),
            (self.contraste_min > 0, "contraste_min tem de ser > 0"),
            (self.area_min_mm2 >= 0, "area_min_mm2 tem de ser ≥ 0"),
            (self.alongamento_max >= 1, "alongamento_max tem de ser ≥ 1"),
        ])


@dataclass(frozen=True)
class ConfigSaida:
    """M7 Saída: imagens anotadas e registos CSV."""

    guardar_imagens: GuardarImagens = D.SAIDA_GUARDAR_IMAGENS
    formato_imagem: FormatoImagem = D.SAIDA_FORMATO_IMAGEM
    separador_decimal: SeparadorDecimal = D.SAIDA_SEPARADOR_DECIMAL
    depurar: GuardarImagens = D.SAIDA_DEPURAR
    painel_colunas: int = D.SAIDA_PAINEL_COLUNAS
    painel_largura_etapa_px: int = D.SAIDA_PAINEL_LARGURA_ETAPA_PX

    def erros(self) -> list[str]:
        return _verificar([
            _opcao(self, "guardar_imagens", GuardarImagens),
            _opcao(self, "formato_imagem", FormatoImagem),
            _opcao(self, "separador_decimal", SeparadorDecimal),
            _opcao(self, "depurar", GuardarImagens),
            (self.painel_colunas >= 1, "painel_colunas tem de ser ≥ 1"),
            (self.painel_largura_etapa_px >= 160, "painel_largura_etapa_px tem de ser ≥ 160"),
        ])


# --- Configuração completa --------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Todas as secções. O nome de cada secção é o nome do módulo que a usa."""

    geral: ConfigGeral = field(default_factory=ConfigGeral)
    camara: ConfigCamara = field(default_factory=ConfigCamara)
    lente: ConfigLente = field(default_factory=ConfigLente)
    aquisicao: ConfigAquisicao = field(default_factory=ConfigAquisicao)
    disparo: ConfigDisparo = field(default_factory=ConfigDisparo)
    localizacao: ConfigLocalizacao = field(default_factory=ConfigLocalizacao)
    dimensoes: ConfigDimensoes = field(default_factory=ConfigDimensoes)
    cor: ConfigCor = field(default_factory=ConfigCor)
    relevo: ConfigRelevo = field(default_factory=ConfigRelevo)
    manchas: ConfigManchas = field(default_factory=ConfigManchas)
    saida: ConfigSaida = field(default_factory=ConfigSaida)

    def __post_init__(self) -> None:
        self.validar()

    # --- Conversões (sempre com geral.px_por_mm) -----------------------------------
    def mm_para_px(self, mm: float) -> float:
        """Converte um comprimento de mm para píxeis."""
        return mm * self.geral.px_por_mm

    def px_para_mm(self, px: float) -> float:
        """Converte um comprimento de píxeis para mm."""
        return px / self.geral.px_por_mm

    def mm2_para_px2(self, mm2: float) -> float:
        """Converte uma área de mm² para píxeis²."""
        return mm2 * self.geral.px_por_mm**2

    # --- Validação ------------------------------------------------------------------
    def validar(self) -> None:
        """Verifica todas as secções; levanta ValueError com todos os problemas de uma vez."""
        erros = [f"{f.name}.{erro}" for f in fields(self) for erro in getattr(self, f.name).erros()]
        # Regras entre secções: a câmara tem de conseguir o que se lhe pede.
        cam, aq, disp = self.camara, self.aquisicao, self.disparo
        if disp.fps > cam.fps_max:
            erros.append(f"disparo.fps ({disp.fps:g}) é maior do que o máximo da câmara (camara.fps_max = {cam.fps_max:g})")
        if not cam.exposicao_min_us <= aq.exposicao_us <= cam.exposicao_max_us:
            erros.append(f"aquisicao.exposicao_us ({aq.exposicao_us:g}) fora da gama da câmara "
                         f"({cam.exposicao_min_us:g} a {cam.exposicao_max_us:g} µs)")
        if disp.fps > 0 and aq.exposicao_us > 1e6 / disp.fps:
            erros.append(f"aquisicao.exposicao_us ({aq.exposicao_us:g}) não cabe no intervalo entre imagens "
                         f"(1/disparo.fps = {1e6 / disp.fps:.0f} µs)")
        diagonal_sensor = math.hypot(cam.largura_px, cam.altura_px) * cam.pixel_um / 1000
        if self.lente.circulo_imagem_mm < diagonal_sensor:
            erros.append(f"lente.circulo_imagem_mm ({self.lente.circulo_imagem_mm:g}) não cobre a diagonal do "
                         f"sensor ({diagonal_sensor:.1f} mm): os cantos da imagem ficam escuros")
        # O M2 não pode rejeitar sacos com as medidas nominais.
        loc, dim = self.localizacao, self.dimensoes
        if not loc.comprimento_min_mm <= dim.comprimento_nominal_mm <= loc.comprimento_max_mm:
            erros.append("dimensoes.comprimento_nominal_mm está fora dos limites de localizacao.comprimento_*")
        if not loc.largura_min_mm <= dim.largura_nominal_mm <= loc.largura_max_mm:
            erros.append("dimensoes.largura_nominal_mm está fora dos limites de localizacao.largura_*")
        if erros:
            raise ValueError(
                "Configuração inválida:\n  - " + "\n  - ".join(erros)
                + "\n(os valores estão em nucleo/defines.py, p.ex. geral.px_por_mm → GERAL_PX_POR_MM,"
                " ou no config.json, se existir)"
            )

    # --- Diferenças face aos defines ---------------------------------------------------
    def alteracoes(self) -> dict[str, dict[str, Any]]:
        """Os valores que diferem dos defines, por secção: {"geral": {"px_por_mm": 12.4}}."""
        atuais, defines = asdict(self), asdict(Config())
        return {
            secao: diferentes
            for secao in atuais
            if (diferentes := {k: v for k, v in atuais[secao].items() if v != defines[secao][k]})
        }

    def descrever_alteracoes(self) -> list[str]:
        """As alterações em texto, p.ex. "geral.px_por_mm = 12.4 (define GERAL_PX_POR_MM = 5.0)"."""
        defines = asdict(Config())
        return [
            f"{secao}.{nome} = {valor!r} (define {nome_define(secao, nome)} = {defines[secao][nome]!r})"
            for secao, valores in self.alteracoes().items()
            for nome, valor in valores.items()
        ]

    # --- JSON ----------------------------------------------------------------------
    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> Config:
        """Cria uma Config a partir de um dicionário (p.ex. lido de JSON).

        Secções ou parâmetros em falta ficam com o valor dos defines. Nomes
        desconhecidos (gralhas, parâmetros que mudaram de nome) e tipos errados
        dão ValueError, com o caminho completo (p.ex. "relevo.crista_limar_alto").
        """
        secoes = {f.name: f.default_factory for f in fields(cls)}
        desconhecidas = sorted(set(dados) - set(secoes))
        if desconhecidas:
            raise ValueError("Secções desconhecidas na configuração: " + ", ".join(desconhecidas))
        valores = {}
        for nome, conteudo in dados.items():
            if not isinstance(conteudo, dict):
                raise ValueError(f"A secção '{nome}' devia ser um objeto JSON {{...}}.")
            valores[nome] = _secao_de_dict(secoes[nome], conteudo, nome)
        return cls(**valores)

    @classmethod
    def carregar(cls, caminho: str | Path = CAMINHO_POR_OMISSAO) -> Config:
        """Defines + os valores do ficheiro JSON por cima."""
        # utf-8-sig: aceita ficheiros guardados com BOM (Bloco de Notas, PowerShell 5.1).
        dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
        if not isinstance(dados, dict):
            raise ValueError(f"{caminho}: esperava um objeto JSON {{...}} com as secções.")
        return cls.de_dict(dados)

    @classmethod
    def carregar_se_existir(cls, caminho: str | Path = CAMINHO_POR_OMISSAO) -> Config:
        """Como carregar(), mas sem ficheiro devolve só os defines."""
        return cls.carregar(caminho) if Path(caminho).exists() else cls()

    def guardar(self, caminho: str | Path = CAMINHO_POR_OMISSAO, completo: bool = False) -> None:
        """Escreve um JSON com os valores que diferem dos defines (ou todos, com completo=True).

        Guardar só as diferenças é de propósito: assim, mudar um define tem
        sempre efeito, a não ser que o JSON o mude explicitamente.
        """
        self.validar()
        dados = asdict(self) if completo else self.alteracoes()
        texto = json.dumps(dados, indent=2, ensure_ascii=False)
        Path(caminho).write_text(texto + "\n", encoding="utf-8")


def _secao_de_dict(classe: Any, dados: dict[str, Any], prefixo: str) -> Any:
    """Cria uma secção a partir de um dicionário, verificando nomes e tipos."""
    omissao = asdict(classe())
    desconhecidos = sorted(set(dados) - set(omissao))
    if desconhecidos:
        raise ValueError(
            "Parâmetros desconhecidos na configuração: " + ", ".join(f"{prefixo}.{n}" for n in desconhecidos)
        )
    valores: dict[str, Any] = {}
    for nome, valor in dados.items():
        referencia = omissao[nome]
        # Num JSON editado à mão, 5.0 aparece muitas vezes como 5.
        if isinstance(referencia, float) and type(valor) is int:
            valor = float(valor)
        elif isinstance(referencia, list) and isinstance(valor, list):
            valor = [float(v) if type(v) is int else v for v in valor]
            if not all(type(v) is float for v in valor):
                raise ValueError(f"'{prefixo}.{nome}' devia ser uma lista de números: {valor!r}")
        if type(valor) is not type(referencia):
            raise ValueError(
                f"'{prefixo}.{nome}' devia ser {type(referencia).__name__}, "
                f"mas é {type(valor).__name__}: {valor!r}"
            )
        valores[nome] = valor
    return classe(**valores)


def carregar_configuracao(caminho: str | Path = CAMINHO_POR_OMISSAO) -> Config:
    """A configuração a usar pelos programas: os defines e, por cima, o JSON.

    - config.json (o ficheiro por omissão) não existe → só os defines;
    - um ficheiro pedido explicitamente (--config) não existe → FileNotFoundError;
    - existe → defines + JSON, e regista no log o que o JSON muda.
    """
    log = logging.getLogger("config")
    caminho = Path(caminho)
    if not caminho.exists():
        if caminho.resolve() != CAMINHO_POR_OMISSAO.resolve():
            raise FileNotFoundError(f"Ficheiro de configuração não encontrado: {caminho}")
        log.info("Sem config.json: a usar os valores de nucleo/defines.py.")
        return Config()
    cfg = Config.carregar(caminho)
    alteracoes = cfg.descrever_alteracoes()
    if alteracoes:
        log.info("%s muda %d valor(es) dos defines:\n    %s", caminho.name, len(alteracoes), "\n    ".join(alteracoes))
    else:
        log.info("%s não muda nenhum valor dos defines.", caminho.name)
    return cfg


def _mostrar(cfg: Config, origem: str) -> None:
    """Escreve todos os valores em uso, assinalando com * os que o JSON muda."""
    alterados = cfg.alteracoes()
    print(f"Valores em uso ({origem}); * = mudado pelo JSON em relação a nucleo/defines.py\n")
    for secao, valores in asdict(cfg).items():
        print(f"[{secao}]")
        for nome, valor in valores.items():
            marca = "*" if nome in alterados.get(secao, {}) else " "
            print(f" {marca} {nome:<28} = {valor!r:<14}  {nome_define(secao, nome)}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mostrar os valores de configuração em uso (defines + JSON).")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="ficheiro JSON (omissão: config.json)")
    parser.add_argument("--exportar", type=Path, help="escrever TODOS os valores num ficheiro JSON (completo)")
    args = parser.parse_args()

    existe = args.config.exists()
    cfg = Config.carregar_se_existir(args.config)
    _mostrar(cfg, f"defines + {args.config.name}" if existe else "só defines; não há config.json")
    if args.exportar:
        cfg.guardar(args.exportar, completo=True)
        print(f"Todos os valores escritos em {args.exportar}")
