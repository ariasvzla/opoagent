# Per-Tema Subagent Chain

Each topic goes through 4 sequential subagents (calibrator runs once globally).

```mermaid
flowchart LR
    A["📥 Tema N +<br/>calibration context"] --> B

    subgraph chain["4-step chain per topic"]
        direction TB
        B["✍️ redactor_especialista<br/>Drafts content by epígrafes<br/>intro → epígrafe 1 → epígrafe 2 → ... → conclusión"]
        C["🧠 pnl_pedagogico<br/>Didactic improvements<br/>clarity, flow, connections"]
        D["🔍 revision_calidad<br/>Quality gate<br/>APROBADO or RECHAZADO"]
        E["🧪 generador_tests<br/>20 questions × 4 options<br/>with explanations"]
        B --> C --> D --> E
    end

    E --> F["📄 tema-result.md<br/>saved to tema-NN/"]
    E --> G["🏁 done message<br/>streamed to UI"]

    style chain fill:#0f3460,stroke:#e94560,color:#eee
```

## Parallelism model

```
Time ──────────────────────────────────────────►

Tema 01: [redactor    ][pnl][rev][tests]
Tema 02: [redactor    ][pnl][rev][tests]
Tema 03: [redactor    ][pnl][rev][tests]
Tema 04:             [redactor    ][pnl][rev]...
  ...
Tema 10:                                          [redactor]...
         ↑                                        ↑
     MAX_PARALLELISM concurrent                semaphore releases slot
```

Each column is one subagent call. Topics run in parallel (horizontally) but
subagents within a topic run sequentially (vertically).
