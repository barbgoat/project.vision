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
   (um vinco de 10 % dá ~0,12; o ruído do papel fica abaixo de ~0,08);
2. a **tinta do próprio saco** (texto, desenhos: traços finos e escuros), com 1 mm de folga, não é
   ruga. Um traço escuro fino com mais de 25 mm (`tinta_fio_mm`, largura média até 0,8 mm) não é
   texto: é um vinco forte na folha metalizada (escuro como a tinta) ou uma linha impressa, que só
   sai com a referência; a **impressão** que não é tinta (traços claros, contornos) desconta-se com a referência: as
   marcas que se repetem nos sacos OK do produto, alinhados a este (`referencia.py`, pasta
   `relevo.referencia_pasta`);
3. retas por ângulo, cada uma só com os pontos que têm a direção dela (±15°): troços de pelo menos
   `relevo.ruga_min_mm` com pelo menos `relevo.prova_min_mm` de traço visível, e com pelo menos
   14 mm (`destaque_mm`) a mais do que as retas paralelas a ±2,5 mm (numa textura, como os reflexos
   da folha metalizada, qualquer reta junta pontos ao acaso, e as do lado também). O que não deixa
   ver uma ruga (queimado, tinta, impressão, zonas muito escuras como um quadrado preto)
   atravessa-se sem contar como prova.

Uma reta paralela a um eixo do saco (±3°) e toda a menos de 3 mm da borda da zona inspecionada
(`borda_mm`) é a selagem ou a aresta de um painel impresso: não é ruga.

Um Defeito `rugas` por ruga, com uma faixa ao longo dela. ERRO (`SemConfianca`) se mais de
`relevo.queimado_max_pct` do saco estiver saturado. ~0,7–1 s por saco a 7 px/mm.

**Desligado na inspeção por omissão** (`RELEVO_ATIVO = False`) até estar validado. Corre a pedido
no **Verificar rugas** da BIapp (F6): só o M2 e o M5 no saco à frente da câmara. Para ver rugas não
é preciso medir o saco: se o M2 não o localizar com o perfil (foto do fundo desatualizada, saco a
tocar na borda, controlos da medição), tenta sem a foto do fundo e sem os controlos; se nem assim, ou se
o saco assim localizado sair pela borda da imagem (mais de 5 % do contorno: saco maior do que o campo, e
o contorno é um palpite que o corta a meio), o M5 vê a imagem toda, menos as bordas. Cada imagem da câmara fica em
`resultados/verificar_rugas/<dia>/`, com o veredito no nome. O administrador ensina: "É OK:
ensinar" põe o recorte na referência (sem `referencia_pasta`, usa `<dataset>/relevo_referencia`, a
guardar no perfil) e "Tem rugas: guardar" põe a imagem em `<dataset>/rugas/`. Ensinar 3 a 5 sacos
OK de cada produto (sacos diferentes, em posições diferentes).

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
