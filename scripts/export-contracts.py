import json
import sys
from pathlib import Path

from compass_agent.main import app
from compass_agent.schemas import (
    AvailableModels,
    CallRecordView,
    CallResponse,
    CatalogPage,
    DemoOverview,
    ModelChangePage,
    ModelEvent,
    ProviderPage,
    ProviderView,
    SavedProvider,
)

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
schemas = {
    "demo-overview.schema.json": DemoOverview,
    "model-available.schema.json": AvailableModels,
    "model-call-record.schema.json": CallRecordView,
    "model-call-response.schema.json": CallResponse,
    "model-catalog-page.schema.json": CatalogPage,
    "model-change-page.schema.json": ModelChangePage,
    "model-event.schema.json": ModelEvent,
    "model-provider-page.schema.json": ProviderPage,
    "model-provider-view.schema.json": ProviderView,
    "model-saved-provider.schema.json": SavedProvider,
}
for name, schema in [
    ("openapi.json", app.openapi()),
    *((name, model.model_json_schema()) for name, model in schemas.items()),
]:
    (output / name).write_text(
        json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
