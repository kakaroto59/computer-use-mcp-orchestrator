# Relatório Técnico de Evolução: Precisão DPI, Resiliência e Gestão de Z-Order (`v0.4.1`)

Este documento consolida todas as falhas diagnosticadas em produção, as soluções arquiteturais implementadas nas camadas **Rust (`actuator_core`)** e **Python (`mcp_daemon`)**, as evidências dos testes End-to-End (E2E) e o mapeamento de obstáculos remanescentes.

---

## 1. O Que Foi Corrigido e Como Foi Resolvido

### 1.1. Desvio de Coordenadas por Escalonamento de Tela (DPI 125%)
- **Sintoma**: Durante a limpeza de histórico do Google Chrome, cliques calculados sobre o frame `1920x1080` sofriam deslocamento matemático nos eixos $X$ e $Y$, errando o botão *"Delete data"*.
- **Causa Raiz**: O Windows estava configurado com escala de 125%. Sem um contexto Per-Monitor V2 explícito no processo nativo, o subsistema Win32 aplicava virtualização de DPI sobre as chamadas de mouse/métricas.
- **Solução Implementada**:
  - Habilitada a feature `"Win32_UI_HiDpi"` em `actuator_core/Cargo.toml`.
  - Injetada a chamada `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` tanto na inicialização do módulo `actuator_core` quanto em `ensure_input_desktop`.
  - **Resultado**: Correspondência física estrita de $1:1$ validada via `GetCursorPos` (`Requested: (1234, 678) -> Actual: (1234, 678)`) e acerto exato no botão *"Delete data"* em `(0.628, 0.824)` $\to$ `(1206, 890)`.

---

### 1.2. Colisão de Z-Order e Sobreposição de Miniaturas da Barra de Tarefas
- **Sintoma**: Ao alternar entre aplicações (ex.: PNetLab/Chrome e IDE Antigravity) clicando na barra de tarefas ou via `Alt+Tab`, o Windows exibia *hover previews* (miniaturas da barra de tarefas) ou mantinha a janela anterior sobreposta, obstruindo a visão do modelo.
- **Causa Raiz**: A ausência de manipulação direta de `HWND` no gerenciador de janelas (DWM/Win32) obrigava o agente a interagir visualmente com a barra de tarefas do sistema.
- **Solução Implementada**:
  - Criada a função nativa `focus_window` em Rust usando `EnumWindows`, `IsWindowVisible` e `GetWindowTextW`.
  - Quando a substring alvo coincide com o título da janela visível, o motor executa:
    1. `ShowWindow(hwnd, SW_RESTORE)` (restaura caso minimizada);
    2. `ShowWindow(hwnd, SW_MAXIMIZE)` (maximiza em tela cheia);
    3. `focus_guard(hwnd)` (`AttachThreadInput` + `SetForegroundWindow`) para contornar o *Foreground Lock Timeout* do Windows.
  - Exposta a 8ª ferramenta MCP `manage_window` com a **Diretriz de Hiper-Especificidade** (proibindo termos genéricos como `"Chrome"` e exigindo o título específico da aba/documento ativo).

---

### 1.3. Ausência de Teclas de Função (`F1`–`F12`) e `Delete` no Parser de Teclado
- **Sintoma**: No primeiro teste E2E (YouTube $\to$ Notepad), o comando `execute_hotkey(["alt", "f4"])` retornou `{"status":"error","message":"Unknown key: f4"}`.
- **Causa Raiz**: A função `parse_key` mapeava apenas teclas de navegação básicas e caracteres únicos Unicode.
- **Solução Implementada**:
  - Adicionado suporte nativo a `F1`–`F12` (`Key::F1` a `Key::F12`) e `delete`/`del` (`Key::Delete`) em `parse_key`.
  - **Resultado**: No teste E2E subsequente de Z-Order, `execute_hotkey(["alt", "f4"])` encerrou tanto o Bloco de Notas quanto o Chrome nativamente na primeira tentativa.

---

### 1.4. Blindagem Ambiental (Interferência Física, Multi-Monitor, Energia, Toasts e CPU)
1. **Bloqueio de Interferência Física (`InputBlockGuard`)**:
   - Estrutura RAII em Rust que aciona `BlockInput(true)` antes de cada injeção do `enigo` e garante `BlockInput(false)` no `Drop`, impedindo que movimentos acidentais do mouse/teclado físico desviem a ação.
2. **Mapeamento Absoluto Multi-Monitor (`VirtualScreen`)**:
   - Substituição de `SM_CXSCREEN`/`SM_CYSCREEN` por `SM_XVIRTUALSCREEN`, `SM_YVIRTUALSCREEN`, `SM_CXVIRTUALSCREEN` e `SM_CYVIRTUALSCREEN` em `capture_screen_gdi` e nas funções de clique/movimento.
3. **Prevenção de Suspensão de Tela/Sistema**:
   - `SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)` na inicialização de `actuator_core`.
4. **Supressão de Notificações Toast (`set_focus_assist`)**:
   - Configuração automática de `NOC_GLOBAL_SETTING_TOASTS_LEVEL = 2` no registro do Windows durante a vida útil do daemon, com restauração automática via `atexit`.
5. **Throttling Assíncrono de CPU (`apply_cpu_cooldown`)**:
   - Janela mínima de `MIN_TOOL_COOLDOWN = 1.2s` garantida via `asyncio.sleep` no início de todas as 8 ferramentas MCP, evitando *thermal throttling* em sessões longas.

---

## 2. Resumo dos Testes E2E Executados

| Teste E2E | Objetivo | Resultado |
| :--- | :--- | :---: |
| **1. Limpeza de Histórico (Chrome 125% DPI)** | Abrir `chrome://history`, clicar em *"Delete browsing data"* `(0.088, 0.302)` e clicar em *"Delete data"* `(0.628, 0.824)` | **100% Aprovado** |
| **2. Pesquisa YouTube 3Blue1Brown $\to$ Notepad** | Abrir Chrome, navegar ao canal, clicar no vídeo em destaque, copiar URL (`Ctrl+L`, `Ctrl+C`), colar e anotar no Bloco de Notas e encerrar | **100% Aprovado** |
| **3. Alternância de Foco Z-Order (`manage_window`)** | Abrir Wikipédia (`Automação`), sobrepor com Bloco de Notas, recuperar Wikipédia com `manage_window("Automação")`, retornar ao editor com `manage_window("Bloco de Notas")`, digitar e encerrar via `Alt+F4` | **100% Aprovado** |

---

## 3. Análise de Obstáculos Remanescentes e Mitigações Futuras

Embora a arquitetura atual (`v0.4.1`) esteja estável e validada para uso diário, existem **4 cenários de borda** inerentes ao sistema operacional Windows que merecem atenção:

### 3.1. Abas em Segundo Plano Dentro da Mesma Janela do Navegador
- **Obstáculo**: A API `GetWindowTextW` do Windows enxerga apenas o título da **aba atualmente ativa** em cada janela do navegador. Se a página `"PNetLab"` estiver aberta em uma aba em segundo plano enquanto a aba ativa daquela mesma janela for `"Google"`, `manage_window("PNetLab")` não encontrará o título no `HWND`.
- **Mitigação Atual / Evolução Sugerida**:
  - Manter aplicações críticas (como PNetLab) em janelas dedicadas do navegador ou usar `Ctrl+1`..`Ctrl+8` / `Ctrl+Tab` após focar a janela do navegador.
  - Em versão futura (`v0.5.0`), integrar a API **Windows UI Automation (`IUIAutomation`)** ou Chrome DevTools Protocol (CDP) para inspecionar e ativar abas internas mesmo quando não estão ativas no título do `HWND`.

### 3.2. Janelas Elevadas (Administrador) e Diálogos UAC (*Secure Desktop*)
- **Obstáculo**: Pela arquitetura de segurança do Windows (*UIPI - User Interface Privilege Isolation*), se o `mcp_daemon` estiver rodando como usuário padrão, ele não pode injetar eventos `SendInput` (`enigo`) nem acionar `BlockInput(true)` sobre janelas que rodam como **Administrador** ou sobre o prompt de **UAC (User Account Control)**.
- **Mitigação**: Quando a automação envolver instaladores ou terminais administrativos, iniciar a IDE/daemon com privilégios de Administrador (o que também habilita 100% do `BlockInput` no kernel).

### 3.3. Digitação de Caracteres Acentuados sob Alta Carga (`execute_type`)
- **Obstáculo**: Em layouts de teclado ABNT2, o `enigo::text()` injeta sequências Unicode `KEYEVENTF_UNICODE`. Em alguns aplicativos legados ou terminais remotos (como consoles VNC/HTML5 do PNetLab/NoVNC), pacotes `KEYEVENTF_UNICODE` podem ter acentos suprimidos (ex.: `"concluído"` $\to$ `"concluido"`) ou serem ignorados por canvases WebGL/VNC que escutam apenas *scancodes* físicos.
- **Mitigação Futura**: Adicionar um parâmetro opcional `use_clipboard: bool = False` em `execute_type` (para colar textos longos instantaneamente via área de transferência) e um modo de *scancode* físico para consoles VNC do PNetLab.

### 3.4. Restauração de Janelas que Não Suportam Maximização (`SW_MAXIMIZE`)
- **Obstáculo**: Em `focus_window`, chamamos `ShowWindow(hwnd, SW_RESTORE)` seguido de `ShowWindow(hwnd, SW_MAXIMIZE)`. Para janelas de diálogo fixo (ex.: Calculadora compacta ou janelas de propriedades do Windows que não possuem estilo `WS_MAXIMIZEBOX`), o `SW_MAXIMIZE` pode ser ignorado ou alterar o layout esperado.
- **Mitigação Futura**: Verificar o estilo da janela (`GetWindowLongW(hwnd, GWL_STYLE) & WS_MAXIMIZEBOX`) antes de aplicar `SW_MAXIMIZE`, ou adicionar um parâmetro `maximize: bool = True` em `manage_window`.
