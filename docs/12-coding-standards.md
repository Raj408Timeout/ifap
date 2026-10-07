# 12. Coding Standards

## Python
- **Python 3.13**, `from __future__ import annotations`, PEP 695 generics (`def f[T: BaseModel]`).
- **100 % type hints.** `pyright --strict` must report 0 errors. Untyped third-party APIs are
  wrapped at a single, commented *typed boundary* (see the LangGraph and LangChain adapters).
- **Formatting:** black (line length 100). **Linting:** ruff (`E,W,F,I,B,UP,N,SIM,C90,RUF,ASYNC,PL,PT,TC,ANN,S`)
  with max McCabe complexity **6**, and pylint at 10.00.
- **Small functions.** One responsibility each, early returns, no flag arguments.
- **Immutability.** Domain models are `frozen=True`. Change them with `model_copy(update=...)`.
- **Dependency injection.** Constructor injection with keyword-only arguments. No globals except
  the plugin registry. No service locators inside the core.
- **Ports are `typing.Protocol`**, which allows structural typing with no inheritance coupling to
  adapters.
- **Errors.** Raise domain exceptions (`*Error`) and translate them at the API boundary. Never
  `except Exception` except at resilience boundaries (agent retries, LLM adapter), and then with
  a pylint pragma and a log line.
- **No magic values.** Use `Settings` or data files, and name module-level constants.
- **Logging.** Use `structlog` key/value events (`agent.completed`, `event.published`). Never log
  secrets or prompt contents at INFO level.
- **Async.** I/O is `async`. Blocking SDKs (Chroma) run via `asyncio.to_thread`.

## Naming
| Kind | Convention | Example |
|---|---|---|
| Port | noun, capability | `KnowledgeProvider` |
| Adapter | technology + port | `ChromaKnowledgeProvider` |
| Agent | role + `Agent` | `ValidationAgent` |
| Use case | noun + `Service` | `QuestionnaireGenerationService` |
| Event | past tense | `QuestionnairePublished` |

## TypeScript / React
`strict` + `noUncheckedIndexedAccess`. Use function components and keep components presentational.
API access only goes through `lib/api.ts`, and types live in `lib/types.ts`, mirroring the API
contracts. Style with Tailwind utility classes and no inline styles. Accessible labels are
required on every input.

## Git
Use Conventional Commits (`feat(agents): add sentiment agent`). Merge PRs only when green, with
one approval. ADRs are required for any new port, technology or cross-cutting pattern.
