# Workflow-файлы

Готовые workflow для GitHub Actions. Чтобы включить, скопируйте их в `.github/workflows/`:

- `ci.yml` — `pytest` на каждый push и PR;
- `arb-qa.yml` — `locqa check` (через `action.yml` из корня репозитория) на каждый PR, где меняются `*.arb`:
  сводка в PR, отчёты артефактом, красный статус при high-находках.

В игровом репозитории замените `uses: ./` на `uses: Yerbo71/decentrathon-grand-loc-qa@main` и укажите свои пути к ARB.
