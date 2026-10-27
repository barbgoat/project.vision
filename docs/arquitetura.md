# Arquitetura Mestre–Escravos

Cada módulo (escravo) responde a **uma pergunta**, tem os **seus parâmetros** e
fala com o Mestre por um **contrato comum** (`nucleo/contrato.py`). Se um módulo
falhar, o problema fica nele e o registo diz qual foi e onde.

| Módulo | Pergunta | Devolve |
|---|---|---|
| M1 Aquisição | quais são as imagens deste saco? | `Passagem` |
| M2 Localização | onde está o saco? está completo? | `Saco` (recorte endireitado + medidas) |
| M3 Dimensões | tamanho e forma certos? | `Relatorio` |
| M4 Cor | cor certa? | `Relatorio` |
| M5 Relevo | riscos, dobras, rugas, amassos? | `Relatorio` |
| M6 Manchas | manchas? | `Relatorio` |
| M7 Saída | — | CSV, imagens, depuração |

Cada `Relatorio` tem o estado (OK / NOK / ERRO / POR_IMPLEMENTAR), os defeitos
(tipo, posição e tamanho em mm), as medidas e o tempo. O Mestre decide o saco:
OK, NOK, ERRO ou SEM_SACO.

## Falhas

- Um módulo que rebenta fica em ERRO, com o **ficheiro e a linha**; os outros continuam.
- Um saco com ERRO é rejeitado (`GERAL_EM_CASO_DE_ERRO = "rejeitar"`).
- O `Saco` e a configuração são só de leitura: um módulo não estraga o trabalho dos outros.
- `POR_IMPLEMENTAR` não conta para a decisão, mas um saco que **nenhum** módulo inspecionou nunca é aprovado.

## Regras

1. Os módulos só importam do `nucleo/`, nunca uns dos outros (verificado nos testes).
2. Cada módulo é uma pasta; o Mestre chama o `escravo.py` e cada etapa tem o seu ficheiro.
3. **Nenhum número no código**: tudo o que pode variar está em `nucleo/defines.py`, em mm.
4. Cada defeito tem um só dono (linhas: M5; áreas: M6).
5. Os testes não dependem dos valores `[MEDIR]`: usam `config_teste()` (`testes/apoio.py`),
   que os fixa em `MONTAGEM`. Um `[MEDIR]` novo nos defines entra também em `MONTAGEM`.

## Configuração

- **`nucleo/defines.py`**: todos os valores, agrupados por módulo. `[MEDIR]` = tem
  de ser medido na montagem real. Os `M2_…`, `M3_…` são afinações internas.
- **Perfis** em `configuracoes/` (`--config`): só o que muda numa montagem.
  O nome é o do define em minúsculas, sem a secção:
  `GERAL_PX_POR_MM` → `{"geral": {"px_por_mm": 12.4}}`.

| Perfil | Para |
|---|---|
| `telemovel` | fotos do telemóvel (cor desligada: o telemóvel ajusta a cor sozinho) |
| `bancada_botao` | câmara, saco parado, botão na Line0 |
| `bancada_virtual` | câmara, saco parado, sem botão |
| `linha` | tapete a andar, ainda sem sensor |

- Um `config.json` na raiz é usado automaticamente (ajustes desta máquina; fora do git).
- Gralhas e valores incoerentes dão erro ao arrancar; o que o perfil muda aparece no log.
- `python -m nucleo.config --config <perfil>` mostra todos os valores em uso.

## Acrescentar um módulo

1. `modulos/m8_<nome>/escravo.py`: classe que deriva de `Escravo` (`nome`, `pergunta`, `analisar`).
2. Valores em `defines.py` e a secção respetiva em `nucleo/config.py`.
3. Juntar a classe a `ESCRAVOS` em `mestre.py`.
4. Imagens das etapas em `Relatorio.intermedios` → aparecem sozinhas na depuração.
