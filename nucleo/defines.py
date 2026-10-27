"""DEFINES: todos os valores que podem variar, num só sítio.

Para mudar um valor, muda-o aqui; o resto do programa vai buscá-lo.

    [MEDIR]  valor provisório que TEM de ser medido para a montagem ou o
             produto real (régua, sacos de referência...).
    (resto)  ponto de partida razoável, a afinar com imagens reais
             (python -m ferramentas.afinar <módulo> <imagem>).

Ligação à configuração: cada define tem o nome do parâmetro em maiúsculas,
com a secção à frente:

    LOCALIZACAO_COMPRIMENTO_MIN_MM  ↔  "localizacao": {"comprimento_min_mm": ...}

Um config.json (opcional) pode sobrepor alguns valores para uma montagem ou um
produto sem mexer aqui, p.ex. {"geral": {"px_por_mm": 12.4}} para o telemóvel.
O programa diz no arranque que valores o config.json está a mudar.
Para ver todos os valores em uso: python -m nucleo.config

Os defines com o prefixo M2_, M3_... são afinações internas de um módulo; não
aparecem na configuração e normalmente não é preciso mexer neles.

Unidades: comprimentos em mm, áreas em mm², tempos em ms (ou µs quando dito),
ângulos em graus. A conversão para píxeis é feita com GERAL_PX_POR_MM.
"""

from typing import Final

# =============================================================================
# GERAL
# =============================================================================
# Píxeis por mm no plano do tapete ≈ 2448 px / largura do campo de visão (mm).
# Com a lente de 8 mm a 300 mm do tapete: campo de ~308 × 258 mm → ~7,9 px/mm
# (0,13 mm por píxel). Depois de montar, medir com uma régua; a calculadora
# (python -m ferramentas.calcular_montagem) avisa se este valor não bater com a
# lente. Telemóvel e outras montagens: noutro config.json.
GERAL_PX_POR_MM: Final = 8.0  # [MEDIR]
# Velocidade do tapete: só importa na fase 3 de ensaios (tapete a andar); com o saco
# parado não é usada. Ver as fases de ensaio em modulos/m1_aquisicao/__init__.py.
GERAL_VELOCIDADE_MM_S: Final = 500.0  # [MEDIR] (só com o tapete a andar)
GERAL_EM_CASO_DE_ERRO: Final = "rejeitar"  # se um módulo der ERRO: "rejeitar" o saco | "ignorar" o módulo
# Tempo entre sacos = 1000 / sacos por segundo da linha (2 sacos/s → 500 ms). A inspeção e
# o registo de cada saco têm de caber aqui, em média; senão a fila do M1 enche e passam
# sacos sem inspeção (ERRO). Para ter folga, apontar para ~70 % deste valor.
GERAL_ORCAMENTO_MS: Final = 500.0  # [MEDIR] confirmar o ritmo real da linha

# =============================================================================
# CÂMARA: FLIR Blackfly S BFS-PGE-50S5C-C (PoE GigE, cor, Sony IMX264) — fixas
# =============================================================================
# Só se mudam se se mudar de câmara. Outras características: sensor 2/3"
# (8,45 × 7,07 mm), obturador global, 12 bits, buffer interno de 240 MB (guarda
# ~48 imagens se a rede atrasar), GigE Vision 1.2, alimentação por PoE.
# Montar a câmara com o lado de 2448 px ao longo do movimento do tapete: dá
# mais imagens com o saco inteiro à vista.
CAMARA_MODELO: Final = "BFS-PGE-50S5C-C"
CAMARA_LARGURA_PX: Final = 2448
CAMARA_ALTURA_PX: Final = 2048
CAMARA_PIXEL_UM: Final = 3.45  # tamanho de cada píxel no sensor (µm)
# 24 FPS é o máximo a resolução total por GigE, com píxeis de 8 bits em Bayer:
# 5 MB por imagem × 24 ≈ 120 MB/s, o limite de uma ligação GigE. Com "BGR8" (cor
# feita na câmara, 3× mais dados) desce para ~8 FPS; com "BayerRG12p", para ~16.
CAMARA_FPS_MAX: Final = 24.0
CAMARA_FORMATO_PIXEL: Final = "BayerRG8"  # "BayerRG8" | "BayerRG12p" | "BGR8"; com Bayer a cor faz-se no PC
CAMARA_EXPOSICAO_MIN_US: Final = 11.0
CAMARA_EXPOSICAO_MAX_US: Final = 30_000_000.0  # 30 s
CAMARA_PACOTE_BYTES: Final = 9000  # jumbo frames; ativar também na placa de rede do PC ("Jumbo Packet" 9014)

# =============================================================================
# LENTE: Basler C23-0828-16M (características fixas, exceto a distância e a abertura)
# =============================================================================
# 8 mm, C-mount, para sensores até 2/3"; ângulo de visão horizontal 56,4°; foco de
# 0,1 m ao infinito; distância ótima de trabalho 0,3 m; distorção de 0,07 %
# (desprezável: não é preciso corrigir a distorção para medir).
# Grande-angular: um saco "alto" (almofadado) parece maior do que é, e mais ainda
# longe do centro da imagem; a calculadora estima esse erro. Para medidas mais
# exatas no futuro: a C23-1628-16M (16 mm) a ~600 mm reduz o efeito para metade.
LENTE_MODELO: Final = "Basler C23-0828-16M"
LENTE_FOCAL_MM: Final = 8.0
LENTE_CIRCULO_IMAGEM_MM: Final = 11.8  # tem de cobrir a diagonal do sensor (11,0 mm), senão os cantos escurecem
LENTE_ABERTURA_F: Final = 4.0  # [MEDIR] F4 é a abertura mais nítida (gama F2.8–F16); fechar mais pede mais luz
LENTE_DISTANCIA_MM: Final = 300.0  # [MEDIR] da frente da lente ao tapete (0,3 m é a distância ótima da lente)

# =============================================================================
# M1 AQUISIÇÃO
# =============================================================================
# Com o saco parado (fases 1 e 2 de ensaios) a exposição pode ser longa: escolhe-se
# só pelo brilho da imagem. Com o tapete a andar (fase 3), arrasto do movimento (px)
# = velocidade · exposição · px_por_mm; para ≤ 0,5 px a 500 mm/s e 8 px/mm:
# exposição ≤ 0,5 / (500 × 8) s = 125 µs, o que pede muita luz (LED forte ou
# estroboscópica). Tem de estar entre 11 µs e 1/FPS.
AQUISICAO_EXPOSICAO_US: Final = 120.0  # [MEDIR]
AQUISICAO_GANHO_DB: Final = 0.0  # ganho da câmara em dB; subir só se faltar luz (aumenta o ruído)

# =============================================================================
# M1 AQUISIÇÃO: DISPARO (trigger) — quando começa e acaba a passagem de cada saco
# =============================================================================
# Uma PASSAGEM é a sequência de imagens de um saco, desde que entra até que sai.
# O Mestre analisa a(s) melhor(es) imagem(ns) de cada passagem e dá UMA decisão
# por saco.
#   "foto"    cada imagem é um saco: fotos de uma pasta; ou a câmara com um BOTÃO
#             (ou o sensor) na entrada Line0: cada vez que se carrega, uma imagem
#   "sensor"  sensor de presença ligado à entrada Line0 da câmara
#   "virtual" sem sensor: a entrada e a saída do saco detetam-se na própria imagem
#             (vídeos do telemóvel; e a câmara enquanto não houver sensor)
# Fotos do telemóvel: "foto". Câmara com o botão na Line0 (ensaios com o saco
# parado): "foto". Câmara sem botão nem sensor: "virtual".
DISPARO_MODO: Final = "foto"
# Imagens por segundo enquanto o saco passa (no máximo CAMARA_FPS_MAX). Imagens com
# o saco COMPLETO à vista ≈ FPS × (campo de visão − comprimento do saco) / velocidade;
# tem de dar pelo menos 1 (melhor 3 ou mais). Com esta câmara (campo de 306 mm a
# 8 px/mm, tapete a 500 mm/s): saco de 200 mm → 24 × (306 − 200) / 500 ≈ 5 imagens.
DISPARO_FPS: Final = 24.0

# --- Modo "sensor" ----------------------------------------------------------------
#   "janela"  a câmara capta em contínuo e grava em cada imagem o estado do sensor;
#             a passagem são as imagens com o sensor ativo (alinhar o sensor com o
#             centro do campo de visão)
#   "rajada"  cada ativação do sensor dispara uma rajada de N imagens
DISPARO_SENSOR_ESTRATEGIA: Final = "janela"
DISPARO_SENSOR_INVERTIDO: Final = False  # True se o sensor/botão dá 0 V quando atua (inverte a Line0)
DISPARO_SENSOR_FILTRO_US: Final = 100.0  # ignora impulsos do sensor mais curtos do que isto (ruído, ressaltos)
DISPARO_RAJADA_IMAGENS: Final = 5  # [MEDIR] imagens por ativação, no modo "rajada"
# [MEDIR] atraso entre o sensor e a 1.ª imagem da rajada, para o saco ficar centrado a
# meio da rajada (ver docs/camara.md). Com DISPARO_MODO "foto" e a câmara, é o
# atraso da única imagem.
DISPARO_RAJADA_ATRASO_US: Final = 0.0
# Botão (modo "foto" com a câmara): um botão mecânico "salta" durante alguns ms ao
# fechar e cada salto pode disparar uma imagem. Imagens disparadas a menos do que
# isto da anterior são ignoradas (ninguém carrega duas vezes em 0,3 s de propósito).
DISPARO_ANTI_RESSALTO_MS: Final = 300.0

# --- Modo "virtual" (barreira virtual na imagem) -----------------------------------
# Como uma barreira ótica, mas na imagem: vigia uma faixa estreita no centro,
# perpendicular ao movimento do tapete. A passagem dura enquanto o saco atravessa
# a faixa (e o saco está inteiro à vista por volta do meio da passagem).
# Arrancar com o tapete VAZIO: as primeiras imagens ensinam o aspeto do fundo.
DISPARO_VIRTUAL_DIRECAO: Final = "horizontal"  # [MEDIR] sentido do tapete na imagem: "horizontal" | "vertical"
DISPARO_VIRTUAL_FAIXA_MM: Final = 20.0  # largura da faixa vigiada, ao longo do movimento
DISPARO_VIRTUAL_RESOLUCAO_PX_POR_MM: Final = 0.5  # miniatura usada para detetar a presença (rápido)
DISPARO_VIRTUAL_DELTA_E: Final = 10.0  # diferença de cor ao fundo (ΔE) para um píxel contar como "saco"
DISPARO_VIRTUAL_OCUPACAO_ENTRADA: Final = 0.03  # o saco ENTROU quando ocupa mais do que esta fração da faixa
DISPARO_VIRTUAL_OCUPACAO_SAIDA: Final = 0.01  # o saco SAIU quando ocupa menos do que esta (histerese)
DISPARO_VIRTUAL_IMAGENS_FUNDO: Final = 5  # imagens iniciais (tapete vazio) usadas para aprender o fundo
DISPARO_VIRTUAL_APRENDIZAGEM: Final = 0.05  # quanto o fundo se adapta a cada imagem sem saco (luz que muda)

# --- Validação das passagens e desempenho -------------------------------------------
DISPARO_IMAGENS_MIN: Final = 1  # passagens com menos imagens são ignoradas (subir para 2–3 contra disparos falsos)
DISPARO_DURACAO_MAX_MS: Final = 2000.0  # acima disto: sacos encostados ou tapete parado → aviso e fecha
DISPARO_INTERVALO_MAX_MS: Final = 200.0  # sem imagens durante mais do que isto, a passagem fecha (> 1000/FPS)
DISPARO_IMAGENS_MAX: Final = 12  # imagens guardadas por passagem (cada imagem a cores de 5 MP ocupa 15 MB)
DISPARO_CANDIDATAS: Final = 3  # melhores imagens que o Mestre experimenta até encontrar o saco completo
DISPARO_FILA_MAX: Final = 4  # passagens à espera de análise; acima disto a análise não acompanha a linha

# =============================================================================
# M2 LOCALIZAÇÃO (encontrar o saco, medir, endireitar)
# =============================================================================
LOCALIZACAO_RESOLUCAO_PX_POR_MM: Final = 4.0  # resolução da segmentação (a imagem é reduzida para isto)
LOCALIZACAO_FUNDO_REFERENCIA: Final = ""  # foto do fundo vazio (recomendado); "" = estimar pela borda da imagem
LOCALIZACAO_FAIXA_FUNDO_MM: Final = 5.0  # faixa junto à borda da imagem usada para estimar o fundo
LOCALIZACAO_LIMIAR_DELTA_E: Final = 0.0  # diferença de cor (ΔE) a partir da qual é saco; 0 = automático (Otsu)
LOCALIZACAO_DELTA_E_MIN: Final = 5.0  # o limiar automático nunca desce abaixo disto (ruído do fundo)
LOCALIZACAO_PESO_LUMINOSIDADE: Final = 1.0  # < 1 ignora mais as sombras; sacos brancos/pretos precisam de 1
LOCALIZACAO_USAR_ARESTAS: Final = True  # usar arestas quando a cor não chega (sacos transparentes)
LOCALIZACAO_ARESTAS_FATOR: Final = 3.0  # aresta = gradiente acima de fator × o gradiente normal do fundo
LOCALIZACAO_FECHO_MM: Final = 3.0  # fecho morfológico: tapa reflexos e falhas na máscara
LOCALIZACAO_ABERTURA_MM: Final = 1.5  # abertura morfológica: tira pontos soltos e fios
LOCALIZACAO_REJEITAR_BORDA: Final = True  # ignorar sacos que tocam na borda da imagem (incompletos)
# Limites LARGOS, só para "isto é um saco?" (as tolerâncias do produto são do M3).
LOCALIZACAO_COMPRIMENTO_MIN_MM: Final = 80.0
LOCALIZACAO_COMPRIMENTO_MAX_MM: Final = 230.0
LOCALIZACAO_LARGURA_MIN_MM: Final = 20.0
LOCALIZACAO_LARGURA_MAX_MM: Final = 100.0
# Recorte normalizado e zona inspecionada pelos outros módulos.
LOCALIZACAO_FOLGA_MM: Final = 3.0  # margem de fundo à volta do saco no recorte
LOCALIZACAO_MARGEM_BORDA_MM: Final = 3.0  # faixa junto ao contorno do saco que não é inspecionada
LOCALIZACAO_MARGEM_EXTREMIDADES_MM: Final = 5.0  # [MEDIR] faixa extra nas pontas do saco (selagens)

# --- M2: afinação interna ----------------------------------------------------------
M2_SUAVIZACAO_MM: Final = 0.25  # desfoque antes de comparar com o fundo (tira o ruído do sensor)
M2_GRADIENTE_FUNDO_MIN: Final = 0.5  # piso do "gradiente normal do fundo" (ΔE/px), para fundos muito lisos
M2_RETANGULARIDADE_SO_COR: Final = 0.8  # abaixo disto a região de cor é um pedaço de saco → tentar com arestas
M2_PARTE_CENTRAL_LADOS: Final = 0.7  # fração central de cada lado usada para o medir (sem os cantos)
M2_ANGULO_ITERACOES: Final = 2  # voltas de afinação do ângulo pelos lados compridos
M2_ANGULO_DESVIO_PX: Final = 2.0  # nessa afinação, pontos mais afastados da reta do que isto (abas) não contam
M2_FUNDO_PONTOS_MAX: Final = 5000  # pontos da faixa da borda usados no modelo do fundo
M2_FUNDO_ITERACOES: Final = 3  # voltas do ajuste robusto do modelo do fundo
M2_FUNDO_DESVIOS: Final = 3.0  # num ajuste, descarta pontos a mais de N desvios (sujidade, cantos de saco)
M2_FUNDO_TOLERANCIA_DELTA_E: Final = 1.0  # folga fixa nesse descarte (ΔE)

# =============================================================================
# M3 DIMENSÕES (o saco tem o tamanho e a forma certos?)
# =============================================================================
DIMENSOES_ATIVO: Final = True
DIMENSOES_COMPRIMENTO_NOMINAL_MM: Final = 150.0  # [MEDIR] comprimento do produto
DIMENSOES_LARGURA_NOMINAL_MM: Final = 55.0  # [MEDIR] largura do produto
DIMENSOES_TOLERANCIA_COMPRIMENTO_MM: Final = 3.0  # [MEDIR] aceita nominal ± tolerância
DIMENSOES_TOLERANCIA_LARGURA_MM: Final = 3.0  # [MEDIR]
# Forma: a silhueta do saco é comparada com o retângulo ideal do tamanho medido.
# Zonas onde FALTA material (canto dobrado, corte torto, rasgão na borda) ou SOBRA
# (aba, dobra para fora) são defeitos, cada um com a sua posição.
DIMENSOES_FORMA_FOLGA_MM: Final = 1.5  # desvios junto às arestas até isto são ignorados (cantos arredondados, soldaduras)
DIMENSOES_FORMA_AREA_MIN_MM2: Final = 25.0  # área mínima de uma falta ou sobra para ser defeito

# =============================================================================
# M4 COR (o saco tem a cor certa?)
# =============================================================================
COR_ATIVO: Final = True  # desligar com fotos de telemóvel (balanço de brancos automático)
COR_REFERENCIA_LAB: Final = (70.0, 0.0, 0.0)  # [MEDIR] cor média de sacos OK em CIELAB (L* 0–100, a*, b*)
COR_DELTA_E_MAX: Final = 6.0  # diferença de cor máxima aceite (ΔE)

# =============================================================================
# M5 RELEVO (riscos, dobras, rugas, amassos)
# =============================================================================
RELEVO_ATIVO: Final = True
RELEVO_CANAL: Final = "cinzento"  # "cinzento" | "b" | "g" | "r" | "h" | "s" | "v"
RELEVO_FUNDO_SIGMA_MM: Final = 5.0  # desfoque que estima a iluminação; bem maior do que os defeitos
RELEVO_CRISTA_SIGMAS_MM: Final = (0.25, 0.5, 1.0, 2.0)  # escalas da Hessiana (larguras de crista)
RELEVO_CRISTA_POLARIDADE: Final = "ambas"  # "ambas" | "clara" | "escura"
RELEVO_CRISTA_LIMIAR_ALTO: Final = 0.05  # histerese: sementes
RELEVO_CRISTA_LIMIAR_BAIXO: Final = 0.025  # histerese: continuação de uma semente
RELEVO_LINHA_COMPRIMENTO_MIN_MM: Final = 10.0  # linha mais curta que conta como defeito
RELEVO_LINHA_ALONGAMENTO_MIN: Final = 5.0  # comprimento / largura mínimo para ser "linha"
RELEVO_RISCO_LARGURA_MAX_MM: Final = 1.0  # largura ≤ isto → risco; acima → dobra
RELEVO_DOBRA_LARGURA_MAX_MM: Final = 6.0  # acima disto a crista não é considerada linha
RELEVO_LINHA_LIGAR_DISTANCIA_MM: Final = 2.0  # religar troços de uma linha separados por menos do que isto
RELEVO_LINHA_LIGAR_ANGULO_GRAUS: Final = 20.0  # ... e com direções que diferem menos do que isto
RELEVO_ZONA_JANELA_MM: Final = 10.0  # janela onde se medem a densidade e a coerência de cristas
RELEVO_ZONA_DENSIDADE_MIN: Final = 0.15  # fração de píxeis de crista na janela para ser zona densa
RELEVO_ZONA_AREA_MIN_MM2: Final = 100.0
RELEVO_ZONA_LINHAS_MIN: Final = 3  # nº mínimo de cristas distintas numa zona
RELEVO_RUGAS_COERENCIA_MIN: Final = 0.5  # coerência de orientação ≥ isto → rugas; abaixo → amasso

# =============================================================================
# M6 MANCHAS
# =============================================================================
MANCHAS_ATIVO: Final = True
MANCHAS_CANAL: Final = "cinzento"  # "cinzento" | "b" | "g" | "r" | "h" | "s" | "v"
MANCHAS_FUNDO_SIGMA_MM: Final = 10.0  # desfoque que estima a iluminação; maior do que as manchas
MANCHAS_POLARIDADE: Final = "ambas"  # "ambas" | "clara" | "escura"
MANCHAS_CONTRASTE_MIN: Final = 0.15  # 0,15 = 15 % mais clara/escura do que a vizinhança
MANCHAS_AREA_MIN_MM2: Final = 4.0
MANCHAS_ALONGAMENTO_MAX: Final = 4.0  # acima disto é uma linha (assunto do M5), não uma mancha

# =============================================================================
# M7 SAÍDA
# =============================================================================
SAIDA_GUARDAR_IMAGENS: Final = "nok"  # "nenhuma" | "nok" (todas as que não são OK) | "todas"
SAIDA_FORMATO_IMAGEM: Final = ".jpg"  # ".jpg" | ".png"
SAIDA_SEPARADOR_DECIMAL: Final = ","  # "," para o Excel em português; "." para Python/pandas
# Depuração: para cada saco, uma pasta com a imagem de cada etapa de cada módulo,
# um painel com tudo e o resultado em JSON (resultados/<data_hora>/depuracao/).
# "nenhuma" | "nok" (só os sacos NOK, ERRO ou SEM_SACO) | "todas". Gasta tempo e
# disco: só em ensaios; na linha, "nenhuma". O main.py aceita --depurar.
SAIDA_DEPURAR: Final = "nenhuma"
SAIDA_PAINEL_COLUNAS: Final = 4  # etapas por linha no painel
SAIDA_PAINEL_LARGURA_ETAPA_PX: Final = 480  # largura de cada etapa no painel
