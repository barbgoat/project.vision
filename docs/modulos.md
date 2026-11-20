# Módulos de visão

## M2 Localização (`modulos/m2_localizacao/`)

A câmara está fixa, a uma distância conhecida: a escala é `geral.px_por_mm`, medido uma vez
com uma régua na imagem.

1. **Fundo**: foto do fundo vazio (`LOCALIZACAO_FUNDO_REFERENCIA`; na BIapp, Afinar câmara → **Fundo
   vazio**) ou superfície ajustada à borda da imagem. **Com a foto, recomendado:** cada píxel compara-se
   com o mesmo píxel do fundo, a luz desigual e os vincos do papel desaparecem, e o limiar sai do ruído
   do fundo (não do Otsu): até um saco claro só ~6 ΔE diferente de um fundo claro é apanhado.
2. **Diferença de cor** ao fundo (ΔE em CIELAB), limiar automático: sacos de qualquer cor.
3. **Arestas**, só se a cor não chegar (sacos transparentes).
4. **Contorno subpíxel**: cada ponto vai para o máximo do gradiente ao longo da normal (do ΔE),
   por isso as medidas não dependem do limiar nem da máscara. **Ângulo e medidas** como um paquímetro
   (mediana de cada lado, sem os cantos).
5. **Recorte** rodado e centrado + **zona útil** (sem as margens), entregue aos outros módulos.

Sem saco, diz porquê: "nada difere do fundo", "toca na borda", "fora dos limites". Um contorno
que não fechou (solidez abaixo de `LOCALIZACAO_SOLIDEZ_MIN`, 0,95) dá ERRO "contorno incompleto"
em vez de medidas erradas.

Em imagens sintéticas: erro ≤ 0,2 mm e ≤ 0,03° (opacos e translúcidos), ~0,9 mm
(transparentes); ~80 ms numa imagem de 5 MP.

**Dicas:** fotografar o fundo vazio em cada sessão; sombra no contorno → baixar
`LOCALIZACAO_PESO_LUMINOSIDADE`; pedaços de fundo → subir `LOCALIZACAO_DELTA_E_MIN`;
sacos "fora dos limites" → verificar `GERAL_PX_POR_MM`. Fundo de inox com reflexos e
sacos com cor na orla → peso da luminosidade 0 e limiar fixo de 8 ΔE: os reflexos são só
luminosidade e o Otsu cortava o saco por dentro (laranja vs bege).

## M3 Dimensões (`modulos/m3_dimensoes/`)

Usa as medidas do M2:

1. **Comprimento** e **largura** face ao nominal ± tolerância (`DIMENSOES_*`, ou do produto
   na base de dados: `CORRESPONDENCIA` em `nucleo/especificacoes.py`). Cada saco fica na base
   com o medido e o pedido; o dashboard mostra, por OF, média, mínimo, máximo e sacos fora.
2. **Forma**: silhueta vs retângulo ideal, com os cantos do produto
   (`DIMENSOES_FORMA_RAIO_CANTO_MM`, e `..._OUTRA_PONTA_MM` se as pontas forem diferentes: o
   saco pode vir virado para qualquer lado, fica a atribuição que encaixa melhor). Cada zona
   com **falta** (canto dobrado, rasgão) ou **sobra** (aba) de material com área ≥
   `DIMENSOES_FORMA_AREA_MIN_MM2` **ou** profundidade ≥ `DIMENSOES_FORMA_PROFUNDIDADE_MIN_MM`
   (entalhes estreitos e fundos) é um defeito, com posição e causa provável.
3. **Geometria**: uma reta robusta a cada lado (sem cantos nem acidentes) → **esquadria** de
   cada ponta e **paralelismo** dos lados compridos, em graus (não dependem da escala). Uma
   ponta acima de `DIMENSOES_ESQUADRIA_MAX_GRAUS` = corte torto (substitui o par falta/sobra
   da forma nessa ponta); lados acima de `DIMENSOES_PARALELISMO_MAX_GRAUS` = trapézio.
4. **Easy open** (`easy_open.py`), só com `dimensoes.easy_open` (vem do produto ou da OF: "Easy
   open" no formulário, EO sim/não): os entalhes de abertura fácil, cortes em V de ~1–2 mm na
   borda. A silhueta do M2 não os guarda (o fecho tapa-os): procuram-se na imagem. Ao longo de cada
   lado (sem os cantos), a borda verdadeira de fora para dentro (o primeiro ponto com 0,5 mm de
   saco seguido, com os níveis do fundo e do saco de cada coluna); um entalhe é um vale estreito
   nessa borda: 1–3,5 mm de fundo, 1–3 mm de boca. As colunas sem saco à vista (uma janela
   transparente, um reflexo) ficam com a borda das vizinhas. Mede-se o lado, a posição no
   referencial do saco e a distância à ponta mais próxima (não depende do sentido do saco):
   `easy_open_entalhes`, `easy_open_distancia_ponta_mm`, `easy_open_profundidade_mm` e, por
   entalhe, `easy_open_<n>_x_mm`, `_y_mm`, `_distancia_ponta_mm`; a mensagem do relatório
   descreve cada um e a imagem "forma" marca-os a laranja (e a linha entre o par do topo). Só se
   procura nos lados do produto (`dimensoes.easy_open_lados`, "Entalhes do easy open" no formulário:
   `compridos`, frente a frente na largura, o normal; `pontas`; `todos`): um falso noutro lado não
   conta. **O topo do saco** é a ponta com mais entalhes (`easy_open_ponta`, −1/+1) e é a referência
   (0,00 mm): com o saco direito (EO em cima, como nas Etapas), `easy_open_esquerdo_mm` e
   `easy_open_direito_mm` são a distância de cada entalhe à aresta real do topo (a reta da geometria, à
   altura do entalhe: um topo cortado torto não entra), `easy_open_assimetria_mm` = esquerdo − direito e
   `easy_open_esquadria_graus` o ângulo da linha entre os dois face ao topo. Ainda só medidas (sem
   tolerância). Com EO e nenhum entalhe: NOK ("easy
   open em falta", e diz os lados em que a borda não se via). Sem EO não se procuram. Fotos reais
   (8 PreVital, 19/11): os dois entalhes em todas, a 17,6–19,2 mm (em cima) e 19,3–21,1 mm (em
   baixo) da ponta, 1,3–2,2 mm de fundo, sem falsos; ~60 ms por saco a 6,9 px/mm. A partir do topo (11 sacos, 19/11): o mesmo saco
   fotografado várias vezes repete a ±0,02 mm; esquerdo 18,0–20,7 mm, direito 18,2–20,9 mm; assimetria
   −0,06 a −0,26 mm na maioria e −1,56 mm numa sessão (esse saco tinha o topo a 1,8°). Ainda sem tolerância de posição
   (só medida) e sem sacos sem EO reais para confirmar os falsos alarmes.

Em sacos sintéticos: sacos certos a ±0,06°; corte torto de 4 mm (4,2°, invisível pela
área) apanhado; entalhe de 5 × 3 mm (7 mm²) apanhado pela profundidade; canto dobrado,
aba, rasgão e dois defeitos no mesmo saco separados; ~15 ms. Entalhes mais estreitos do
que `LOCALIZACAO_FECHO_MM` são tapados pelo M2. Fotos reais (PreVital na folha): pontas a
≤ 1°, lados a ≤ 0,2°, cantos de 4 e 10 mm; sem falsos alarmes de forma. Um saco branco
sobre um fundo branco perde contraste nas zonas sem impressão: subir então a profundidade
mínima (`DIMENSOES_FORMA_PROFUNDIDADE_MIN_MM`, p.ex. 3 mm).

## M5 Relevo (`modulos/m5_relevo/`): rugas

Uma ruga (um vinco de lado a lado, ou vários) é uma linha fina e comprida, mais escura ou mais
clara do que a vizinhança conforme o ângulo da luz. No recorte normalizado do M2 (`linhas.py`):

1. pontos de linha pela Hessiana, em 3 escalas e nas duas polaridades, cada um com a sua direção,
   com o teste de simetria (uma linha tem os dois lados parecidos; a borda de um reflexo não) e o de
   tinta (um fio muito mais escuro do que os lados é impressão); `relevo.limiar` é a força mínima
   (um vinco de 10 % dá ~0,12; o ruído do papel fica abaixo de ~0,08). As **dobras largas e
   suaves** (3–6 mm de largura, 5–15 % de contraste, como num saco mate) não dão linhas finas: a mesma
   Hessiana corre numa imagem reduzida a ~1,75 px/mm (`dobra_px_por_mm`), com sigmas de 0,9–2 mm, e só
   o centro de cada dobra conta (resposta entre `dobra_limiar` 0,04 e `dobra_max` 0,30; acima é o
   contraste da impressão desfocada; longe da tinta);
2. a **tinta do próprio saco** (texto, desenhos: traços finos e escuros), com 1 mm de folga, não é
   ruga. Um traço escuro fino com mais de 25 mm (`tinta_fio_mm`, largura média até 0,8 mm) não é
   texto: é um vinco forte na folha metalizada (escuro como a tinta) ou uma linha impressa, que só
   sai com a referência; a **impressão** que não é tinta (traços claros, contornos) desconta-se com a referência
   (abaixo);
3. retas por ângulo, cada uma só com os pontos que têm a direção dela (±15°): troços de pelo menos
   `relevo.ruga_min_mm` com pelo menos `relevo.prova_min_mm` de traço visível, e com pelo menos
   14 mm (`destaque_mm`) a mais do que as retas paralelas a ±2,5 mm (numa textura, como os reflexos
   da folha metalizada, qualquer reta junta pontos ao acaso, e as do lado também). O que não deixa
   ver uma ruga (queimado, tinta, impressão, zonas muito escuras como um quadrado preto)
   atravessa-se sem contar como prova.

Uma reta paralela a um eixo do saco (±3°) e toda a menos de 3 mm da borda da zona inspecionada
(`borda_mm`) é a selagem ou a aresta de um painel impresso: não é ruga.

**Referência da impressão** (`referencia.py`, `linhas.imprimir`, pasta `relevo.referencia_pasta`): cada saco
OK ensinado é uma **vista** (o recorte do M2 ou, num saco maior do que o campo, a imagem toda). Em cada saco
inspecionado, cada vista alinha-se com ele pelos **pontos de interesse** (SIFT a ~3,5 px/mm, teste da razão,
RANSAC numa semelhança e depois a afim por mínimos quadrados nas correspondências coerentes): em qualquer
posição e rotação, mesmo só com uma parte em comum. Sem pontos que cheguem (um saco quase liso), recortes do
mesmo tamanho (±8 mm) alinham-se pela correlação, direto ou rodados 180°, e o ECC. Uma vista só serve se o
brilho normalizado na parte comum se parecer (correlação ≥ 0,5) e a afim não esticar mais de 10 %: outro
produto ou a outra face não serve. As vistas que servem (até 15, as de mais correspondências) levam as
marcas (tinta, fios e pontos de linha, com 1 mm de folga) e o que veem para o referencial do saco: é
impressão o que está marcado em pelo menos metade das vistas que veem esse sítio. Votar no referencial do
saco deixa usar vistas que só veem uma parte dele. Medidas: `referencia_sacos` (as vistas usadas),
`referencia_alinhamento`, `referencia_cobertura_pct` (a parte do saco que elas veem: a impressão só se
desconta aí; abaixo de 90 % a mensagem diz para ensinar um saco OK nessa posição). Medido em fotos reais
(19/11, imagem toda, 1277 × 955 px a 6,9 px/mm): o mesmo produto alinha com 200–800 correspondências,
correlação 0,88–0,99 e erro p90 ≤ 0,5 mm, também rodado 90–180° e com 14 % em comum; outro produto dá
4–8 correspondências e correlação ≈ 0. Preparar uma vista leva ~1,5 s numa imagem toda: cada uma fica
preparada em `<pasta>/_vistas/<nome>.npz` (refeita se o recorte ou os parâmetros do M5 mudarem), e ao
ensinar só se prepara a nova. Ensinar só sacos sem rugas: o que se repete nas vistas passa a impressão (o
mesmo saco ensinado em várias posições ensina também as rugas dele). Medido (19/11, cada foto com as outras
do mesmo produto ensinadas, imagem toda): Dolmio, 7 fotos em posições e rotações diferentes, rugas falsas
16/13/8/1/0/1/14 → 0/0/0/0/0/0/2 (alinhamento 0,94–0,99; cobertura 57–98 %); Rice, 5 fotos, 4 → 2. No
PreVital (folha metalizada) os sacos OK do dataset alinham entre si (0,94), mas com as fotos de rugas a
correlação fica 0,26–0,48 (os reflexos mudam de foto para foto): abaixo de 0,5 a vista não se usa e fica
como sem referência. Por fazer: aceitar aí o alinhamento pelos pontos coerentes.

**Comparar com o resto do saco** (`linhas.desvio_no_saco`): uma ruga é uma diferença entre zonas do
mesmo saco. Para cada reta, a prova das retas paralelas com o mesmo comprimento, de 1 em 1 mm por todo
o saco (sem as que estão a menos de 4 mm dela e só a parte à vista de cada uma): a ruga tem de ter
`saco_desvio_min` (2) dispersões a mais do que a mediana delas (mediana e MAD: um feixe de dobras
paralelas não sobe a referência; a dispersão conta pelo menos 2 mm). Numa textura muitas paralelas
juntam quase tanta prova. A descrição de cada ruga diz quanto se destaca. Nas fotos de 19/11: os vincos
reais no PreVital dão 1,6–20 (mediana 4,3); as linhas falsas conhecidas também se destacam (a borda do
saco 12, a aresta de um painel 4,4): é uma rede contra texturas, não as resolve.

**Famílias de rugas paralelas** (`linhas._procurar_familias`, `linhas.agrupar_familias`): pela mecânica do saco,
uma ruga costuma ter outras paralelas acima ou abaixo.
- Não se penalizam: dois troços paralelos só são a mesma ruga se o centro de um estiver a menos de 4 mm da reta
  do outro (antes bastava o centro a menos de 8 mm); na comparação com o resto do saco, as paralelas a ±4 mm das
  rugas já detetadas não contam (2.ª passagem); e a prova da ruga mede-se na melhor posição a ±`faixa_mm` (a reta
  sai de colunas alargadas e podia ficar até 0,6 mm ao lado do traço, com prova medida ~0).
- Procura guiada: à volta de cada ruga com desvio ≥ 3 (`familia_semente_desvio`), retas no mesmo ângulo (±3°)
  em todo o saco, com os mesmos pontos (simetria, tinta, impressão da referência) e limites mais baixos, do
  perfil: `relevo.prova_min_mm_familia` (12), `relevo.ruga_min_mm_familia` (25), `relevo.desvio_min_familia`
  (1,5); destaque das vizinhas 8 mm (`familia_destaque_mm`).
- Família ou textura (`linhas.e_textura`): as paralelas à semente (as já achadas e as novas, as que distam
  menos de 5 mm contam como uma) são textura se forem mais de 6, ou 4 ou mais com espaçamentos regulares
  (coeficiente de variação < 0,2: reflexos, estrias da folha metalizada). Aí não se junta nenhuma.
- Resultado: as rugas paralelas (±3°) agrupam-se em famílias; cada ruga diz a família e se foi achada pela
  procura dela; a mensagem diz "família de 3 rugas a +12° (espaçamento 8–15 mm)"; medidas `familias`,
  `familia_rugas_max`, `rugas_familia`; na etapa das rugas, cada família tem a sua cor (sozinha: vermelho).

**Comparar com a zona à volta** (desligada: `saco_local_mm` 0; para ligar, 15): o mesmo, só com as paralelas
a menos de 15 mm; a ruga tem de se destacar também delas (`saco_local_desvio_min`, 2). O saco todo engana
quando metade é liso (num Dolmio, qualquer reta na fotografia da comida se destacava 4–13 vezes do resto do saco).

**Textura** (desligada: `textura_coerencia_max` 0; para ligar, 0,5; `linhas.textura`): numa janela de 6 mm, mais de 8 % de pontos de linha com as direções
misturadas (coerência do dobro do ângulo < 0,5: fotografia, comida, letras claras) é textura. Aí uma ruga
não se distingue: não se procura e atravessa-se sem contar como prova (como a tinta). Um feixe de dobras
paralelas tem uma direção só: não é textura. A medida `textura_pct` e a mensagem dizem quanto do saco
ficou por ver (a partir de 10 %); nas candidatas, a textura fica a magenta escuro. Medido (19/11, 73 fotos com
rugas, 4 OK, 3 Dolmio): com as duas regras, fotos com rugas apanhadas 49 → 42 (a textura tira 6 do
PreVital com reflexos; a zona à volta 2), OK rejeitados 0 → 0, e o Dolmio só desce de 16/13/8 para
10/12/6 falsas: as que ficam são estruturas impressas nítidas (o rebordo da taça, as arestas das letras
grandes e dos painéis), coerentes, não textura. Nenhum limite da textura separa os dois casos. Por isso as
duas ficaram desligadas e a impressão de um saco muito impresso desconta-se com a referência dos sacos OK.

**Zona** (Verificar rugas, botão "Zona"): arrastar um retângulo na imagem e o M5 corre só aí, na mesma
imagem (`janela_rugas.verificar_zona`); numa zona pequena a ruga mínima fica em 60 % do lado maior dela.
Para ver um sítio concreto quando o saco todo dá falsos (impressão) ou o M2 não o localiza.

**Não é ruga** (Verificar rugas, botão "Não é ruga…", só o administrador): escolher, das rugas achadas (pelo
número da imagem), as que não são rugas. As linhas vão para `<referência>/nao_rugas/` (`referencia.guardar_nao_rugas`):
uma entrada por imagem (`<nome>.png` e `_util.png`, o recorte e a zona como num saco OK, e `<nome>.json` com as
pontas de cada linha em px do recorte; o nome sai do recorte, por isso mais linhas na mesma imagem juntam-se).
Cada entrada é uma vista que só leva essas linhas (`linhas.vista_nao_ruga`): alinha-se com cada saco como um
saco OK (pontos de interesse ou correlação, correlação ≥ 0,5), sem votos (basta uma) e sem contar na cobertura
nem em `referencia_sacos`. Onde alinha, cada linha dá uma faixa de ±2 mm, prolongada 10 mm para lá das pontas
(`nao_ruga_folga_mm`, `nao_ruga_prolongar_mm`), e uma reta achada com metade ou mais do comprimento dentro de
uma faixa não conta (`nao_ruga_fracao`). Não se tiram pontos: tirá-los mudava as paralelas, a densidade e o
que se atravessa, e numa foto real aparecia uma reta nova ao lado de uma faixa. Uma ruga verdadeira que cruze
a faixa fica. Serve para o que se repete no mesmo sítio do mesmo produto (uma linha impressa, a aresta de um
painel); um reflexo muda de sítio de saco para saco e aí não serve. Também numa zona escolhida (a entrada é a
zona). Medidas `referencia_nao_rugas` (as entradas que alinharam) e `rugas_nao_ruga` (as retas que não
contaram), só com entradas na referência; nas etapas do M5 as faixas ficam a verde e, nas candidatas, as retas
que não contaram também. Apagar os ficheiros de uma entrada desfaz.

Um Defeito `rugas` por ruga, com uma faixa ao longo dela. ERRO (`SemConfianca`) se mais de
`relevo.queimado_max_pct` do saco estiver saturado. A 6,9 px/mm: preparar ~1,5 s numa imagem toda (1277 ×
955 px); procurar as rugas ~1–1,3 s num PreVital e ~2,6 s num Dolmio muito impresso (as somas acumuladas
da procura de retas fazem-se só nas colunas precisas: antes 3–4 s e 10 s, com os mesmos resultados); com
a referência, mais ~0,1–0,4 s a alinhar as vistas. A procura corre à resolução inteira abaixo de 7 px/mm
(`busca_px_por_mm` 3,5, fator inteiro).

**Desligado na inspeção por omissão** (`RELEVO_ATIVO = False`) até estar validado. Corre a pedido
no **Verificar rugas** da BIapp (F6): só o M2 e o M5 no saco à frente da câmara. Para ver rugas não
é preciso medir o saco: se o M2 não o localizar com o perfil (foto do fundo desatualizada, saco a
tocar na borda, controlos da medição), tenta sem a foto do fundo e sem os controlos; se nem assim, ou se
o saco assim localizado sair pela borda da imagem (mais de 5 % do contorno: saco maior do que o campo, e
o contorno é um palpite que o corta a meio), o M5 vê a imagem toda, menos as bordas. Cada imagem da câmara fica em
`resultados/verificar_rugas/<dia>/`, com o veredito no nome. O administrador ensina: "É OK:
ensinar" põe o recorte na referência (sem `referencia_pasta`, usa `<dataset>/relevo_referencia`, a
guardar no perfil), "Não é ruga…" põe as linhas escolhidas em `<referência>/nao_rugas/` (acima) e "Tem
rugas: guardar" põe a imagem em `<dataset>/rugas/`. Ensinar 3 a 5 sacos
OK de cada produto (sacos diferentes, em posições diferentes). Num saco maior do que o campo ensina-se a
imagem toda: cada saco OK ensinado conta na parte que tem em comum com o saco verificado, e a janela diz
quanto dele está coberto (ensinar sacos OK nas posições que faltam). Uma zona escolhida não se ensina.

Estado (2026-11-17), sem referência ensinada: numa foto real de um PreVital com um vinco na faixa
clara, só o vinco (77 mm, 72 mm visíveis; sem a tinta, a direção e o destaque eram 16 retas, quase
todas nos reflexos e no texto); numa Garnier OK, nenhuma. Em vincos desenhados na Garnier: apanha
os de lado a lado de 10–15 %; não apanha um diagonal que passe quase todo por baixo do texto, um
vinco claro (crista com sombra) nem um de 6 %. Falta medir com mais rugas reais
(`ferramentas/bancada_rugas.py`, que usa o mesmo detetor) e afinar os `[MEDIR]`.

## Depuração

```powershell
python main.py --config <perfil> --depurar nok     # ou: todas
```

Cada saco depurado tem uma pasta em `resultados/<data_hora>/depuracao/` com:
`painel.jpg` (todas as etapas numa imagem: **abrir primeiro**), `00_original.png`
(para repetir o caso), a imagem anotada, cada etapa e `resultado.json` (medidas,
limiares, tracebacks). Ler o painel da esquerda para a direita: a primeira etapa
errada é onde está o problema. Gasta tempo: só em ensaios.

## Notas

- **Luz rasante** realça o relevo: testar posições da luz antes de afinar.
- **Arrasto** (px) ≈ velocidade × exposição × px/mm (500 mm/s × 120 µs × 8 ≈ 0,5 px).
- **Impressão** nos sacos dá falsas rugas no M5 sem a referência dos sacos OK ("É OK: ensinar").
- **Acentos nos caminhos:** usar `nucleo.imagens.ler_imagem`/`guardar_imagem`, não `cv2.imread`/`imwrite`.
- **Referências:** Frangi 1998 (Hessiana), Steger 1998 (linhas), Bigün 1987
  (coerência de orientação), CIE ΔE. Para depois: estéreo fotométrico, câmara
  polarizada, Anomalib (deteção de anomalias treinada só com sacos OK).
