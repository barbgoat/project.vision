# Depuração: ver cada etapa para perceber onde está o erro

```powershell
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar nok     # só os sacos NOK, ERRO e SEM_SACO
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar todas   # todos
python -m ferramentas.testar_modulo dimensoes dataset\telemovel --config configuracoes\telemovel.json --guardar resultados\verificacao   # a mesma, para um módulo
```

Para cada saco depurado fica uma pasta em `resultados/<data_hora>/depuracao/`
(e o `registo.csv` diz qual, na coluna `depuracao`):

```
saco_0012_canto_dobrado_NOK/
   painel.jpg                   ← TUDO numa só imagem: abrir este primeiro
   00_original.png              ← a imagem analisada, sem alterações: para repetir o caso
   01_anotada.jpg               ← contorno, defeitos numerados, estado de cada módulo
   02_disparo_candidata_1.jpg   ← (modos sensor/virtual) as candidatas da passagem
   02_localizacao_diferenca.png ← M2: quanto cada píxel difere do fundo (claro = muito)
   03_localizacao_segmentacao.png  ← M2: a máscara do saco
   04_localizacao_recorte.png   ← M2: o saco endireitado, com a zona útil a amarelo
   05_dimensoes_forma.png       ← M3: retângulo ideal (amarelo), falta (vermelho), sobra (magenta)
   ...                          ← o M5 e o M6 juntam-se sozinhos, quando existirem
   resultado.json               ← estados, medidas, limiares usados, defeitos, tempos,
                                  traceback completo dos erros, valores que o JSON de configuração muda
```

O **painel** mostra as etapas lado a lado, por ordem, e o resumo por baixo:
o estado e o tempo de cada módulo, as medidas, os defeitos e, a vermelho, os
erros. Como ler: percorrer as etapas da esquerda para a direita e procurar a
primeira que já está mal. Exemplo: o M3 diz "comprimento fora", mas o saco está
bom; se a **segmentação** apanhou a sombra, o problema é do M2 (contraste,
`LOCALIZACAO_PESO_LUMINOSIDADE`); se a segmentação está certa, é do M3
(nominal, tolerância) ou do `GERAL_PX_POR_MM`.

**Repetir um caso:** copiar o `00_original.png` para uma pasta, mudar o define
suspeito e correr `python main.py --pasta <essa pasta> --config <o mesmo perfil> --depurar todas`.

**Custo:** os módulos só produzem as imagens das etapas quando a depuração está
ligada, e gravá-las gasta tempo e disco (nos testes, ~0,25 s por saco). Por
omissão está desligada (`SAIDA_DEPURAR = "nenhuma"`): usar em ensaios, nunca na
linha. Se a própria depuração falhar, a inspeção continua (o erro fica no log).

**Para quem faz um módulo novo:** basta pôr as imagens de cada etapa em
`Relatorio.intermedios` (quando `guardar_intermedios` for `True`); aparecem
sozinhas na pasta e no painel.
