# Orquestrador Computer Use (Antigravity + Gemini + MCP)

Arquitetura de alto desempenho para automação de interface (*Computer Use*) unindo um atuador nativo de baixo nível em **Rust** a um servidor de contexto em **Python** utilizando o padrão **Model Context Protocol (MCP)**.

---

## Estrutura do Projeto

```text
├── actuator_core/                 # Módulo de baixo nível em Rust
│   ├── Cargo.toml                 # Dependências (pyo3, enigo, windows GDI, image)
│   └── src/
│       └── lib.rs                 # Win32 GDI capture + input dispatch + Input Desktop attach
├── mcp_daemon/                    # Servidor de contexto MCP em Python
│   ├── venv/                      # Ambiente virtual isolado (Python 3.14)
│   ├── requirements.txt           # mcp, maturin, pydantic, Pillow, numpy
│   ├── __init__.py
│   └── server.py                  # Servidor MCP stdio com ciclo de feedback visual
├── antigravity_mcp_config.json    # Configuração do MCP para o Antigravity / Gemini
├── test_actuator.py               # Suíte de validação integrada
└── README.md
```

---

## Ferramentas MCP Expostas (`tools`)

Todas as ferramentas de atuação disparam o ciclo de feedback visual automático (execução da ação $\to$ latência de renderização do SO $\to$ captura e validação visual em base64):

1. **`capture_screen`**: Captura o estado atual da tela em JPEG/PNG otimizado.
2. **`execute_click`**: Move o cursor e clica nas coordenadas `(x, y)` (`left`, `right`, `middle`).
3. **`execute_type`**: Digita strings de texto no elemento em foco com opção `press_enter`.
4. **`execute_press_key`**: Aciona teclas individuais (`enter`, `tab`, `backspace`, `escape`, setas direcionais, etc.).
5. **`execute_hotkey`**: Executa atalhos de teclado combinados (ex: `["win", "r"]`, `["ctrl", "c"]`).
6. **`execute_scroll`**: Rola a roda do mouse (`up`, `down`, `left`, `right`).
7. **`execute_move`**: Move o cursor sem clicar.

---

## Como Executar

### 1. Teste de Integração Direto
```powershell
$env:PYTHONPATH = (Get-Location).Path
.\mcp_daemon\venv\Scripts\python.exe test_actuator.py
```

### 2. Inicialização do Servidor MCP (stdio)
```powershell
$env:PYTHONPATH = (Get-Location).Path
.\mcp_daemon\venv\Scripts\python.exe -m mcp_daemon.server
```

---

## Integração no Antigravity

A configuração já foi registrada em `C:\Users\Joao\.gemini\config\mcp_config.json` e espelhada em [antigravity_mcp_config.json](file:///c:/Users/Joao/Documents/antigravity/hopeful-hertz/antigravity_mcp_config.json).

### Prompt de Teste End-to-End
> *"Abra a calculadora do sistema, clique em '5', clique em '+', clique em '5', clique em '=' e valide o resultado visualmente."*
