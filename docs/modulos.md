# Módulos de visão

## M2 Localização (`modulos/m2_localizacao/`)

1. **Fundo**: foto do fundo vazio (`LOCALIZACAO_FUNDO_REFERENCIA`) ou superfície ajustada à borda da imagem.
2. **Diferença de cor** ao fundo (ΔE em CIELAB), limiar automático: sacos de qualquer cor.
3. **Arestas**, só se a cor não chegar (sacos transparentes).
4. **Ângulo e medidas** como um paquímetro (mediana de cada lado, sem os cantos).
5. **Recorte** rodado e centrado + **zona útil** (sem as margens), entregue aos outros módulos.

Sem saco, diz porquê: "nada difere do fundo", "toca na borda", "fora dos limites".

Em imagens sintéticas: erro ≤ 0,2 mm e ≤ 0,03° (opacos e translúcidos), ~0,9 mm
(transparentes); ~80 ms numa imagem de 5 MP.

**Dicas:** fotografar o fundo vazio em cada sessão; sombra no contorno → baixar
`LOCALIZACAO_PESO_LUMINOSIDADE`; pedaços de fundo → subir `LOCALIZACAO_DELTA_E_MIN`;
sacos "fora dos limites" → verificar `GERAL_PX_POR_MM`.

## M3 Dimensões (`modulos/m3_dimensoes/`)

Usa as medidas do M2:

1. **Comprimento** e **largura** face ao nominal ± tolerância (`DIMENSOES_*`).
2. **Forma**: silhueta vs retângulo ideal. Cada zona com **falta** (canto dobrado,
   rasgão) ou **sobra** (aba) de material acima de `DIMENSOES_FORMA_AREA_MIN_MM2`
   é um defeito, com posição e causa provável; falta + sobra na mesma ponta = corte torto.

Em sacos sintéticos: 8 sacos certos sem falsos alarmes; canto dobrado, aba,
rasgão, corte torto e dois defeitos no mesmo saco detetados e separados; ~5 ms.

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
python main.py --pasta <pasta> --config <perfil> --depurar nok     # ou: todas
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
