from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from compass_agent.database import DemoSale, get_engine, get_session
from compass_agent.llm import catalog
from compass_agent.llm.litellm_chat import LiteLLMChatAdapter
from compass_agent.llm.runtime import Runtime
from compass_agent.llm.systemone_decision import SystemOneDecisionAdapter
from compass_agent.model_api import ModelAPIError, error_response
from compass_agent.model_api import router as model_router
from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import ModelContinuationCodec
from compass_agent.models.service import ModelService
from compass_agent.schemas import DemoOverview, Health
from compass_agent.settings import get_settings

app = FastAPI(title="Compass Agent", version="0.1.0", description="Commerce analysis scaffold")


def build_model_service() -> ModelService:
    # Build the read-only catalog once at startup so no call pays the SDK import.
    catalog.records()
    codec = ModelContinuationCodec()
    runtime = Runtime(
        {
            "litellm_chat": LiteLLMChatAdapter(codec),
            "systemone_decision": SystemOneDecisionAdapter(),
        }
    )
    return ModelService(get_engine(), runtime)


app.state.model_service = build_model_service()
app.include_router(model_router)


@app.middleware("http")
async def model_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v1/models"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
    return response


@app.exception_handler(ModelError)
async def model_error_handler(_: Request, exc: ModelError) -> JSONResponse:
    return error_response(exc)


@app.exception_handler(ModelAPIError)
async def model_api_error_handler(_: Request, exc: ModelAPIError) -> JSONResponse:
    from compass_agent.model_api import api_error_response

    return api_error_response(exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": "validation_failed",
                "message": "请检查输入字段、范围和版本。",
                "requestId": str(uuid4()),
            }
        },
        status_code=422,
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/healthz", response_model=Health, operation_id="health")
def health() -> Health:
    return Health()


@app.get("/readyz", response_model=Health, operation_id="readiness")
def readiness() -> Health:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1 FROM demo_daily_sales LIMIT 1"))
            connection.execute(text("SELECT 1 FROM model_provider LIMIT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database or migration unavailable") from exc
    return Health()


@app.get("/v1/demo/overview", response_model=DemoOverview, operation_id="demo_overview")
def demo_overview(
    response: Response, session: Annotated[Session, Depends(get_session)]
) -> DemoOverview:
    # Opt-in fixture endpoint; this never accepts a tenant or a user-supplied query.
    if not get_settings().compass_demo_enabled:
        raise HTTPException(status_code=404, detail="Demo disabled")
    response.headers["Cache-Control"] = "no-store"
    try:
        row = session.execute(
            select(
                func.min(DemoSale.day),
                func.max(DemoSale.day),
                func.sum(DemoSale.revenue_minor),
                func.sum(DemoSale.orders),
                func.count(),
            ).select_from(DemoSale)
        ).one()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Demo database unavailable") from exc
    start, end, revenue, orders, days = row
    if not days:
        raise HTTPException(status_code=503, detail="Run the demo seed first")
    return DemoOverview(
        start_date=start, end_date=end, revenue_minor=revenue, orders=orders, days=days
    )
