# SleepMax

Multi-agent sleep analytics platform. Ingests wearable data (Apple Health, Fitbit, Oura), enriches it with contextual signals (weather, calendar, exercise), discovers correlational patterns, and generates actionable sleep improvement insights.


## Architecture

The system uses four agent layers:

1. **Ingestion Agent** — Parses wearable exports into a unified sleep schema
2. **Context Agent** — Enriches each sleep record with environmental/behavioral signals
3. **Discovery Agent** — Finds correlational patterns between context and sleep quality
4. **Insight Agent** — Translates patterns into actionable recommendations