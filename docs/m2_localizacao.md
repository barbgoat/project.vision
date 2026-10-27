# M2 Localização: como funciona e como verificar

O saco pode chegar em qualquer posição e com qualquer rotação; o M2 encontra-o,
mede-o e entrega aos outros módulos um recorte com o saco sempre na horizontal.

| Etapa | Ficheiro |
|---|---|
| 1. Modelo do fundo e diferença de cor | `modulos/m2_localizacao/fundo.py` |
| 2–3. Limiar, arestas, limpeza, contorno | `modulos/m2_localizacao/segmentacao.py` |
| 4. Orientação e medidas | `modulos/m2_localizacao/medicao.py` |
| 5. Recorte normalizado e zona útil | `modulos/m2_localizacao/normalizacao.py` |
| liga as etapas | `modulos/m2_localizacao/escravo.py` |

1. **Modelo do fundo**: uma foto do fundo vazio (`LOCALIZACAO_FUNDO_REFERENCIA`)
   ou, sem ela, uma superfície suave ajustada à faixa junto à borda da imagem
   (acompanha a luz rasante, que deixa um lado mais claro).
2. **Diferença de cor ao fundo** (ΔE, em CIELAB): serve para sacos de qualquer
   cor, mais claros, mais escuros ou de outra cor do que o fundo. Limiar
   automático (Otsu), nunca abaixo de `LOCALIZACAO_DELTA_E_MIN`.
3. **Arestas**, só se a cor não chegar (sacos transparentes): o contorno e os
   reflexos fecham o saco e o interior é preenchido.
4. **Orientação** pelos momentos da forma, afinada pelos lados compridos;
   **medidas** como um paquímetro: posição mediana de cada lado, sem os cantos
   (a "escada" de píxeis de um saco rodado não engana a medida).
5. **Recorte normalizado**, rodado e centrado, e **zona útil** (sem
   `margem_borda_mm` junto ao contorno e sem `margem_extremidades_mm` nas pontas).

Quando não encontra saco, o registo diz porquê: "nada difere do fundo", "o
saco toca na borda da imagem (incompleto)" ou "objeto com 41 x 21 mm, fora dos
limites de um saco".

## Resultados em imagens sintéticas

5 a 15 px/mm, luz rasante, ruído (os mesmos casos, a 5 px/mm, em `testes/test_m2_localizacao.py`):

| Caso | Erro nas medidas | Erro no ângulo |
|---|---|---|
| Opacos de 8 cores (branco, preto, vermelho, azul, verde, amarelo, cinzento, rosa) | ≤ 0,13 mm | ≤ 0,03° |
| 12 rotações, de −89° a +90°, e várias posições na imagem | ≤ 0,2 mm | ≤ 0,03° |
| Translúcidos (25–40 %) | ≤ 0,14 mm | ≤ 0,02° |
| Transparente (só contorno e reflexos) | ~0,9 mm | 0,02° |
| Fundo com textura, com foto de referência | 0,1 mm | — |

Tempo: ~80 ms numa imagem de 5 MP (Blackfly) e ~110 ms numa de 12 MP (telemóvel).

## Verificar com as tuas fotos

```powershell
python -m ferramentas.testar_modulo localizacao dataset\telemovel\ok --config configuracoes\telemovel.json --guardar resultados\verificacao_m2
```

Para cada foto, aparecem na consola as medidas, o ângulo e o limiar usado, e
fica na pasta indicada uma pasta de depuração (ver [depuracao.md](depuracao.md))
com o `painel.jpg` e as etapas do M2: a imagem anotada (contorno, centro e eixo
maior), a **diferença ao fundo** (claro = muito; é aqui que se vê se o fundo e o
saco se distinguem bem), a máscara do saco e o recorte com a zona útil.

**Dicas:**
- Tira uma foto do **fundo vazio** em cada sessão (mesma posição e luz) e
  indica-a em `LOCALIZACAO_FUNDO_REFERENCIA`. É o que mais ajuda com sacos
  transparentes e fundos com textura.
- Se o contorno incluir a **sombra** do saco, baixa `LOCALIZACAO_PESO_LUMINOSIDADE`
  (p.ex. 0.5); se apanhar **pedaços do fundo**, sobe `LOCALIZACAO_DELTA_E_MIN`.
- Calibra o `GERAL_PX_POR_MM` com uma régua: se estiver errado, os sacos
  são rejeitados como "fora dos limites".
