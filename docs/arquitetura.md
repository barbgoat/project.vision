# Arquitetura Mestre–Escravos

O programa está dividido em módulos independentes. Cada um responde a **uma
só pergunta**, tem os **seus próprios parâmetros** e fala com o Mestre através
de um **contrato comum**. Se um módulo falhar, o problema fica isolado nele e
o registo diz qual foi e onde.

```
MESTRE (main.py + mestre.py)
 │
 ├─1─► M1 Aquisição ...... "dá-me as imagens de cada saco"      → Passagem (uma por saco)
 ├─2─► M2 Localização .... "onde está o saco? está completo?"   → Saco
 │                          (se não houver saco: SEM_SACO, para aqui)
 ├─3─► Escravos de inspeção, todos com o MESMO contrato:
 │      ├─ M3 Dimensões ... "tem o tamanho e a forma certos?"   → Relatorio
 │      ├─ M4 Cor ......... "tem a cor certa?"                  → Relatorio
 │      ├─ M5 Relevo ...... "riscos, dobras, rugas, amassos?"   → Relatorio
 │      └─ M6 Manchas ..... "tem manchas?"                      → Relatorio
 ├─4─  Decide: OK só se nenhum módulo der NOK ou ERRO
 └─5─► M7 Saída .......... imagem anotada, registos CSV (mais tarde, PLC)
```

| Módulo | Pasta | Deteta | Técnica |
|---|---|---|---|
| M1 Aquisição | `modulos/m1_aquisicao/` | — | fontes (pasta, vídeo, webcam, Blackfly S); disparo (foto, sensor, barreira virtual); aquisição em paralelo |
| M2 Localização | `modulos/m2_localizacao/` | sem saco, saco incompleto | diferença de cor ao fundo (CIELAB) + arestas; momentos; medição pelos lados; recorte normalizado |
| M3 Dimensões | `modulos/m3_dimensoes/` | `dimensao` | medidas do M2 face ao nominal ± tolerância; silhueta vs retângulo ideal (falta / sobra de material) |
| M4 Cor | `modulos/m4_cor/` | `cor` | cor mediana em CIELAB, diferença ΔE |
| M5 Relevo | `modulos/m5_relevo/` | `risco`, `dobra`, `rugas`, `amasso` | Hessiana multiescala, linhas de Steger, coerência de orientação |
| M6 Manchas | `modulos/m6_manchas/` | `mancha` | contraste relativo + componentes ligadas |
| M7 Saída | `modulos/m7_saida/` | — | `registo.csv`, `defeitos.csv`, imagem anotada, depuração (painel com cada etapa) |

## O contrato

- O **M2** devolve um `Saco`: o recorte normalizado do saco, as máscaras e as medidas.
- Cada **escravo de inspeção** (M3–M6) recebe esse `Saco` e devolve um
  `Relatorio`: estado (OK / NOK / ERRO / POR_IMPLEMENTAR), lista de
  `Defeito`, medidas, tempo e mensagem.
- O **Mestre** junta os relatórios num `Resultado` e decide o estado do saco:
  OK, NOK, ERRO ou SEM_SACO.

Está tudo definido em `nucleo/contrato.py`.

## Como o Mestre isola as falhas

- Cada chamada a um escravo é **protegida**. Se rebentar, fica ERRO no
  relatório desse módulo, com o tipo de erro e o **ficheiro e a linha** onde
  aconteceu (p.ex. `ValueError: ... (em modulos/m5_relevo/cristas.py:57 (mapa_cristas))`),
  e os outros módulos continuam.
- O `Saco` é **só de leitura**. Um escravo que tente alterar a imagem ou as
  medidas recebe um erro no próprio módulo e não estraga a análise dos outros.
- Um escravo que não devolva o seu `Relatorio` fica com ERRO de "contrato não cumprido".
- Um módulo ainda **por implementar** aparece como `POR_IMPLEMENTAR`, com o
  ficheiro e a função em falta, e não conta para a decisão. Assim o sistema
  pode ser desenvolvido um módulo de cada vez.
- Quando um módulo dá ERRO, o saco é **rejeitado** (`GERAL_EM_CASO_DE_ERRO =
  "rejeitar"`): na dúvida, não passa. Com `"ignorar"`, decide-se só com os
  restantes módulos.
- Nunca se aprova um saco que nenhum módulo inspecionou: se todos estiverem
  desligados ou por implementar, o estado é ERRO.

## Regras da arquitetura

1. Os módulos só importam do núcleo (`nucleo/`), **nunca uns dos outros**
   (verificado nos testes: `testes/test_arquitetura.py`).
2. Só o Mestre (`main.py`, `mestre.py`) e as ferramentas importam módulos.
3. Cada módulo só lê a **sua secção** da configuração (e a `geral`).
4. Os escravos **não alteram o Saco** que recebem; se precisarem, fazem uma cópia.
5. Cada defeito tem **um só dono**: as linhas são do M5 e as áreas do M6
   (o M6 ignora regiões alongadas).
6. **Nenhum número "mágico" no código**: tudo o que pode variar está em
   `nucleo/defines.py` (ver [configuracao.md](configuracao.md)).

## Estrutura completa

```
Projeto_Framework/
├─ main.py                  MESTRE: linha de comandos e ciclo principal
├─ mestre.py                MESTRE: chama os escravos, isola as falhas, decide
├─ nucleo/                  partilhado por todos (não é um escravo)
│   ├─ contrato.py          Estado, TipoDefeito, Captura, Passagem, Saco, Defeito, Relatorio, Resultado, Escravo
│   ├─ defines.py           TODOS os valores que podem variar (é aqui que se mudam)
│   ├─ config.py            agrupa os defines por módulo, valida-os, lê/escreve JSON
│   └─ imagens.py           ler/guardar imagens (acentos), para_ver, extrair_canal, contraste_relativo
├─ modulos/                 os escravos, uma pasta cada; o Mestre chama o escravo.py
│   ├─ m1_aquisicao/
│   │   ├─ fontes.py            de onde vêm as imagens (pasta, vídeo, webcam, Blackfly S)
│   │   ├─ disparo.py           quando começa e acaba cada saco (trigger)
│   │   └─ em_paralelo.py       capta o saco seguinte enquanto o anterior é analisado
│   ├─ m2_localizacao/
│   │   ├─ escravo.py           Localizador: liga as etapas
│   │   ├─ fundo.py             1. modelo do fundo e diferença de cor ao fundo
│   │   ├─ segmentacao.py       2. limiar, arestas, limpeza, maior região, contorno
│   │   ├─ medicao.py           3. ângulo, comprimento e largura
│   │   └─ normalizacao.py      4. rodar, recortar, zona útil
│   ├─ m3_dimensoes/
│   │   ├─ escravo.py           comprimento e largura face ao nominal
│   │   └─ forma.py             silhueta vs retângulo ideal: falta e sobra de material
│   ├─ m4_cor/escravo.py
│   ├─ m5_relevo/
│   │   ├─ escravo.py           ponto de entrada (liga as etapas e mede o tempo de cada uma)
│   │   ├─ preprocessamento.py
│   │   ├─ cristas.py
│   │   ├─ zonas.py             rugas, amasso
│   │   └─ linhas.py            risco, dobra
│   ├─ m6_manchas/escravo.py
│   └─ m7_saida/
│       ├─ registo.py           registo.csv, defeitos.csv, imagens anotadas
│       ├─ desenho.py           a imagem anotada: contorno, defeitos numerados, estado dos módulos
│       └─ depuracao.py         pasta por saco com cada etapa, painel.jpg e resultado.json
├─ ferramentas/
│   ├─ testar_modulo.py     correr UM módulo e ver o relatório completo
│   ├─ testar_disparo.py    ver que passagens (sacos) o disparo encontra num vídeo
│   ├─ testar_camara.py     primeiro contacto com a Blackfly S (PySpin)
│   ├─ calcular_montagem.py lente, distância, px/mm, FPS e exposição para esta câmara
│   ├─ afinar.py            barras deslizantes para os parâmetros de um módulo
│   └─ avaliar.py           métricas por saco, por módulo e por defeito
├─ configuracoes/           perfis: telemovel.json, bancada_botao.json, bancada_virtual.json, linha.json
├─ testes/                  testes automáticos (pytest), com imagens sintéticas
├─ docs/                    esta documentação
├─ dataset/                 uma pasta por montagem (telemovel/, blackfly/), uma subpasta por classe
├─ resultados/              saídas do main.py e das ferramentas (não vai para o git)
├─ instaladores/            wheel do PySpin / Spinnaker SDK (não vai para o git)
├─ config.json              (opcional) ajustes desta máquina; não vai para o git
├─ requirements.txt         dependências do programa
├─ requirements-dev.txt     + pytest, para os testes
└─ pyproject.toml           configuração do pytest
```

## Acrescentar um módulo novo (selagem, furos, impressão...)

1. Criar a pasta `modulos/m8_<nome>/` com um `escravo.py` (uma classe que
   deriva de `Escravo`: `nome`, `pergunta`, `analisar`) e um `__init__.py` que
   a exporta. As etapas, cada uma no seu ficheiro, ao lado.
2. Pôr os seus valores em `nucleo/defines.py` (com `<MÓDULO>_ATIVO`), criar a
   sua secção em `nucleo/config.py` a ler esses defines e juntá-la à `Config`.
3. Juntar a classe a `ESCRAVOS` em `mestre.py` e, se tiver tipos novos, a `TipoDefeito`.
4. Para aparecer na depuração, pôr as imagens de cada etapa em
   `Relatorio.intermedios` quando `guardar_intermedios` for `True`
   (ver [depuracao.md](depuracao.md)).

Nenhum dos módulos existentes precisa de ser alterado.
