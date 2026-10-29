"""Interface gráfica BIapp (PySide6): ver o que a inspeção está a fazer e afinar, ao vivo ou em produção.

Um ficheiro por assunto:

    janela.py             a janela principal: barra, vistas Operador e Engenharia, revisão
    janela_conta.py         + a conta (👤) e a memória da sessão
    janela_parametros.py    + as definições (⚙), a câmara (📷), o perfil e o dataset
    janelas.py            as janelas à parte: histórico e revisão de um saco
    paineis.py            as peças que mostram: imagem, estado, contadores, tabelas, etapas, log
    editor.py             o editor de parâmetros (um campo por parâmetro da configuração)
    estilo.py             a marca, as cores, o aspeto (ESTILO) e os ícones
    login.py              a janela de entrada e as contas
    ciclo.py              a inspeção numa thread (Ao vivo ou Produção) e a pré-visualização
    registos.py           o que fica escrito: revisões e dataset

Arranca com `python gui.py` (na raiz). Usa o Mestre, o M1 e o M7 tal como o
main.py; não acrescenta nenhuma regra de inspeção.
"""
