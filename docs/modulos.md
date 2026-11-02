# Módulos de visão

## M2 Localização (`modulos/m2_localizacao/`)

A câmara está fixa, a uma distância conhecida: a escala é `geral.px_por_mm`, medido uma vez
com uma régua na imagem.

1. **Segmentador** (`localizacao.segmentador`): `delta_e` (abaixo), `aprendido` (as cores e
   texturas das fotos anotadas, completadas pelo GrabCut, CPU; P(saco) dentro menos à volta < `aprendido_score_min` → ERRO), `sam3` (SAM 3 por texto,
   qualquer fundo, GPU; confiança < `sam3_score_min` → ERRO) ou `duplo` (os dois; IoU <
   `duplo_iou_min` → ERRO e a foto para `dataset/revisao/`). No ΔE:
   **Fundo**: foto do fundo vazio (`LOCALIZACAO_FUNDO_REFERENCIA`) ou superfície ajustada à borda da imagem.
2. **Diferença de cor** ao fundo (ΔE em CIELAB), limiar automático: sacos de qualquer cor.
3. **Arestas**, só se a cor não chegar (sacos transparentes).
4. **Contorno subpíxel**: cada ponto vai para o máximo do gradiente ao longo da normal (do ΔE,
   ou da cor em CIELAB com o SAM 3), por isso as medidas não dependem do limiar nem da máscara. **Ângulo e medidas** como um paquímetro
   (mediana de cada lado, sem os cantos).
5. **Recorte** rodado e centrado + **zona útil** (sem as margens), entregue aos outros módulos.

Sem saco, diz porquê: "nada difere do fundo", "toca na borda", "fora dos limites".

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

Em sacos sintéticos: sacos certos a ±0,06°; corte torto de 4 mm (4,2°, invisível pela
área) apanhado; entalhe de 5 × 3 mm (7 mm²) apanhado pela profundidade; canto dobrado,
aba, rasgão e dois defeitos no mesmo saco separados; ~15 ms. Entalhes mais estreitos do
que `LOCALIZACAO_FECHO_MM` são tapados pelo M2. Fotos reais (PreVital na folha): pontas a
≤ 1°, lados a ≤ 0,2°, cantos de 4 e 10 mm; sem falsos alarmes de forma. Um saco branco
sobre um fundo branco perde contraste nas zonas sem impressão: subir então a profundidade
mínima (`DIMENSOES_FORMA_PROFUNDIDADE_MIN_MM`, p.ex. 3 mm).

## M5 Relevo (`modulos/m5_relevo/`, por fazer)

Riscos, dobras, rugas e amassos aparecem, com luz rasante, como cristas:

1. `preprocessamento.py`: remove as variações lentas de iluminação.
2. `cristas.py`: Hessiana multiescala + linhas centrais (Steger).
3. `zonas.py`: muitas cristas juntas → rugas (paralelas) ou amasso (caóticas).
4. `linhas.py`: cristas isoladas → risco (fina) ou dobra (larga).

Defeitos que se tocam ficam separados: linhas partidas nos cruzamentos e
religadas pela direção; troços alinhados religados; rugas e amasso separados
pela coerência de orientação.

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
- **Impressão** nos sacos pode dar falsos riscos (M5) e manchas (M6).
- **Acentos nos caminhos:** usar `nucleo.imagens.ler_imagem`/`guardar_imagem`, não `cv2.imread`/`imwrite`.
- **Referências:** Frangi 1998 (Hessiana), Steger 1998 (linhas), Bigün 1987
  (coerência de orientação), CIE ΔE. Para depois: estéreo fotométrico, câmara
  polarizada, Anomalib (deteção de anomalias treinada só com sacos OK).
