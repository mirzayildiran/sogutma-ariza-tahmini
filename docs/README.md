# Documentation

Documentation for the AI-powered refrigeration fault prediction project. For the main page, see the
[repository README](../README.md); source code: <https://github.com/mirzayildiran/sogutma-ariza-tahmini>

| Document | Contents | Audience |
|---|---|---|
| [Model Card](model-card.md) | The model's purpose, usage limits, data (simulator), features, models, health-score formula, evaluation results, limitations and what must be validated in a pilot | Technical team, evaluators, partners |
| [Project Proposal](project-proposal.md) | Draft for an R&D funding application: problem, objectives, method, work packages and timeline, risks, commercialization, budget items, team | Management, application preparation |
| [Field Architecture](field-architecture.md) | Moving from the demo to a real site: sensors, sampling, gateway, MQTT, storage, model serving and retraining, alert flow, security | Field/IoT and software team |
| [Data Format](data-format.md) | CSV/JSON measurement format, units, validation and sensor-quality rules | Field/IoT and data team |
| [REST API and Docker](api.md) | Prediction service: endpoints (CSV / JSON), sample curl requests, API key, error codes, running with uvicorn and docker compose, an example of sending hourly data from a gateway | Software and field/IoT team |
| [Notifications](notifications.md) | Email / Telegram / webhook alerts: rules (persistence, cooldown, quiet hours), settings reference, environment variables, cron example, security | Field/IoT and maintenance team |

## Important note

All numerical results in this project were measured on **synthetic (simulator) data** and do not represent real field
performance. The targets and sensor/sampling values in the proposal and architecture documents are **suggestions**;
fields marked with `[...]` are placeholders to be filled in. Documents with bracketed fields should not be considered
ready for use until real data and sources have been added.

The dashboard, CLI messages and source-code identifiers are in Turkish; where a Turkish term appears in the documents
(a column name, a CLI flag, a code), its meaning is given next to it.

## Suggested reading order

1. [Repository README](../README.md): product overview and demo.
2. [Model Card](model-card.md): what the model does and does not do.
3. [Field Architecture](field-architecture.md): moving to a real site.
4. [Project Proposal](project-proposal.md): project framework.
