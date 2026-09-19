# NSRecruiter

Cliente de recrutamento para a Telegrams API oficial do NationStates, com um dashboard ao vivo em Rich.

Descobre nações recém-fundadas em tempo real, confirma que cada uma pode mesmo receber um telegrama de recrutamento, e envia, respeitando os limites de ritmo da API.

## Limites do NS

O NationStates disponibiliza a Telegrams API oficial para enviar telegramas de recrutamento por scripts/bots, com limites definidos (1 por 180 segundos, mais um limite geral de 50 pedidos por 30 segundos). Usar essa API, com uma Client Key de um officer de Communications e um telegrama-modelo marcado como "recruitment", é o uso que o NationStates prevê.

O que nunca faz, em nenhum modo:

- Automatizar a página HTML normal de envio de telegramas: as Script Rules proíbem scripts a atuar sobre páginas HTML, mesmo com o envio a parecer iniciado por um clique humano.
- Gerar links pré-preenchidos para essa página: é explicitamente ilegal, mesmo como "modo manual".

Se o ritmo parecer lento, não vale a pena acelerar pela página normal: é o atalho que a plataforma proíbe.

## Pré-requisitos

- Python 3.11 ou mais recente.
- Autoridade de Communications na região, para gerar ou consultar a Client Key na página do Regional Control.
- Um telegrama-modelo enviado para `tag:api`, marcado como recruitment ao compor (é assim que se obtêm o TGID e a Secret Key.)

## Instalação

```
arrancar.cmd --setup           (Windows)
python3 arrancar.py --setup    (Linux/macOS)
```

Na primeira vez prepara o ambiente virtual, instala as dependências, abre o assistente que pede região, nação, contacto e as três chaves (grava tudo em `.env`) e arranca a aplicação. Nas vezes seguintes basta `arrancar.cmd` (ou `python3 arrancar.py`), que aceita os mesmos argumentos da secção Uso.

## Configuração

As chaves ficam em `.env`. Podes preenchê-lo à mão, copiando `.env.example`, ou voltar a correr o assistente com `--setup` a qualquer momento.

## Uso

```
python -m nsrecruiter --setup      # corre o assistente de configuração e depois arranca
python -m nsrecruiter --check      # valida a configuração e sai, sem enviar nada
python -m nsrecruiter --dry-run    # corre tudo a, mas nunca chama a=sendTG
python -m nsrecruiter              # arranca o script com as configurações preenchidas
python -m nsrecruiter --verbose    # o mesmo que o anterior, com mais detalhe no log
```

Sem o ambiente virtual ativado, usa `arrancar.cmd` (ou `python3 arrancar.py`) no lugar de `python -m nsrecruiter`, com os mesmos argumentos.

Atalhos dentro do dashboard:

| Tecla | Ação |
|---|---|
| `p` | Pausa ou retoma o emissor |
| `r` | Força uma verificação imediata de novas nações |
| `l` | Prioriza manualmente uma nação no topo da fila |
| `v` | Revê rejeições por deteção de padrões |
| `w` | Abre o dashboard web num browser |
| `q` | Sai de forma limpa, terminando primeiro o envio em curso |

`Ctrl+C` encerra da mesma forma.

## Dashboard web

Abre em `http://127.0.0.1:8765/` com as mesmas estatísticas em mais detalhe.

## Arquitetura, em resumo

Três componentes independentes, coordenados por uma fila em SQLite:

- **Coletor**: ouve o stream de fundações em tempo real, com fallback para polling.
- **Validador**: confirma `tgcanrecruit`, exclui quem já está na região, e rejeita nomes bloqueados, prováveis alts e prováveis lotes gerados.
- **Emissor**: um envio de cada vez, revalidando imediatamente antes de cada um.

Um alvo demasiado tempo na fila sem ser enviado expira automaticamente: 6 horas por omissão, 8 horas com prioridade por bandeira ou fixação manual. Só corre uma instância de cada vez (lockfile em `data/nsrecruiter.lock`), já que a Client Key é partilhada por toda a região. Tudo o que persiste (base de dados, lockfile, logs) está em `data/`, fora do controlo de versões.

## Licença

MIT. Ver [LICENSE](LICENSE).
