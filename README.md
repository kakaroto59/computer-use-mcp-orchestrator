# Orquestrador Computer Use Híbrido (Rust + Python MCP)

Arquitetura de alto desempenho para automação de interface gráfica (*Computer Use*) no Windows, combinando um motor nativo de baixo nível em **Rust (`actuator_core`)** a um servidor de contexto assíncrono em **Python (`mcp_daemon`)** sob o padrão **Model Context Protocol (MCP)**.

---

## Principais Funcionalidades e Otimizações

- **Motor Nativo Win32 GDI + Enigo (`actuator_core`)**:
  - Captura de tela direta via `BitBlt` + `GetDIBits` com conversão BGRA $\to$ RGBA otimizada em modo `--release`.
  - **GIL-Free Execution**: Liberação do Global Interpreter Lock do Python (`py.allow_threads`) durante a captura de tela e codificação de imagem.
  - **Prioridade de Tempo Real (`HIGH_PRIORITY_CLASS`)**: Elevação automática da prioridade do processo no agendador do Windows via `SetPriorityClass`.
  - **Bypass de Isolamento de Desktop (`ensure_input_desktop`)**: Vinculação automática do thread ao desktop interativo ativo (`OpenInputDesktop` + `SetThreadDesktop`) e consciência nativa de DPI Per-Monitor V2 (`DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`), garantindo mapeamento físico $1:1$ mesmo sob escala de 125%/150%.
  - **Focus Guard (`focus_guard`)**: Acoplamento de fila de input Win32 (`AttachThreadInput` + `SetForegroundWindow`) imediatamente antes de eventos de mouse e teclado para evitar perda silenciosa de foco.
  - **Resiliência Avançada (`v0.4.0`)**:
    - **Bloqueio de Interferência Física (`InputBlockGuard`)**: Guard RAII com `BlockInput(true)` / `BlockInput(false)` durante a injeção de eventos `enigo`.
    - **Mapeamento Multi-Monitor (`VirtualScreen`)**: Captura e deslocamento de coordenadas via `SM_XVIRTUALSCREEN`, `SM_YVIRTUALSCREEN`, `SM_CXVIRTUALSCREEN` e `SM_CYVIRTUALSCREEN`.
    - **Prevenção de Suspensão**: `SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)`.
    - **Focus Assist & Cooldown de CPU (`1.2s`)**: Supressão automática de notificações toast via registro (`winreg` + `atexit`) e limitador de taxa assíncrono (`MIN_TOOL_COOLDOWN = 1.2s`) em todas as ferramentas MCP.

- **Daemon MCP Otimizado para LLMs (`mcp_daemon`)**:
  - **Coordenadas Relativas Normalizadas (`[0.0, 1.0]`)**: Elimina erros de escala de resolução exigindo `x_rel` e `y_rel` no intervalo estrito `[0.0, 1.0]`, convertidos dinamicamente para pixels físicos da tela ativa.
  - **Pipeline Visual de Baixo Consumo de Tokens (`process_screenshot_for_llm`)**: Redimensionamento proporcional `LANCZOS` (limite máximo `1024x1024`), conversão para escala de cinza 8-bit (`"L"`) e compressão JPEG (`quality=60, optimize=True`).
  - **Latência Adaptativa**: Tempos de espera pós-ação calibrados por operação (`50 ms` move, `120 ms` digitação, `250 ms` clique, `650 ms` abertura de menus do sistema).
  - **Schemas MCP Minificados**: Docstrings e descrições Pydantic compactadas para reduzir o overhead fixo de tokens em cada turno.

---

## Benchmarks de Performance (1920x1080)

| Métrica | Baseline Inicial (`dev` RGB) | Pós-Otimização (`--release` + Grayscale 1024px) | Ganho / Redução |
| :--- | :---: | :---: | :---: |
| **Latência de Captura Nativa (Rust)** | `940.73 ms` | **`140.36 ms`** | **-85.1% ($6.7\times$ mais rápido)** |
| **Latência Total Fim-a-Fim (Captura + LLM Pipeline)** | `940.73 ms` | **`253.46 ms`** | **-73.1% ($3.7\times$ mais rápido)** |
| **Tamanho do Payload Visual (Base64)** | `197.88 KB` | **`49.93 KB`** | **-74.8% de tráfego/tokens** |
| **Tamanho do Schema JSON MCP (7 Tools)** | `5.419 bytes` | **`3.400 bytes`** | **-37.3% de overhead fixo** |

---

## Estrutura do Projeto

```text
├── actuator_core/                         # Módulo nativo em Rust (PyO3 + maturin)
│   ├── Cargo.toml                         # Dependências (pyo3, enigo, windows GDI/Win32, image)
│   └── src/
│       └── lib.rs                         # GDI capture, GIL release, Focus Guard, Input Desktop attach
├── mcp_daemon/                            # Servidor Model Context Protocol em Python
│   ├── requirements.txt                   # mcp, maturin, pydantic, Pillow, numpy
│   ├── __init__.py
│   └── server.py                          # Servidor MCP stdio, coordenadas [0.0, 1.0] e pipeline LANCZOS
├── antigravity_mcp_config.json            # Configuração do servidor MCP para o cliente LLM
├── test_actuator.py                       # Suíte de testes de integração e benchmark
├── relatorio_execucao_orquestrador_mcp.md # Relatório técnico do teste E2E e RCA inicial
├── relatorio_de_otimizacao.md             # Relatório de benchmarks pós-otimização
└── README.md
```

---

## Ferramentas MCP Expostas (`tools`)

Todas as ferramentas de atuação executam o ciclo de feedback visual fechado (ação $\to$ estabilização adaptativa $\to$ captura otimizada em Base64):

1. **`capture_screen`**: Captura a tela otimizada (JPEG 1024px Grayscale em Base64) e retorna a resolução física `(width, height)`.
2. **`execute_click`**: Move o cursor e clica nas coordenadas relativas `(x_rel, y_rel)` $\in [0.0, 1.0]$ (`left`, `right`, `middle`, simples ou duplo).
3. **`execute_type`**: Digita texto no elemento em foco (protegido por `focus_guard`) com suporte a `press_enter`.
4. **`execute_press_key`**: Pressiona tecla única (`enter`, `tab`, `esc`, `backspace`, `win`, setas, `f1`–`f12`).
5. **`execute_hotkey`**: Dispara atalho combinado (ex.: `["win", "r"]`, `["ctrl", "c"]`, `["alt", "f4"]`).
6. **`execute_scroll`**: Rola a roda do mouse (`up`, `down`, `left`, `right`).
7. **`execute_move`**: Move o cursor para `(x_rel, y_rel)` $\in [0.0, 1.0]$ sem clicar.

---

## Compilação e Execução

### 1. Compilar o Módulo Nativo Rust (Modo Release)
```powershell
.\mcp_daemon\venv\Scripts\maturin.exe develop --release --manifest-path .\actuator_core\Cargo.toml
```

### 2. Executar a Suíte de Testes e Benchmarks
```powershell
$env:PYTHONPATH = (Get-Location).Path
.\mcp_daemon\venv\Scripts\python.exe test_actuator.py
```

### 3. Iniciar o Servidor MCP (`stdio`)
```powershell
$env:PYTHONPATH = (Get-Location).Path
.\mcp_daemon\venv\Scripts\python.exe -m mcp_daemon.server
```
