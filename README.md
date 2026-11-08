# Quiet Island

Primeiro protótipo de extensão para **GNOME Shell 50**, baseado nas escolhas da [fase de pesquisa](research/escopo.html).

Veja as [capturas reais e a validação do protótipo](docs/prototipo.html).

O relógio fica no centro do monitor principal, em 24 horas e sem data. O visualizador de áudio fica à esquerda, sem título/artista na barra; ícone colorido da IA, ação atual e contador de sessões ficam à direita. Cada componente abre seu próprio menu. Sem player tocando/pausado ou sem sessões de IA conectadas, o respectivo módulo desaparece; em repouso fica só o relógio.

## O que está implementado

- Relógio centralizado com espaços simétricos, mesmo quando os módulos laterais têm larguras diferentes.
- Menu do relógio conforme o formulário: data completa, mês e notificações em uma coluna compacta. A grade de datas e os cartões de notificação usam widgets reais do GNOME; o painel original é restaurado ao desativar.
- Menu de data com largura constante e altura conforme o conteúdo, até aproximadamente 570 px (menor em telas baixas). As notificações passam a rolar no limite; vazio, o menu não reserva espaço para cartões.
- Descoberta MPRIS por D-Bus, título, artista, capa de 68 px, reproduzir/pausar, anterior/próxima e progresso.
- Menu de música com capa quadrada de 68 px, recorte central preservando proporções e raio de 12 px. Botões de 32 px abaixo do artista, progresso em toda a largura e tempo abaixo. O visualizador na barra superior tem 68 px, com nove barras arredondadas de 4 px. Os dados completos do player são atualizados ao abrir o menu e a cada cinco segundos para recuperar eventos perdidos; títulos/estados incorretos publicados pelo próprio navegador continuam sendo uma limitação do MPRIS dele.
- Capas HTTP e arquivos locais; para Chrome/Chromium Flatpak, o serviço lê a capa PNG temporária da instância que publicou o MPRIS. Funciona pela toolbox com `flatpak-spawn --host flatpak enter`, sem alterar permissões do navegador. A leitura é limitada a 2 MB e à capa anunciada pelo player; sem serviço ou imagem disponível, mantém o ícone padrão.
- Busca na faixa somente com duração, identidade de faixa e capacidade de busca válidas.
- Seleção automática de player: mantém o atual enquanto toca; caso contrário, prefere o player que começou a tocar mais recentemente. Não há seletor manual.
- Visualizador com nove bandas reais usando **CAVA**, fora do processo do Shell. Captura o monitor da saída padrão, não o microfone. Sem sinal, sem CAVA ou sem serviço, as barras permanecem em repouso. O ícone nativo aparece apenas quando o visualizador é desativado nas preferências.
- Serviço local de eventos por D-Bus, sem servidor HTTP ou acesso a APIs pagas.
- Adaptadores de hooks para Codex, Claude Code e Hermes. O instalador principal só gera snippets; a conexão das ferramentas é uma etapa explícita com `scripts/codex_hooks.py` e `scripts/claude_hooks.py`, preservando hooks existentes e criando backup quando necessário.
- Lista de sessões com estado, projeto opcional e duração da tarefa. O contador inclui sessões ociosas conectadas; não é um contador de tarefas executando.
- Duração medida do envio do prompt até a conclusão ou interrupção, atualizada a cada segundo sem reconstruir as linhas. Mudanças de estado não reiniciam o contador; a conclusão congela o total e o próximo prompt inicia outra contagem. Tarefas cujo início não foi observado mostram “—”. IDs continuam internos e não aparecem na lista.
- Duas barras acima das sessões mostram a cota restante de 5 horas de Codex e Claude. Anéis em cada linha mostram o contexto disponível daquela sessão. Codex consulta `account/rateLimits/read` a cada 60 s e os metadados `thread/read` a cada 5 s; lê até 2 MB finais do rollout indicado, interpretando apenas registros `event_msg/token_count` com a última chamada e a janela do modelo (nunca o total acumulado). Claude envia `context_window.remaining_percentage` e o ID da sessão pela status line já configurada. Valores ausentes mostram “—”; dados recebidos há mais de 3 minutos são descartados. O anel é a fração da janela informada pelo CLI, não uma previsão do momento da compactação. O clique continua focando a janela do terminal.
- Ícones das IAs conectadas empilhados na barra, um por ferramenta: Codex na frente, Claude atrás e Hermes no fundo. As marcas mantêm as cores; o estado exibido corresponde à ferramenta da frente e o contador continua incluindo todas as sessões.
- Clique na atividade com terminal identificado para trazer a janela existente do Ptyxis à frente. A identidade é capturada no host antes de entrar na toolbox, com verificação de PID e início do processo. Para o servidor compartilhado do Codex, são consultados metadados da sessão sem turnos; a associação exige origem `codex-tui` e um único terminal candidato. Terminais fechados e múltiplas janelas ambíguas geram aviso, sem abrir cópias da sessão.
- Avisos de conclusão pelo sistema de notificações do GNOME, respeitando Não Perturbe. Não avisa ao apenas reconectar ou expirar um estado.
- Preferências para música, capa, progresso, visualizador, IA, projeto e aviso de conclusão.
- Desativação restaura o ator e o conteúdo originais do relógio.

Os recursos marcados como “Depois” continuam no backlog: selecionar a aba exata do terminal, custos/tokens, regras avançadas de alerta, agrupamento por projeto, privacidade em apresentação e fusos. O HTML de pesquisa contém simulações desses recursos; ele não representa implementação concluída.

## Desenvolvimento na toolbox Code

A toolbox `Code` foi criada usando Fedora 44. As ferramentas do protótipo foram instaladas nela. Os comandos abaixo partem deste diretório.

```sh
toolbox run -c Code python3 scripts/build.py
toolbox run -c Code node --test tests/model.test.js
toolbox run -c Code python3 -m pytest tests/test_model.py -q
toolbox run -c Code env QUIET_ISLAND_PRIVATE_BUS=1 dbus-run-session -- python3 -m pytest tests/test_dbus.py -q
toolbox run -c Code env QUIET_ISLAND_PRIVATE_BUS=1 dbus-run-session -- python3 tests/run_mpris.py
toolbox run -c Code python3 scripts/nested.py --smoke
toolbox run -c Code python3 scripts/nested.py --smoke --with-dash-to-panel
```

O último comando inicia um GNOME 50 **headless isolado**, com perfil, configurações e barramento D-Bus próprios. Verifica carregamento, relógio centralizado, menus exclusivos, integração MPRIS, três sessões de IA e desativação/reativação. O resultado e uma captura real ficam em `.test-session/`. O player e os eventos de IA usados nesse teste são fixtures, não sessões pessoais.

Para abrir uma janela interativa de GNOME Devkit com a extensão, sem alterar o desktop atual:

```sh
toolbox run -c Code python3 scripts/nested.py
```

A prévia interativa precisa do pacote `mutter-devkit` na toolbox `Code`. Para mostrar um player de exemplo sem tocar áudio, acrescente `--silent-demo`.

Para testar música, visualizador e notificações na janela:

```sh
toolbox run -c Code python3 scripts/nested.py --with-dash-to-panel --demo
```

A opção `--demo` toca uma faixa sintética original de 16 segundos em loop, publica um player MPRIS com controles reais e cria uma notificação de exemplo no barramento isolado. Ao fechar a janela, encerra o áudio e os processos de teste.

Sem `--smoke`, não são habilitados Eval ou instrumentação de testes. O ZIP distribuído também não contém essa instrumentação.

## Instalação quando quiser usar na sessão

O desenvolvimento e os testes não instalam ou ativam a extensão no desktop atual.

```sh
toolbox run -c Code python3 scripts/install.py
```

Esse comando copia a extensão, o serviço e os snippets; **não ativa nada** e não instala hooks nas ferramentas. Saia da sessão e entre novamente para o GNOME descobrir a nova extensão. Em seguida:

```sh
toolbox run -c Code systemctl --user daemon-reload
toolbox run -c Code systemctl --user enable --now quiet-island.service
toolbox run -c Code gnome-extensions enable quiet-island@local
toolbox run -c Code gnome-extensions prefs quiet-island@local
```

O serviço roda dentro da toolbox `Code`. Seu launcher fica em `~/.local/bin/quiet-island`. Os snippets ficam em `~/.local/share/quiet-island/hooks/`.

Para parar o protótipo e voltar ao relógio original:

```sh
toolbox run -c Code gnome-extensions disable quiet-island@local
toolbox run -c Code systemctl --user disable --now quiet-island.service
```

## Abrir o terminal da atividade

Na versão 0.2.0, a linha de uma atividade com terminal identificado vira um botão com indicador de janela. O clique fecha o menu e foca a janela já existente, inclusive em outro workspace ou minimizada. O primeiro suporte é para Ptyxis. O Ptyxis 50.1 não exporta o UUID da aba ao processo filho, por isso o foco é da janela; selecionar uma aba específica ainda está pendente. Com múltiplas janelas do mesmo Ptyxis, o foco é recusado para não escolher a janela errada. Sem vínculo identificado, a linha permanece informativa.

O clique foi validado com um Ptyxis real dentro do GNOME isolado. Os testes também rejeitaram identidade de processo alterada e duas janelas ambíguas. A associação da sessão real do Codex ao Ptyxis do host foi verificada separadamente. Os hooks existentes não mudaram de definição. Para carregar o novo botão no desktop principal, saia e entre na sessão GNOME.

## Conectar as ferramentas de IA

Para conectar o Codex instalado:

```sh
toolbox run -c Code python3 scripts/codex_hooks.py
```

O comando instala nove observadores em `~/.codex/hooks.json`, sem duplicar entradas se for repetido. O Codex precisa revisar e confiar nessas definições em `/hooks` (CLI) ou Configurações → Hooks (app). Nenhum registro de confiança é alterado pelo instalador. Abra ou retome uma conversa após a revisão para validar os eventos reais. Não é necessário sair da sessão GNOME para conectar os hooks.

Foram verificados no Codex 0.166.0: reconhecimento dos nove eventos, nenhum erro/aviso de configuração e, após revisão pelo usuário, todos os nove confiáveis e habilitados. Uma sessão real já reportou execução ao serviço com projeto e contador corretos. O teste D-Bus privado passa pelo launcher instalado e cobre início, pensamento, leitura, edição, execução, espera, interrupção, conclusão e remoção da sessão, sem guardar conteúdo dos payloads. O teste automatizado injeta eventos; a conexão da sessão real foi verificada separadamente após a revisão dos hooks. A validação ao vivo de espera por permissão, interrupção e encerramento ainda deve ser feita ao usar essas operações. Eventos de subagentes do Codex usam o ID da sessão pai e são ignorados para não sobrescrever seu estado.

Para conectar Claude Code CLI:

```sh
toolbox run -c Code python3 scripts/claude_hooks.py
```

O comando mescla 12 observadores em `~/.claude/settings.json`, faz backup e preserva as demais configurações e hooks. Para alimentar o anel do Claude com os limites oficiais da status line, execute `toolbox run -c Code python3 scripts/claude_statusline.py`. Esse passo preserva hooks e tema, cria backup e recusa substituir uma status line personalizada. A linha exibe apenas a cota de 5 horas e publica a mesma métrica por D-Bus a cada atualização do CLI, com atualização adicional a cada 60 segundos. Transmite somente a cota da conta e, para associar o contexto à sessão correta, seu ID e a porcentagem disponível; não transmite diretórios, transcrições ou mensagens. Segundo a documentação oficial, `rate_limits` é fornecido após a primeira resposta da API para assinantes Pro/Max/Ultra; sem o campo `five_hour`, o valor continua desconhecido, mesmo quando existe porcentagem de contexto. Essa conexão não exige relogar o GNOME. Na interface 0.3.0, a cota aparece em barras separadas e os anéis mostram contexto por sessão. Reabra o Claude Code para carregar a configuração e confira `/hooks`. Não é necessário relogar o GNOME. Inclui início/fim, envio de mensagem, ferramentas, permissões, notificações, falha de turno e elicitação MCP. Perguntas por `AskUserQuestion` ficam em “Aguardando você”; interrupções sinalizadas em falhas de ferramentas voltam a ocioso. Não inventamos um evento `Interrupt` que o Claude não expõe. Subagentes não sobrescrevem a sessão principal. O vínculo com o terminal usa o mesmo launcher do Codex.

Abra os snippets gerados pelo instalador e combine somente suas entradas de `hooks` com as configurações existentes. Não substitua o arquivo completo da ferramenta.

| Ferramenta | Snippet | Destino / revisão |
| --- | --- | --- |
| Codex | `hooks/codex.json` | Entradas em `~/.codex/hooks.json`; revisar e confiar pelo `/hooks` do Codex. |
| Claude Code | `hooks/claude.json` | Mesclar `hooks` em `~/.claude/settings.json`. |
| Hermes | `hooks/hermes.yaml` | Mesclar no `config.yaml` do perfil e revisar o consentimento oferecido pelo Hermes. |

Os hooks são observadores: não aprovam permissões, não bloqueiam ferramentas, não alteram prompts e não imprimem conteúdo no stdout. São transmitidos ferramenta, ID de sessão, estado e nome final do diretório do projeto; quando identificado, também o tipo de terminal, PID no host e instante de início do processo, necessários para o foco seguro da janela. A consulta de uso retém somente porcentagem restante, duração da janela e horários; não retém identificação da conta ou dados de autenticação. Prompts, argumentos de ferramentas, respostas e transcrições são descartados.

Os eventos suportados variam por versão da ferramenta. O Codex CLI presente no ambiente é 0.166.0 e informa `hooks` estável. O Claude Code 2.1.317 também está instalado: início e encerramento foram validados com o CLI real pelo protocolo de controle, sem enviar prompt ao modelo, em D-Bus isolado. Os demais estados do Claude foram validados com eventos injetados. Hermes ainda depende de configuração e validação real.

Se não houver novo evento por **15 minutos**, o estado vira “Sem sinal”. Ele não vira “Concluído” e não é contado como sessão conectada. O serviço mantém somente memória; ao reiniciá-lo, cada integração precisa reportar suas sessões novamente. Eventos de início/fim de sessão têm significado próprio; não há varredura de processos para inventar atividade.

Uma chamada de terminal genérica aparece como “Executando”; o adaptador não lê o comando para adivinhar que são testes. Estados mais específicos, como “Rodando testes”, podem ser reportados explicitamente:

```sh
quiet-island event --tool codex --session exemplo --status testing --project QuietIsland
quiet-island event --tool codex --session exemplo --status done --project QuietIsland
quiet-island event --tool codex --session exemplo --status disconnected
quiet-island snapshot
```

## Estrutura

- `extension/`: GJS, preferências, CSS, schemas e ícones locais.
- `bridge/quiet_island/`: serviço Python, validação e adaptadores.
- `bin/quiet-island`: CLI do checkout.
- `scripts/`: build, instalação sem ativação, hooks e GNOME isolado.
- `tests/`: testes de modelo e integrações reais em D-Bus privado.
- `research/`: formulário autossuficiente, escolhas e escopo preservados.
- `dist/quiet-island@local.shell-extension.zip`: pacote gerado da extensão.

## Referências e limites

O painel nativo usa interfaces internas do GNOME; por enquanto o suporte declarado é apenas GNOME 50. O Dash to Panel 74 do usuário preserva a barra superior (`stockgs-keep-top-panel=true`). O teste isolado com uma cópia do Dash to Panel 74 ativo passou. Isso não substitui validação com todos os aplicativos e extensões do desktop real.

Fontes técnicas: [GJS / GNOME Extensions](https://gjs.guide/extensions/), [MPRIS Player](https://specifications.freedesktop.org/mpris/latest/Player_Interface.html), [hooks do Codex](https://learn.chatgpt.com/docs/hooks), [hooks do Claude Code](https://code.claude.com/docs/en/hooks), [hooks do Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks). Os ícones são identificados em [ATTRIBUTION.md](licenses/ATTRIBUTION.md).

Falhas recuperáveis de ferramentas do Claude (`PostToolUseFailure`) retornam a “Pensando” e mantêm a duração da tarefa em andamento. Apenas `StopFailure` marca o turno como “Erro”; interrupções explícitas continuam voltando a “Ocioso”.
