## Obter as chaves

O NSRecruiter precisa de três valores do NationStates: a Client Key, o TGID e a Secret Key.

**Client Key**

1. Um officer da região com autoridade de Communications abre a página de Regional Control.
2. Aí gera (ou consulta) a Client Key da região.

Só há uma chave ativa por região e é partilhada por todos os scripts dessa região, por isso o limite de pedidos também é partilhado. Se já existir uma, pede-a a quem a gerou e consulta com o resto do governo que outros scripts estão a correr com essa chave.

**TGID e Secret Key**

1. Escreve o telegrama-modelo que as novas nações vão receber
2. Ao compor, marca-o como "recruitment". (Obrigatoriamente).
3. Envia-o para `tag:api`.
4. Vai à secção de telegramas enviados, abre esse telegrama, clica em `Recruitment & Delivery Reports` e vai estar aí o TGID e a Secret Key.

**Onde as usar**

Corre `--setup` e cola os valores quando o assistente os pedir, ou preenche `.env` à mão: `NS_CLIENT_KEY`, `NS_TELEGRAM_ID` (o TGID) e `NS_SECRET_KEY`. Preenche também `NS_REGION` (o nome da região) e `NS_NATION` (a nação do officer, que entra no User-Agent).

A Secret Key permite a quem a tiver enviar o teu telegrama a quem quiser: nunca a partilhes, nem a colocues em issues ou chats públicos, nem a commites.
