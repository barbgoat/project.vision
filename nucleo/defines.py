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
# É também a escala do espaço métrico onde o M2 e os escravos trabalham: uma Captura
# com outra escala conhecida (câmara calibrada) é reamostrada para ela pelo Mestre.
GERAL_PX_POR_MM: Final = 8.0  # [MEDIR]
# Velocidade do tapete: só importa na fase 3 de ensaios (tapete a andar); com o saco
# parado não é usada. Ver as fases de ensaio em modulos/m1_aquisicao/__init__.py.
GERAL_VELOCIDADE_MM_S: Final = 500.0  # [MEDIR] (só com o tapete a andar)
GERAL_EM_CASO_DE_ERRO: Final = "rejeitar"  # se um módulo der ERRO: "rejeitar" o saco | "ignorar" o módulo
# Tempo entre sacos = 1000 / sacos por segundo da linha (2 sacos/s → 500 ms). A inspeção e
# o registo de cada saco têm de caber aqui, em média; senão a fila do M1 enche e passam
# sacos sem inspeção (ERRO). Para ter folga, apontar para ~70 % deste valor.
GERAL_ORCAMENTO_MS: Final = 500.0  # [MEDIR] confirmar o ritmo real da linha
GERAL_PARALELO: Final = False  # True = os escravos de inspeção correm ao mesmo tempo (threads)

# =============================================================================
# CÂMARA: Basler acA1300-30gm (GigE, monocromática, modelo legacy) agora; Blackfly S mais tarde
# =============================================================================
# A câmara é PARTILHADA com outra aplicação: o programa muda só a exposição, o ganho e o
# trigger, guarda os valores originais e repõe-nos ao fechar. Nunca grava nem carrega User Sets.
CAMARA_MODELO: Final = "acA1300-30gm"
CAMARA_SERIAL: Final = "23987581"  # nº de série da câmara (o perfil pode pôr outro); procura-se por ele
CAMARA_IP: Final = ""  # IP da câmara, se não aparecer na procura (p.ex. noutra sub-rede); "" = não usar
CAMARA_LARGURA_PX: Final = 1296
CAMARA_ALTURA_PX: Final = 966
CAMARA_PIXEL_UM: Final = 3.75  # tamanho de cada píxel no sensor (µm)
CAMARA_FPS_MAX: Final = 30.0
CAMARA_FORMATO_PIXEL: Final = "Mono8"  # o formato em que a câmara envia (não é alterado pelo programa)
CAMARA_EXPOSICAO_MIN_US: Final = 16.0  # confirmar na ficha da câmara
CAMARA_EXPOSICAO_MAX_US: Final = 10_000_000.0  # confirmar na ficha da câmara
CAMARA_PACOTE_BYTES: Final = 9000  # GigE: jumbo frames; ativar também na placa de rede do PC ("Jumbo Packet" 9014)

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
AQUISICAO_CALIBRACAO: Final = ""  # ficheiro .npz da calibração da montagem (lente + plano do tapete); "" = nenhuma
AQUISICAO_GANHO_DB: Final = 0.0  # Blackfly: ganho em dB; subir só se faltar luz (aumenta o ruído)
AQUISICAO_GANHO_RAW: Final = -1  # Basler legacy: GainRaw (unidades da câmara); -1 = não alterar
AQUISICAO_TIMEOUT_MS: Final = 1000  # espera máxima por cada imagem; com trigger, sem sinal, volta a esperar

# =============================================================================
# M1 AQUISIÇÃO: DISPARO (trigger) — quando começa e acaba a passagem de cada saco
# =============================================================================
# Uma PASSAGEM é a sequência de imagens de um saco, desde que entra até que sai.
# O Mestre analisa a(s) melhor(es) imagem(ns) de cada passagem e dá UMA decisão
# por saco. Dois modos:
#   "ao_vivo"  a câmara capta em contínuo (DISPARO_FPS); uma barreira virtual na
#              própria imagem deteta a entrada e a saída de cada saco
#   "trigger"  a câmara só capta quando recebe um sinal na entrada DISPARO_TRIGGER_LINHA
#              (botão ou sensor de presença): cada sinal, uma imagem, um saco
# A linha final tem o sensor ("trigger"); enquanto não houver, os perfis usam "ao_vivo".
DISPARO_MODO: Final = "trigger"
# Imagens por segundo no modo "ao_vivo" (no máximo CAMARA_FPS_MAX). Imagens com
# o saco COMPLETO à vista ≈ FPS × (campo de visão − comprimento do saco) / velocidade;
# tem de dar pelo menos 1 (melhor 3 ou mais). Com um campo de 306 mm a 8 px/mm e o
# tapete a 500 mm/s: saco de 200 mm → 24 × (306 − 200) / 500 ≈ 5 imagens.
DISPARO_FPS: Final = 24.0

# --- Modo "trigger" ----------------------------------------------------------------
# Entrada da câmara onde chega o sinal: nas Basler ace, "Line1" é a entrada opto-isolada
# (confirmar no manual do modelo). O botão de agora e o sensor de mais tarde usam os mesmos fios.
DISPARO_TRIGGER_LINHA: Final = "Line1"
DISPARO_TRIGGER_ATIVACAO: Final = "RisingEdge"  # "RisingEdge" (dispara na subida do sinal) | "FallingEdge"
# Um botão mecânico "salta" durante alguns ms ao fechar e cada salto pode disparar uma
# imagem. Imagens a menos do que isto da anterior são ignoradas (ninguém carrega duas
# vezes em 0,3 s de propósito; a 2 sacos/s o sensor dispara de 500 em 500 ms). Medido
# com o relógio da câmara.
DISPARO_ANTI_RESSALTO_MS: Final = 300.0

# --- Modo "ao_vivo": barreira virtual na imagem --------------------------------------
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
M1_OBJETO_AREA_MIN: Final = 0.2  # na barreira virtual, só contam objetos com esta fração da área do maior (o resto é ruído)

# --- Validação das passagens ("ao_vivo") e desempenho --------------------------------
DISPARO_IMAGENS_MIN: Final = 1  # passagens com menos imagens são ignoradas (subir para 2–3 contra disparos falsos)
DISPARO_DURACAO_MAX_MS: Final = 2000.0  # acima disto: sacos encostados ou tapete parado → aviso e fecha
DISPARO_INTERVALO_MAX_MS: Final = 200.0  # sem imagens durante mais do que isto, a passagem fecha (> 1000/FPS)
DISPARO_IMAGENS_MAX: Final = 12  # imagens guardadas por passagem (cada imagem a cores de 5 MP ocupa 15 MB)
DISPARO_CANDIDATAS: Final = 3  # melhores imagens que o Mestre experimenta até encontrar o saco completo
DISPARO_FILA_MAX: Final = 4  # passagens à espera de análise; acima disto a análise não acompanha a linha

# =============================================================================
# M2 LOCALIZAÇÃO (encontrar o saco, medir, endireitar)
# =============================================================================
# Segmentador (o que separa o saco do fundo): "delta_e" (diferença de cor ao fundo, CPU, rápido),
# "aprendido" (as cores e texturas aprendidas com as fotos anotadas em ferramentas/anotar.py:
# fundos variados, CPU), "sam3" (SAM 3 da Meta por texto: qualquer fundo, precisa de GPU e dos
# pesos; ver o README) ou "duplo" (ΔE e SAM 3; se discordarem, ERRO e a foto vai para dataset/revisao/).
LOCALIZACAO_SEGMENTADOR: Final = "delta_e"
LOCALIZACAO_APRENDIDO_MODELO: Final = "dataset/segmentacao/modelo.npz"  # criado e atualizado pelo anotar.py
LOCALIZACAO_APRENDIDO_SCORE_MIN: Final = 0.15  # P(saco) dentro do saco menos à volta (fundo claro ~0,25, fundo preto ~0,5); abaixo: ERRO "incerta"
LOCALIZACAO_SAM3_PROMPT: Final = "plastic bag"  # o que o SAM 3 procura (texto, em inglês)
LOCALIZACAO_SAM3_SCORE_MIN: Final = 0.5  # confiança mínima (0–1); abaixo: ERRO "segmentação incerta"
LOCALIZACAO_SAM3_FECHO_MM: Final = 0.0  # fecho morfológico com o SAM 3 (0 = nenhum: não tapa entalhes)
LOCALIZACAO_DUPLO_IOU_MIN: Final = 0.95  # "duplo": coincidência mínima (IoU) das duas máscaras
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
M2_APRENDIDO_LADO_PX: Final = 800  # o modelo aprendido aprende e trabalha a esta resolução (lado maior)
M2_APRENDIDO_NIVEIS_COR: Final = 16  # níveis por canal de CIELAB no modelo (16³ cores)
M2_APRENDIDO_NIVEIS_TEXTURA: Final = 4  # níveis do desvio-padrão local da luminosidade
M2_APRENDIDO_TEXTURA_JANELA_PX: Final = 7  # janela (px da versão de 800 px) do desvio-padrão que mede a textura
M2_APRENDIDO_COR_SUAVIZACAO_PX: Final = 1.0  # desfoque (px da versão de 800 px) antes de ler a cor
M2_APRENDIDO_TEXTURA_MAX: Final = 24.0  # desvio-padrão (níveis de cinzento) a partir do qual é o último nível
M2_APRENDIDO_PROBABILIDADE_MIN: Final = 0.6  # P(saco) para ser saco: > 0,5 (o ambíguo e o nunca visto ficam de fora)
M2_APRENDIDO_SUAVIZACAO_PX: Final = 2.0  # desfoque do mapa de P(saco) antes do limiar
M2_APRENDIDO_FAIXA_INCERTA: Final = 0.02  # zona junto à aresta (fração da imagem) que o GrabCut decide
M2_APRENDIDO_GRABCUT_LADO_PX: Final = 400  # o GrabCut corre numa versão com este lado maior (é o passo lento)
M2_APRENDIDO_GRABCUT_ITERACOES: Final = 3
M2_REVISAO: Final = "dataset/revisao"  # "duplo": onde ficam as fotos em que os segmentadores discordam
# A aresta subpíxel procura-se a ± isto px DO MAPA do segmentador. Fica em px (não em mm): é a
# precisão da máscara grosseira na sua própria resolução, não uma tolerância do saco.
M2_AFINAR_PROCURA: Final = 3
M2_AFINAR_VIZINHOS: Final = 4  # pontos de cada lado usados para a direção da normal ao contorno
M2_AFINAR_GRADIENTE_MIN: Final = 2.0  # transição mais fraca do que isto (ΔE/px) não move o ponto
M2_SUAVIZACAO_MM: Final = 0.25  # desfoque antes de comparar com o fundo (tira o ruído do sensor)
M2_SUAVIZACAO_MIN_PX: Final = 0.3  # esse desfoque nunca é menor do que isto (px)
M2_GRADIENTE_FUNDO_MIN: Final = 0.5  # piso do "gradiente normal do fundo" (ΔE/px), para fundos muito lisos
M2_RETANGULARIDADE_SO_COR: Final = 0.8  # abaixo disto a região de cor é um pedaço de saco → tentar com arestas
M2_PARTE_CENTRAL_LADOS: Final = 0.7  # fração central de cada lado usada para o medir (sem os cantos)
M2_ANGULO_ITERACOES: Final = 2  # voltas de afinação do ângulo pelos lados compridos
M2_ANGULO_DESVIO_MM: Final = 0.4  # nessa afinação, pontos mais afastados da reta do que isto (abas) não contam
M2_FUNDO_PONTOS_MAX: Final = 5000  # pontos da faixa da borda usados no modelo do fundo
M2_FUNDO_ITERACOES: Final = 3  # voltas do ajuste robusto do modelo do fundo
M2_FUNDO_DESVIOS: Final = 3.0  # num ajuste, descarta pontos a mais de N desvios (sujidade, cantos de saco)
M2_FUNDO_TOLERANCIA_DELTA_E: Final = 1.0  # folga fixa nesse descarte (ΔE)

# =============================================================================
# M3 DIMENSÕES (o saco tem o tamanho e a forma certos?)
# =============================================================================
DIMENSOES_ATIVO: Final = True
DIMENSOES_TIMEOUT_MS: Final = 0.0  # tempo máximo do módulo por saco; acima disto ERRO; 0 = sem limite
DIMENSOES_COMPRIMENTO_NOMINAL_MM: Final = 150.0  # [MEDIR] comprimento do produto
DIMENSOES_LARGURA_NOMINAL_MM: Final = 55.0  # [MEDIR] largura do produto
DIMENSOES_TOLERANCIA_COMPRIMENTO_MM: Final = 3.0  # [MEDIR] aceita nominal ± tolerância
DIMENSOES_TOLERANCIA_LARGURA_MM: Final = 3.0  # [MEDIR]
# Forma: a silhueta do saco é comparada com o retângulo ideal do tamanho medido.
# Zonas onde FALTA material (canto dobrado, corte torto, rasgão na borda) ou SOBRA
# (aba, dobra para fora) são defeitos, cada um com a sua posição.
DIMENSOES_FORMA_RAIO_CANTO_MM: Final = 0.0  # [MEDIR] raio dos cantos do saco bom; 0 = cantos vivos
DIMENSOES_FORMA_RAIO_CANTO_OUTRA_PONTA_MM: Final = -1.0  # [MEDIR] se a outra ponta for diferente; -1 = igual
DIMENSOES_FORMA_FOLGA_MM: Final = 1.5  # desvios junto às arestas até isto são ignorados (soldaduras, píxeis)
DIMENSOES_FORMA_AREA_MIN_MM2: Final = 25.0  # área mínima de uma falta ou sobra para ser defeito
DIMENSOES_FORMA_PROFUNDIDADE_MIN_MM: Final = 2.5  # ...ou profundidade mínima (entalhes estreitos e fundos)
# Geometria: uma reta a cada lado. Ângulos não dependem da escala: apanham cortes tortos pequenos.
DIMENSOES_ESQUADRIA_MAX_GRAUS: Final = 2.0  # [MEDIR] desvio máximo de cada ponta à perpendicular do eixo
DIMENSOES_PARALELISMO_MAX_GRAUS: Final = 2.0  # [MEDIR] ângulo máximo entre os lados compridos

# --- M3: afinação interna ----------------------------------------------------------
M3_GEOMETRIA_PARTE_CENTRAL: Final = 0.5  # fração central de cada lado usada na reta (sem os cantos arredondados)
M3_GEOMETRIA_DESVIO_MM: Final = 0.5  # pontos mais longe da reta do que isto (canto dobrado, rasgão) não contam
M3_GEOMETRIA_ITERACOES: Final = 2  # voltas de reajuste sem os pontos afastados
M3_GEOMETRIA_PONTOS_MIN: Final = 10  # pontos mínimos num lado para lhe ajustar uma reta

# =============================================================================
# M4 COR (o saco tem a cor certa?)
# =============================================================================
COR_ATIVO: Final = True  # desligar com fotos de telemóvel (balanço de brancos automático)
COR_TIMEOUT_MS: Final = 0.0  # tempo máximo do módulo por saco; acima disto ERRO; 0 = sem limite
COR_REFERENCIA_LAB: Final = (70.0, 0.0, 0.0)  # [MEDIR] cor média de sacos OK em CIELAB (L* 0–100, a*, b*)
COR_DELTA_E_MAX: Final = 6.0  # diferença de cor máxima aceite (ΔE)

# =============================================================================
# M5 RELEVO (riscos, dobras, rugas, amassos)
# =============================================================================
RELEVO_ATIVO: Final = True
RELEVO_TIMEOUT_MS: Final = 0.0  # tempo máximo do módulo por saco; acima disto ERRO; 0 = sem limite
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
MANCHAS_TIMEOUT_MS: Final = 0.0  # tempo máximo do módulo por saco; acima disto ERRO; 0 = sem limite
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
# Base de dados dos produtos, das ordens de fabrico (OF) e das inspeções (nucleo/especificacoes.py):
# "" = especificacoes.sqlite na pasta dos resultados; senão o caminho do ficheiro (relativo à raiz).
SAIDA_BASE_DADOS: Final = ""
# Dashboard fora da BIapp (biapp_dashboard.py): um servidor envia as atualizações por SSE.
SAIDA_DASHBOARD_ENDERECO: Final = "127.0.0.1"  # só este PC; "0.0.0.0" = toda a rede (sem login!)
SAIDA_DASHBOARD_PORTA: Final = 8765
SAIDA_DASHBOARD_INTERVALO_MS: Final = 1000  # de quanto em quanto tempo se vê se a base de dados mudou
SAIDA_DASHBOARD_LIMPAR: Final = True  # botão "Limpar dados" no dashboard (apaga a base de dados!): só em testes
# Depuração: para cada saco, uma pasta com a imagem de cada etapa de cada módulo,
# um painel com tudo e o resultado em JSON (resultados/<data_hora>/depuracao/).
# "nenhuma" | "nok" (só os sacos NOK, ERRO ou SEM_SACO) | "todas". Gasta tempo e
# disco: só em ensaios; na linha, "nenhuma". O main.py aceita --depurar.
SAIDA_DEPURAR: Final = "nenhuma"
SAIDA_PAINEL_COLUNAS: Final = 4  # etapas por linha no painel
SAIDA_PAINEL_LARGURA_ETAPA_PX: Final = 480  # largura de cada etapa no painel
