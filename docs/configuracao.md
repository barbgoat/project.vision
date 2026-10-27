# Configuração: os defines e os perfis

## Os defines

**Todos os valores que podem variar estão em `nucleo/defines.py`**: limites,
tolerâncias, distâncias, limiares, margens... Para mudar um valor, muda-o aí.

```python
# nucleo/defines.py
GERAL_PX_POR_MM: Final = 8.0                     # [MEDIR] píxeis por mm na imagem
DIMENSOES_COMPRIMENTO_NOMINAL_MM: Final = 150.0  # [MEDIR] comprimento do produto
DIMENSOES_TOLERANCIA_COMPRIMENTO_MM: Final = 3.0 # [MEDIR] aceita nominal ± tolerância
LOCALIZACAO_MARGEM_BORDA_MM: Final = 3.0         # faixa junto ao contorno que não é inspecionada
```

- `[MEDIR]`: valor provisório que **tem** de ser medido para a montagem ou o
  produto real. Os restantes são pontos de partida, a afinar com imagens reais.
- Os defines estão agrupados por módulo (GERAL, CAMARA, LENTE, AQUISICAO,
  DISPARO, LOCALIZACAO, DIMENSOES, COR, RELEVO, MANCHAS, SAIDA). Os que começam
  por `M2_`, `M3_`... são afinações internas de um módulo; normalmente não é
  preciso mexer neles.
- Comprimentos sempre em **mm** (áreas em mm²); a conversão para píxeis é feita
  com `GERAL_PX_POR_MM`. Nenhum módulo tem números "mágicos" no código: um
  valor que possa variar vai para os defines.
- Valores incoerentes dão erro logo no arranque, com todos os problemas de uma
  vez (p.ex. limiar baixo maior do que o alto, ou o nominal do M3 fora dos
  limites do M2).

Para ver todos os valores em uso, com o define de onde vem cada um:

```powershell
python -m nucleo.config
python -m nucleo.config --config configuracoes\telemovel.json
```

## Perfis JSON: o que muda numa montagem ou produto

Um ficheiro JSON pode sobrepor **alguns** valores aos defines sem mexer no
código, p.ex. o `px_por_mm` do telemóvel, ou as medidas de outro produto. Tem
só o que muda; o resto vem dos defines:

```json
{"geral": {"px_por_mm": 12.4}, "cor": {"ativo": false}}
```

- Os perfis de cada montagem estão em `configuracoes/` (ver o
  `configuracoes/README.md`) e escolhem-se com `--config configuracoes\telemovel.json`.
- `config.json` na raiz é usado automaticamente, se existir (ajustes desta
  máquina; não vai para o git).
- No arranque, o programa diz que valores o JSON está a mudar, p.ex.
  `geral.px_por_mm = 12.4 (define GERAL_PX_POR_MM = 8.0)`. Um valor mudado no
  JSON nunca fica escondido.
- O nome de cada parâmetro é o do define em minúsculas, sem a secção:
  `GERAL_PX_POR_MM` → `"geral": {"px_por_mm": ...}`.
- Gralhas (`relevo.crista_limar_alto`), secções desconhecidas e tipos errados
  dão erro ao carregar. Os testes carregam todos os perfis de `configuracoes/`.
- O `afinar.py` guarda os valores afinados num JSON destes (só os que mudaram).
  Quando estiveres satisfeito, passa-os para os defines (ou para o perfil da
  montagem) e apaga-os do JSON.
