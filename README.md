# KioBot EX

Um soundboard compartilhado em tempo real. Uma pessoa hospeda uma sala no próprio computador, as outras entram com um convite, e qualquer um clica num botão para tocar um som que todo mundo da sala ouve ao mesmo tempo. Pensado para rodar em paralelo a uma call (Discord, por exemplo), sem passar pela call.

Tem também um cronômetro: "toca um som daqui a 20 minutos" ou "toca um som a cada 10 minutos, 5 vezes", para a sala inteira. É a evolução do KioBot original, que era um bot de Discord. Aqui não existe comando de texto: tudo é botão.

Sem conta, sem senha, sem login. A única informação que circula é um apelido opcional.

---

## Instalação

1. Baixe o `KioBotEX-windows.zip` na página de [Releases](../../releases) deste repositório e extraia.
2. Abra o `KioBotEX.exe`.

O Windows costuma mostrar o alerta "aplicativo desconhecido" em programas sem assinatura digital (clique em "Mais informações" e depois em "Executar assim mesmo"), e alguns antivírus dão falso positivo em executáveis feitos com PyInstaller. O código está todo aqui para ser conferido, e o executável é gerado automaticamente pelo GitHub a partir dele (veja `.github/workflows/build.yml`).

**Rodar pelo código-fonte:** instale o Python 3.11 (marque "Add Python to PATH") e dê dois cliques em `Iniciar KioBot EX.bat`. Na primeira vez ele instala as dependências sozinho. Usamos o 3.11 de propósito: foi a versão em que o bot original finalmente funcionou com áudio, depois de muita dor de cabeça com versões mais novas.

---

## Como usar

**Hospedar.** Escolha a pasta onde estão seus sons (mp3, wav ou ogg), escolha quem vai entrar e clique em "Criar sala". Clique em "Copiar convite" e mande para as pessoas.

**Entrar.** Cole o convite em "Entrar em uma sala". Os sons do host são baixados sozinhos e os botões aparecem.

**Tocar.** Clique em qualquer botão. O som toca no computador de todo mundo da sala. Cada pessoa tem o seu controle de volume, que só afeta o que ela mesma ouve.

**Editar (só o host).** Em "Editar soundboard": adicionar sons, renomear cada botão (sem mexer no nome do arquivo), trocar a cor, colocar uma imagem, mudar a ordem e remover botões. Também dá para arrastar arquivos de áudio direto para a janela. As mudanças aparecem na hora para todos.

**Cronômetro.** Dois modos. "Uma vez" toca um som depois de um tempo (de 1 segundo a 24 horas). "Repetir" toca a cada N minutos (1 a 60), de 1 a 10 vezes. Dá para escolher um som específico ou deixar aleatório. Cada sala aceita até 5 cronômetros ao mesmo tempo. Qualquer um cria e cancela os seus; o host cancela todos.

---

## A conexão pela internet é automática

O host só escolhe "Pessoas em qualquer lugar (pela internet)" e clica em "Criar sala". O programa faz o resto:

1. Sobe um relé minúsculo dentro dele mesmo, que só aceita conexões do próprio computador.
2. Abre um túnel gratuito da Cloudflare até esse relé e recebe um endereço público.
3. Coloca esse endereço dentro do convite. Quem entra só cola o convite.

Na primeira vez, o programa pede permissão para baixar o `cloudflared`, o programa oficial da Cloudflare (cerca de 55 MB no Windows). A versão é fixa e o arquivo é conferido por SHA-256 antes de ser usado, a cada abertura. Nenhuma conta é necessária.

Os limites, sem enfeite:

- **Sem garantia.** Túneis sem conta da Cloudflare não têm garantia de funcionamento e seguem os termos de serviço dela. É ótimo para amigos jogando junto; não é infraestrutura de produção.
- **O computador do host precisa ficar ligado e com o programa aberto.** A sala é a janela dele. Se ele fechar, a sala acaba.
- **O endereço muda a cada sala.** Mande o convite de novo sempre que criar uma sala nova.
- **Redes que bloqueiam.** Alguns Wi-Fi de empresa ou escola bloqueiam esse tipo de túnel. Nesse caso, use "mesma rede" ou um relé próprio.

**Mesma rede.** Se todo mundo está na mesma casa ou Wi-Fi, escolha "Pessoas na mesma rede". Não baixa nada e é mais rápido. O convite vira algo como `192.168.0.10:8765/ABCD2345`. Na primeira vez o Windows pergunta se o programa pode usar a rede: permita em redes privadas.

**Relé próprio (avançado).** Se você quer um endereço fixo, hospede o `relay.py` onde quiser e informe o endereço em Configurações (o botão "Testar conexão" confere se está tudo certo). Para hospedar: `pip install -r relay_requirements.txt` e `python relay.py`, com a variável `TRUST_PROXY=1` quando houver proxy na frente. O endereço precisa ser `wss://`. Hospedagens gratuitas costumam "dormir" quando ficam sem uso e levam cerca de um minuto para acordar; o programa espera e avisa na tela, mas as salas ficam na memória do relé e somem se ele dormir de verdade.

---

## Como funciona por dentro

A decisão que mais simplifica tudo: **o áudio não é transmitido.** Quando alguém entra, o programa do host envia os arquivos de som uma única vez (em pedaços, com verificação de integridade) e o convidado guarda numa pasta de cache. Depois disso, um clique só envia uma mensagem de poucos bytes ("tocar o som X"), e cada computador toca o próprio arquivo.

Consequências boas: quase não gasta internet, o volume individual sai de graça, e o cronômetro funciona do mesmo jeito (o host guarda os timers e, quando um acaba, manda tocar para todos). A sincronia não é perfeita ao milissegundo, mas para um soundboard ninguém percebe.

O host é a autoridade. Convidados só pedem coisas ("quero tocar", "quero criar um cronômetro") e o host valida e executa. O relé não interpreta nada além do necessário para entregar as mensagens, e todas as conexões são de saída, então ninguém precisa mexer em roteador.

---

## Segurança

Publicar o código é seguro; o que importa é o desenho. O que o programa faz:

- **Arquivos recebidos do host:** o convidado só aceita áudio (mp3, wav, ogg), identificado pelo conteúdo e não pelo nome, com limite de 5 MB por som e 100 sons por sala. Cada arquivo é conferido contra um hash antes de ser guardado, e o cache usa o próprio hash como nome, o que elimina ataques de caminho (`../`).
- **Imagens:** só PNG e JPG, até 1 MB, validadas abrindo a imagem de verdade. O host reduz e reconverte para PNG antes de enviar.
- **Tudo que chega pela rede é tratado como não confiável:** nomes, cores e números são limpos e limitados antes de aparecer na tela.
- **O túnel expõe só o relé**, que escuta apenas em `127.0.0.1`. O resto do computador do host não fica acessível.
- **Download do `cloudflared`:** versão fixa, só de `github.com/cloudflare/cloudflared`, com SHA-256 conferido no download e a cada abertura. O programa também só encerra processos `cloudflared`, e só o que ele mesmo abriu, caso tenha sido fechado à força.
- **Limites:** o host recusa cliques em excesso, o relé limita mensagens por conexão e o tamanho do que um convidado pode mandar, e bloqueia temporariamente quem erra muitos códigos. Máximo de 20 convidados por sala. Atrás do túnel, o IP de cada pessoa vem do cabeçalho `CF-Connecting-IP`, que a Cloudflare define e o cliente não consegue forjar.
- **Código da sala:** 8 caracteres de um alfabeto de 31 símbolos, cerca de 850 bilhões de combinações. É a única proteção da sala: trate como segredo.
- **Expulsar:** o host remove alguém com um clique, e o relé impede essa pessoa de voltar para aquela sala.
- **Nenhum dado pessoal:** não há conta, e o apelido é opcional.
- **Direitos autorais:** o host envia cópias dos sons para os convidados. Entre amigos é o uso normal de um soundboard, mas não coloque sons protegidos dentro deste repositório.

---

## Estrutura

```
KioBotEX/
├── main.py                  ponto de entrada
├── ui.py                    telas (inicial e sala) e janela principal
├── widgets.py               tema azul/roxo e componentes visuais
├── net.py                   sessões de host e convidado, cache, validações
├── relay.py                 relé (repassa mensagens entre host e convidados)
├── tunnel.py                túnel automático (baixa e controla o cloudflared)
├── store.py                 configurações e soundboard da pasta do host
├── audio.py                 reprodução local e volume
├── protocol.py              constantes, limites e validações compartilhadas
├── assets/                  ícone
├── tests/                   testes automáticos
├── .github/workflows/       gera o .exe e publica a release
├── Iniciar KioBot EX.bat    abre pelo código-fonte no Windows
└── Gerar executavel.bat     gera o .exe localmente
```

O soundboard de cada host fica salvo na própria pasta de sons, em `kiobot_ex_board.json` (nomes, cores, ordem) e na subpasta oculta `.imagens`.

---

## Testes

```
python tests/test_flow.py      rede: relé, host, convidados, arquivos, cronômetros, expulsão, reconexão
python tests/test_tunnel.py    túnel: log do cloudflared, ciclo de vida, download com hash, IP real
python tests/test_ui.py        interface sem tela: duas janelas, uma hospeda e outra entra (precisa do PySide6)
```

Cobrem, entre outras coisas: transferência de arquivos com conferência de integridade, spam de cliques, cronômetros inválidos e acima do limite, convidado tentando cancelar o cronômetro de outro, mensagens gigantes, manifesto malicioso, arquivo que não é áudio, download adulterado, `cloudflared` que falha ou trava, queda e volta do host e expulsão sem retorno.

**O que foi validado em uso real:** uma sala pela internet (relé e túnel da Cloudflare), duas janelas no mesmo computador, com som saindo do alto-falante, volume individual, cronômetros, queda e volta da conexão e expulsão.

**O que ainda não foi validado:** uso com outro computador (firewall, instalação e placa de som de outra pessoa), sessões de várias horas seguidas, e o fluxo automático do túnel com o `cloudflared` real (os testes usam um programa de mentira no lugar; o túnel manual com o programa real foi validado).

---

## Publicar uma versão

O GitHub gera o executável sozinho. Depois de subir o código, crie uma etiqueta de versão:

```
git tag v0.2.0
git push origin v0.2.0
```

A aba "Actions" roda os testes, gera o `KioBotEX.exe` num Windows de verdade e publica o `KioBotEX-windows.zip` na página de Releases.

---

## Ideias para depois

- Arrastar e soltar para reordenar os botões (hoje a ordem muda pelos botões "Antes" e "Depois").
- Atalhos de teclado para os botões.
- Se o host perder a conexão por mais de cerca de 80 segundos, a sala precisa ser recriada.
