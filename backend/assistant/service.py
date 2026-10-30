# @file: backend/assistant/service.py
# @exports: AssistantService
# @depends_on: backend/assistant/{intent_parser,sql_templates}.py
# @purpose: Resolve validated slots separately from an owned read-only template transaction.
from fastapi.encoders import jsonable_encoder

from backend.assistant.intent_parser import parse_query
from backend.assistant.sql_templates import execute_template
from backend.schemas.assistant import ParameterizedSQLResponse
from backend.schemas.ingestion import utc_now


class AssistantService:
    def __init__(self, session_factory, settings):
        self.session_factory, self.settings = session_factory, settings

    async def query(self, request):
        # Geocoding may autobegin a transaction, so template execution uses a new session.
        async with self.session_factory() as parsing:
            slots, source = await parse_query(request.query, parsing, self.settings)
        async with self.session_factory() as readonly:
            rows = await execute_template(readonly, slots, request.include_fixtures, utc_now())
        return ParameterizedSQLResponse(template_id=slots.sql_template_id, parameters=slots.slots,
            explanation='Validated slots matched an allowlisted read-only template; all values were bound. '
                        'Results reflect stored local reports and history, not a complete live traffic feed.',
            results=jsonable_encoder(rows), row_count=len(rows), include_fixtures=request.include_fixtures,
            slot_source=source)
