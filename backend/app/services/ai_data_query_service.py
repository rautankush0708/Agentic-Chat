"""Python port of the reference AIDataQueryService.cs, retargeted from a hospital
management system onto an enterprise demand-forecasting platform, and from
SQL Server/T-SQL onto SQLite.

Pipeline (unchanged from the original): classify intent -> (optionally) block unsafe
or unauthorized questions -> build a schema-grounded prompt -> ask the LLM for SQL ->
validate the SQL against the schema (tables/columns actually exist, no destructive
statements, no SELECT *) -> execute it -> ask the LLM to turn the raw rows into a
human-readable, role-appropriate HTML answer.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ..extensions import db
from .llm_provider import LLMError, LLMProvider
from .tts_provider import synthesize_speech  # noqa: F401  (re-exported for routes/tts.py convenience)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "demand_forecasting_schema.json"

SQL_ROW_LIMIT_DEFAULT = 200

SYSTEM_PROMPT = """
You are an AI assistant integrated into an enterprise Demand Forecasting Platform.
Your role is to help demand planners, supply planners, category managers, analysts,
and executives understand demand, forecasts, inventory, and supply performance.

PLATFORM ROLES

Demand Planner
- Analyze historical demand and build/monitor forecasts.
- Investigate demand pattern classification (smooth, erratic, intermittent, lumpy, seasonal).

Supply Planner
- Monitor inventory levels and stockout/overstock risk.
- Track replenishment orders and delivery status.

Category Manager
- Analyze sales, revenue, and promotional performance for a product category.

Analyst
- Evaluate forecast accuracy (MAPE, bias) across models and time periods.

Executive
- Get high-level summaries of demand, revenue, and forecast performance across the business.

GUIDELINES
- Never expose internal database structure to the user.
- Provide clear, professional, data-grounded assistance.
- Never fabricate numbers; only report what the data shows.
"""


# --------------------------------------------------------------------------------
# Schema loading
# --------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_schema_json() -> dict:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_schema_tables() -> list[dict]:
    return _load_schema_json()["tables"]


def load_global_business_rules() -> list[dict]:
    return _load_schema_json().get("GlobalBusinessRules", [])


def load_metric_safety_rules() -> list[str]:
    return _load_schema_json().get("MetricSafetyRules", [])


def build_schema_text(tables: list[dict]) -> str:
    lines: list[str] = []

    global_rules = load_global_business_rules()
    if global_rules:
        lines.append("\n### GLOBAL BUSINESS RULES (STRICT)")
        for rule in global_rules:
            lines.append(f"\nRule: {rule['RuleName']}")
            for definition in rule.get("Definition", []):
                lines.append(f"- {definition}")
            lines.append(f"Strict: {rule.get('Strict', False)}")

    safety_rules = load_metric_safety_rules()
    if safety_rules:
        lines.append("\n### METRIC SAFETY RULES (STRICT)")
        for rule in safety_rules:
            lines.append(f"- {rule}")

    for table in sorted(tables, key=lambda t: t["TableName"]):
        lines.append(f"\n### TABLE: {table['TableName']}")
        if table.get("Purpose"):
            lines.append(f"Purpose: {table['Purpose']}")
        if table.get("Tags"):
            lines.append(f"Tags: {', '.join(table['Tags'])}")
        if table.get("DataType"):
            lines.append(f"Type: {table['DataType']}")
        if table.get("DefaultDateColumn"):
            lines.append(f"DefaultDateColumn: {table['DefaultDateColumn']}")
        if table.get("BusinessRules"):
            lines.append("BusinessRules:")
            for rule in table["BusinessRules"]:
                lines.append(f"- {rule}")

        lines.append("Columns:")
        columns = table.get("columns") or []
        if not columns:
            lines.append("- No columns defined")
            continue
        for col in columns:
            fk = col.get("ForeignKey")
            fk_text = f" -> FK {fk['Table']}.{fk['Column']}" if fk else ""
            tags = f" [{','.join(col['Tags'])}]" if col.get("Tags") else ""
            formula = f" | Formula: {col['FormulaHint']}" if col.get("FormulaHint") else ""
            warning = f" | Warning: {col['Warning']}" if col.get("Warning") else ""
            lines.append(f"- {table['TableName']}.{col['ColumnName']}{fk_text}{tags}{formula}{warning}")

    return "\n".join(lines)


def build_schema_map(schema_text: str) -> dict[str, set[str]]:
    """table_name(lower) -> set of column_name(lower), parsed back out of the
    "- Table.Column ..." lines produced by build_schema_text (mirrors BuildSchemaMap)."""
    schema_map: dict[str, set[str]] = {}
    for line in schema_text.splitlines():
        trimmed = line.strip().lstrip("-").strip()
        parts = trimmed.split(".")
        if len(parts) < 2:
            continue
        table = parts[0].strip()
        column = parts[1].split(" ")[0].strip()
        if not table or not column:
            continue
        schema_map.setdefault(table.lower(), set()).add(column.lower())
    return schema_map


# --------------------------------------------------------------------------------
# Safety / guardrail checks (ported near-verbatim from AIDataQueryService.cs)
# --------------------------------------------------------------------------------

_UNSAFE_PATTERNS = [
    r"^\s*SELECT\s+.+\s+FROM\s+",
    r"^\s*INSERT\s+INTO\s+",
    r"^\s*UPDATE\s+\w+\s+SET\s+",
    r"^\s*DELETE\s+FROM\s+",
    r"^\s*DROP\s+TABLE\s+",
    r"^\s*ALTER\s+TABLE\s+",
    r"^\s*TRUNCATE\s+TABLE\s+",
    r"^\s*EXEC\s+",
    r"^\s*MERGE\s+",
    r"'\s*OR\s*'?\d+'?\s*=\s*'?\d+",
    r"'\s*AND\s*'?\d+'?\s*=\s*'?\d+",
    r"\bUNION\s+SELECT\b",
    r"\binformation_schema\b",
    r"\bsqlite_master\b",
    r"\bPRAGMA\b",
    r"\bxp_cmdshell\b",
    r"\bCHAR\s*\(",
    r"0x[0-9A-Fa-f]{4,}",
]


def is_unsafe_question(question: str) -> bool:
    if not question or not question.strip():
        return False
    return any(re.search(p, question, re.IGNORECASE) for p in _UNSAFE_PATTERNS)


def is_logical_injection(question: str) -> bool:
    if not question or not question.strip():
        return False
    pattern = r"'\s*\w+\s*'\s*=\s*'\s*\w+\s*'|\bOR\s+1\s*=\s*1\b|\bAND\s+1\s*=\s*1\b|''\s*=\s*''"
    return bool(re.search(pattern, question, re.IGNORECASE))


_SENSITIVE_KEYWORDS = [
    "password", "passwords", "login", "credentials", "username and password",
    "user credentials", "secret", "access key", "api key", "private data", "authentication",
]


def is_sensitive_data_request(question: str) -> bool:
    if not question:
        return False
    lowered = question.lower()
    return any(k in lowered for k in _SENSITIVE_KEYWORDS)


_SYSTEM_ACCESS_PATTERNS = [
    r"\b(database|db)\b",
    r"\b(table|tables|entity|entities)\b",
    r"\b(list|show|give|get|all)\s+(database\s+)?(table|tables|entity|entities)\b",
    r"\b(schema|structure|architecture|design)\b",
    r"\b(database|table)\s+(schema|structure|design)\b",
    r"\b(column|columns|field|fields|attribute|attributes)\b",
    r"\ball\s+(column|columns|field|fields)\b",
    r"\b(relation|relations|relationship|relationships|mapping|mappings|join|joins|foreign\s+key|primary\s+key)\b",
    r"information_schema",
    r"\bsqlite_master\b",
    r"metadata",
    r"system\s+data",
    r"internal\s+data",
    r"hidden\s+data",
    r"\bdescribe\b",
    r"\bdesc\b",
    r"\bshow\s+.*\b(database|tables|columns|schema)\b",
    r"\bgive\s+.*\b(database|tables|columns|schema)\b",
    r"\blist\s+.*\b(database|tables|columns|schema)\b",
    r"\bget\s+.*\b(database|tables|columns|schema)\b",
]


def is_system_access_request(question: str) -> bool:
    if not question:
        return False
    return any(re.search(p, question, re.IGNORECASE) for p in _SYSTEM_ACCESS_PATTERNS)


def is_suspicious_data_extraction(question: str) -> bool:
    """Guards against probing for a specific individual staff member's record by name."""
    if not question:
        return False
    patterns = [
        r"\bwhere\b.*\bname\b",
        r"\bnamed\s+\w+",
        r"\bname\s*=\s*'.*?'",
    ]
    return any(re.search(p, question, re.IGNORECASE) for p in patterns)


def is_select_query(sql: str) -> bool:
    if not sql or not sql.strip():
        return False
    cleaned = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()
    if not re.match(r"^(WITH|SELECT)\b", cleaned, re.IGNORECASE):
        return False
    if not re.search(r"\bSELECT\b", cleaned, re.IGNORECASE):
        return False
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|ATTACH|DETACH|PRAGMA)\b", cleaned, re.IGNORECASE):
        return False
    return True


def contains_sql_keywords(text_value: str) -> bool:
    return bool(re.search(
        r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|EXEC|MERGE|CREATE|FROM\s+\w+|JOIN\s+\w+)\b",
        text_value, re.IGNORECASE,
    ))


class SqlValidationError(Exception):
    pass


def validate_sql(sql: str) -> str:
    """Raises SqlValidationError; returns the sql with a LIMIT clause guaranteed."""
    if re.search(r"\bSELECT\s+\*", sql, re.IGNORECASE):
        raise SqlValidationError("SELECT * is not allowed. You must explicitly list columns.")

    forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|EXEC|MERGE|CREATE|ATTACH|DETACH|PRAGMA)\b"
    if re.search(forbidden, sql, re.IGNORECASE):
        raise SqlValidationError("Only SELECT queries are allowed.")

    if not re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        sql = sql.rstrip().rstrip(";") + f" LIMIT {SQL_ROW_LIMIT_DEFAULT}"

    return sql


def validate_join_tables(sql: str, schema_text: str) -> None:
    schema_map = build_schema_map(schema_text)
    tables_used = set()
    for match in re.finditer(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", sql, re.IGNORECASE):
        table = match.group(1) or match.group(2)
        if table:
            tables_used.add(table)

    for table in tables_used:
        if table.lower() not in schema_map:
            raise SqlValidationError(f"Unauthorized table used: {table}")


def validate_columns(sql: str, schema_text: str) -> None:
    schema_map = build_schema_map(schema_text)

    alias_map: dict[str, str] = {}
    for match in re.finditer(r"\bFROM\s+(\w+)\s+(\w+)|\bJOIN\s+(\w+)\s+(\w+)", sql, re.IGNORECASE):
        table = match.group(1) or match.group(3)
        alias = match.group(2) or match.group(4)
        if alias and alias.upper() not in ("ON", "WHERE", "GROUP", "ORDER", "LIMIT"):
            alias_map.setdefault(alias, table)

    for match in re.finditer(r"(\w+)\.(\w+)", sql):
        alias, column = match.group(1), match.group(2)
        if alias not in alias_map:
            continue  # could be a CTE or derived table; skip
        table = alias_map[alias]
        if table.lower() not in schema_map:
            raise SqlValidationError(f"Unauthorized table used: {table}")
        if column.lower() not in schema_map[table.lower()]:
            raise SqlValidationError(f"Column {column} does not exist in table {table}")


def is_unauthorized_access(question: str, tables: list[dict], role: str) -> bool:
    restricted_tables = [
        t["TableName"].lower() for t in tables
        if role in (t.get("RestrictedRoles") or [])
    ]
    lowered = question.lower()
    return any(t in lowered for t in restricted_tables)


def validate_restricted_tables(sql: str, tables: list[dict], role: str) -> tuple[bool, str | None]:
    restricted_tables = [
        t["TableName"] for t in tables
        if role in (t.get("RestrictedRoles") or [])
    ]
    for table in restricted_tables:
        if re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
            return True, "You are not authorised to view this information"
    return False, None


# --------------------------------------------------------------------------------
# Intent / history helpers
# --------------------------------------------------------------------------------

_ANALYTICAL_INTENT_RE = re.compile(
    r"\b(why|reason|drop|increase|decrease|trend|compare|difference|growth|behind|explain|changed)\b",
    re.IGNORECASE,
)
_FORECASTING_CONTEXT_RE = re.compile(
    r"\b(demand|forecast|revenue|sales|inventory|stock|stockout|replenishment|order|mape|accuracy)\b",
    re.IGNORECASE,
)


def is_follow_up_analytics_question(question: str, history: str) -> bool:
    if not question or not question.strip():
        return False
    if not _ANALYTICAL_INTENT_RE.search(question):
        return False
    if not _FORECASTING_CONTEXT_RE.search(history or ""):
        return False
    return True


def format_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = []
    for msg in history[-10:]:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        content = re.sub(r"<.*?>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        if len(content) > 500:
            content = content[:500]
        lines.append(f"Role={msg.get('role')}; Content={content}")
    return "\n".join(lines)


CAPABILITIES_BY_ROLE = {
    "Demand Planner": """
        <h5>Demand Planning</h5>
        <ul>
            <li>Review historical demand trends by product, category, or region</li>
            <li>Check the latest forecast for any SKU</li>
            <li>Investigate demand pattern classification (smooth, erratic, intermittent, seasonal)</li>
        </ul>""",
    "Supply Planner": """
        <h5>Supply Planning</h5>
        <ul>
            <li>Check current inventory and stockout risk</li>
            <li>Track open and overdue replenishment orders</li>
            <li>Review reorder recommendations by location</li>
        </ul>""",
    "Category Manager": """
        <h5>Category Management</h5>
        <ul>
            <li>Analyze sales revenue and volume by category or brand</li>
            <li>Review promotional performance</li>
            <li>Compare category trends over time</li>
        </ul>""",
    "Analyst": """
        <h5>Forecast Analytics</h5>
        <ul>
            <li>Evaluate forecast accuracy (MAPE) across models and runs</li>
            <li>Identify over- or under-forecasted products (bias)</li>
            <li>Compare demand pattern mix across the catalog</li>
        </ul>""",
    "Executive": """
        <h5>Executive Overview</h5>
        <ul>
            <li>High-level demand and revenue summaries</li>
            <li>Forecast accuracy at a glance</li>
            <li>Supply risk overview</li>
        </ul>""",
    "Admin": """
        <h5>Admin</h5>
        <ul>
            <li>Full visibility across demand, forecast, inventory, and supply data</li>
        </ul>""",
}


def get_capabilities_response(role: str) -> str:
    common = """
    <div class='ai-capabilities'>
        <p>I can help you analyze demand, forecasts, inventory, and supply performance using real-time data.</p>
        <ul>
            <li>Track historical demand and revenue by product, category, or region</li>
            <li>Review the latest demand forecast and its accuracy</li>
            <li>Monitor inventory levels and stockout risk</li>
            <li>Check replenishment order status</li>
        </ul>"""
    role_specific = CAPABILITIES_BY_ROLE.get(role, "")
    footer = """
        <p class='mt-2'><i>Ask me things like:</i></p>
        <ul>
            <li>Total demand this month for Beverages</li>
            <li>What's the forecast accuracy for the latest run?</li>
            <li>Which products are at risk of stockout?</li>
            <li>Show me open replenishment orders</li>
        </ul>
        <p><b>Please ask a demand/forecast/inventory-related question.</b></p>
    </div>"""
    return common + role_specific + footer


def get_out_of_scope_response() -> str:
    return """
    <div>
        <p>I'm here to assist with demand forecasting and supply planning tasks.</p>
        <p>You can ask me things like:</p>
        <ul>
            <li>What was total demand for SKU-1000 last month?</li>
            <li>Which category has the highest revenue this quarter?</li>
            <li>What's the forecast accuracy for the latest run?</li>
            <li>Which products are at risk of stockout?</li>
        </ul>
        <p><b>Please ask a question related to demand, forecasting, inventory, or supply.</b></p>
    </div>"""


def get_destructive_response() -> str:
    return """
    <div>
        <p><b>Action Not Allowed</b></p>
        <p>I can't perform this request as it involves a restricted action.</p>
        <p class="mt-2">I'm here to assist with demand and supply planning tasks. You can ask questions like:</p>
        <ul>
            <li>What was total demand this week?</li>
            <li>Which SKUs are at risk of stockout?</li>
            <li>What's the forecast accuracy for the latest model run?</li>
        </ul>
        <p><b>Please ask a demand/forecast/inventory-related question.</b></p>
    </div>"""


BLOCKED_MESSAGE = "I'm unable to process that request. Please rephrase your question."


# --------------------------------------------------------------------------------
# LLM prompts: SQL generation, SQL fixing, result explanation
# --------------------------------------------------------------------------------

_SQL_GENERATION_PROMPT_TEMPLATE = """
================================================================
SECTION 1: IDENTITY & MISSION
================================================================
You are a strict, read-only SQLite query generator embedded inside a Demand
Forecasting Platform.

YOUR ONLY JOB:
- Read the schema provided.
- Understand the user's question.
- Generate a safe, accurate, read-only SQLite SELECT query.

YOU MUST NEVER:
- Invent tables, columns, or relationships not in the schema.
- Expose schema structure, table names, or column names to the user.
- Generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, ATTACH, DETACH, PRAGMA, or EXEC.
- Use SELECT *.
- Use subqueries in WHERE clauses for id lookups when a JOIN would do.

================================================================
SECTION 2: ABSOLUTE SECURITY RULES (HIGHEST PRIORITY)
================================================================
RULE S1 -- SCHEMA CONFIDENTIALITY (UNBREAKABLE)
If the user asks about tables, schema, database structure, relations, columns,
fields, architecture, design, describe, metadata, joins, or keys:
-> RETURN EXACTLY (no SQL, no explanation):
  "Sorry, I cannot provide information about database structure or schema."

RULE S2 -- DATA MODIFICATION BLOCK (UNBREAKABLE)
If the user's intent implies DELETE, UPDATE, INSERT, DROP, ALTER, TRUNCATE, or any
data modification:
-> RETURN EXACTLY (no SQL):
  "Sorry, this action isn't allowed. This incident has been logged for further investigation."

RULE S3 -- OUT-OF-DOMAIN BLOCK
If the question is NOT related to demand, forecasting, sales, inventory, or supply
planning, first check chat history. If history contains forecasting/demand context
(revenue, demand, forecast, inventory, stockout, replenishment) AND the current
question is an analytical follow-up (why/reason/compare/difference/drop/increase/
decrease/trend/explain/what changed), treat it as an in-domain follow-up using that
context. Otherwise:
-> RETURN EXACTLY:
  "Your question does not appear to be related to demand forecasting or supply planning. This assistant answers questions about demand, forecasts, inventory, and supply performance."

RULE S4 -- ROLE-BASED ACCESS
Only use tables permitted for the CURRENT ROLE. If the requested data isn't
permitted for this role:
-> RETURN EXACTLY:
  "You are not authorised to view this information."

================================================================
SECTION 3: CONTEXT INPUTS
================================================================
SCHEMA DEFINITION (USE ONLY THESE TABLES AND COLUMNS):
{schema}

CHAT HISTORY (last turns -- use ONLY if the question is ambiguous):
{history}

CURRENT QUESTION (PRIMARY -- always prioritize this):
{question}

CURRENT STAFF ID:
{staff_id}

CURRENT ROLE:
{current_role}

CURRENT DATE:
{current_date}

================================================================
SECTION 4: CONTEXT & HISTORY RESOLUTION RULES
================================================================
RULE C1 -- Treat the current question as fully independent by default.
RULE C2 -- Use history only when the question is short/ambiguous (e.g. "this week?",
"by category", "for the West region") and clearly relates to the previous question.
RULE C3 -- Ignore history when the question is a complete sentence, changes topic,
or is out of domain.

================================================================
SECTION 5: SCHEMA USAGE RULES
================================================================
RULE SC1 -- ZERO HALLUCINATION: use only tables/columns explicitly listed above.
RULE SC2 -- Use "Tags"/"BusinessRules"/"FormulaHint"/"Warning" metadata exactly as given.
RULE SC3 -- SalesHistory = actual/historical demand. ForecastDetail = predicted demand.
Never confuse the two.

================================================================
SECTION 6: SQL GENERATION RULES (STRICT)
================================================================
RULE G1 -- No SELECT *. Always explicitly list required columns.
RULE G2 -- Every table MUST have a short alias (e.g. P = Product, SH = SalesHistory,
L = Location, C = Category, FR = ForecastRun, FD = ForecastDetail, FA = ForecastAccuracy,
INV = Inventory, RO = ReplenishmentOrder). Every column reference must be prefixed
with its alias.
RULE G3 -- Always end the query with LIMIT 200 unless the user asks for a specific
smaller number of rows (e.g. "top 5").
RULE G4 -- Never use a correlated subquery in WHERE for an id lookup when a JOIN works.
RULE G5 -- Apply IsActive = 1 AND IsDeleted = 0 filters on master tables (Product,
Category, Location, Channel, Staffs, Promotion) unless the user explicitly asks for
inactive/deleted/discontinued records.
RULE G6 -- Never expose internal auto-increment primary keys (SalesHistoryId,
ForecastDetailId, ForecastAccuracyId, ForecastRunId, InventoryId, OrderId,
ClassificationId, ProductId, CategoryId, LocationId, ChannelId, StaffId) in the
final SELECT list -- always resolve them to a human-readable name via JOIN
(ProductName, CategoryName, LocationName, ChannelName, RunName) instead.
RULE G7 -- If the user asks for "the latest forecast" without naming a run, resolve
the latest completed ForecastRun first (MAX(RunDate) WHERE Status = 'Completed').

================================================================
SECTION 7: JOIN RULES
================================================================
- Product name: JOIN Product P ON [Table].ProductId = P.ProductId
- Category name: JOIN Product P ... JOIN Category C ON P.CategoryId = C.CategoryId
- Location/region name: JOIN Location L ON [Table].LocationId = L.LocationId
- Channel name: JOIN Channel CH ON SH.ChannelId = CH.ChannelId
- Forecast run name/model: JOIN ForecastRun FR ON [Table].ForecastRunId = FR.ForecastRunId
- If no ForeignKey is defined between two tables, do NOT join them.

================================================================
SECTION 8: DATE & TIME RULES (SQLite)
================================================================
Use date('now','localtime') as "today" (matches how this database's demo data was
seeded) -- never hardcode a literal date or year.

"today": [Col] >= date('now','localtime') AND [Col] < date('now','localtime','+1 day')
"this week" (trailing 7 days incl. today): [Col] >= date('now','localtime','-6 days') AND [Col] < date('now','localtime','+1 day')
"this month": [Col] >= date('now','localtime','start of month') AND [Col] < date('now','localtime','start of month','+1 month')
"last month": [Col] >= date('now','localtime','start of month','-1 month') AND [Col] < date('now','localtime','start of month')
"this year": [Col] >= date('now','localtime','start of year') AND [Col] < date('now','localtime','start of year','+1 year')
"last year": [Col] >= date('now','localtime','start of year','-1 year') AND [Col] < date('now','localtime','start of year')
A specific date range named by the user should use literal date('YYYY-MM-DD') bounds.
If no timeframe is mentioned for a question about SalesHistory/ForecastAccuracy,
default to "this month".
Inventory/DemandPatternClassification are point-in-time snapshots; do not apply a
date range unless the user asks for a specific historical snapshot date.

================================================================
SECTION 9: METRIC RULES
================================================================
RULE M1 -- "demand"/"units sold" -> SUM(SalesHistory.QuantitySold)
RULE M2 -- "revenue"/"sales value" -> SUM(SalesHistory.Revenue)
RULE M3 -- "forecast"/"predicted demand" -> ForecastDetail.ForecastedQuantity, scoped
to the latest completed ForecastRun unless another run is named.
RULE M4 -- "forecast accuracy"/"MAPE" -> AVG(ForecastAccuracy.MAPE). NEVER compute
MAPE manually by joining ForecastDetail with SalesHistory row by row.
RULE M5 -- "forecast bias"/"over-forecasting"/"under-forecasting" -> AVG(ForecastAccuracy.Bias);
positive = over-forecasting, negative = under-forecasting.
RULE M6 -- "stockout risk"/"understocked" -> Inventory.OnHandQuantity < Inventory.SafetyStock.
"needs reorder" -> (Inventory.OnHandQuantity + Inventory.InTransitQuantity) <= Inventory.ReorderPoint.
RULE M7 -- "open orders"/"pending orders" -> ReplenishmentOrder.Status IN ('Pending','Shipped').
"overdue orders" -> Status != 'Delivered' AND ExpectedDeliveryDate < date('now','localtime').
RULE M8 -- Never join SalesHistory and ForecastDetail/ForecastAccuracy at row level and
sum both together -- they answer different questions (actual vs predicted). If the
user wants both, return them as separate columns/scalar subqueries, never blended
into one SUM.

================================================================
SECTION 10: RESPONSE FORMAT (STRICT -- NO EXCEPTIONS)
================================================================
Return STRICT VALID JSON with exactly these two fields, using ONLY single braces:
{{
  "explanation": "One sentence describing which tables/columns were used and why.",
  "sql": "SELECT ..."
}}
- Do NOT return plain text, markdown, or code blocks.
- The "sql" field must contain ONLY the final executable SQLite query.
- If returning a message (a security rule triggered) put the message in the "sql"
  field as plain text instead of SQL, and still fill "explanation".
"""


def build_sql_generation_prompt(question: str, schema: str, history: str, staff_id: str, current_role: str, current_date: str) -> str:
    return _SQL_GENERATION_PROMPT_TEMPLATE.format(
        schema=schema, history=history or "(none)", question=question,
        staff_id=staff_id or "(none)", current_role=current_role or "(unknown)",
        current_date=current_date,
    )


_FIX_SQL_PROMPT_TEMPLATE = """
### ROLE
You are an expert SQLite query debugger for a Demand Forecasting Platform.

### ERROR CONTEXT
- User Question: {question}
- Failing SQL: {sql}
- SQL Error: {error}

### DEBUGGING RULES
1. If the error mentions an unknown column/table, re-check the schema below and use
   only what exists there.
2. If the error is a syntax error around date functions, use SQLite date()/datetime()
   modifiers, not T-SQL functions like GETDATE()/DATEADD()/ISNULL().
3. Prefer a JOIN over a correlated subquery when the subquery could return more
   than one row.
4. Schema (use ONLY these tables/columns): {schema}

### RESPONSE FORMAT
Return JSON only:
{{
  "explanation": "Briefly explain the fix.",
  "sql": "SELECT ..."
}}
"""


_EXPLAIN_RESULT_PROMPT_TEMPLATE = """
You are a professional Demand Planning Data Analyst.

Context:
User Question: {question}
SQL Query: {sql}
Data Result (JSON): {data_json}
CurrentRole: {current_role}

Instructions:
1. Primary Goal: if 'Data Result' contains any items, produce a Bootstrap table
   using class 'table table-striped table-hover mt-3'.
2. Only say "No records available" if 'Data Result' is empty or null; otherwise
   explain briefly why there might be no data (e.g. "No sales recorded for this SKU
   this week").
3. Format dates as 'MMM dd, yyyy'. Prefix money amounts with '$' and show them
   rounded to 2 decimal places with comma separators (e.g. $12,345.67).
4. Remove internal ids from display; if a numeric code has no readable meaning,
   omit that column entirely.
5. If there is only one record, present it as a simple <p>, not a table.
6. Never invent or change numeric values -- use exactly what's in Data Result.
7. Show information appropriate to CurrentRole (Demand Planner, Supply Planner,
   Category Manager, Analyst, Executive, Admin).
8. Make the presentation professional, not a raw DB dump.

Return JSON:
{{
  "html": "..."
}}
"""


class AgentService:
    def __init__(self, config):
        self.config = config
        self.llm = LLMProvider(config)

    # -------------------- LLM plumbing --------------------

    def _call_ai(self, user_prompt: str) -> str:
        return self.llm.chat(SYSTEM_PROMPT, user_prompt)

    @staticmethod
    def _clean_ai_response(content: str) -> str:
        return (
            content.replace("```json", "")
            .replace("```", "")
            .replace("\n", " ")
            .replace("\r", " ")
            .strip()
        )

    def _detect_intent(self, question: str, history: str) -> str:
        prompt = f"""
        You are an intent classifier for a demand forecasting assistant.

        Classify the user's question into ONE of these categories:
        1. DATA_QUERY -> asking about demand, sales, forecasts, inventory, stockout,
           replenishment orders, or demand pattern classification.
        2. CAPABILITY -> asking what the assistant can do
        3. GREETING -> greetings or small talk
        4. OUT_OF_SCOPE -> anything not related to demand/forecasting/supply,
           EXCEPT when it's a follow-up to previous in-domain context.
        5. DESTRUCTIVE_ACTION -> requests to delete, cancel, or modify data (e.g.
           "cancel all orders", "delete this forecast run")

        Be strict: if it's not clearly demand/forecast/supply-related, and history
        doesn't establish that context for a follow-up, classify as OUT_OF_SCOPE.

        Return ONLY one word.
        History: {history}
        Question: {question}
        """
        response = self._call_ai(prompt)
        return response.strip().upper()

    # -------------------- Main entrypoint --------------------

    def run_agent(self, request_payload: dict) -> dict:
        question = (request_payload.get("question") or "").strip()
        history_messages = request_payload.get("history") or []
        current_role = request_payload.get("currentRole") or ""
        staff_id = request_payload.get("staffId") or ""

        history_text = format_history(history_messages)

        if not question:
            return self._create_response("Please ask a question.", question)

        if is_follow_up_analytics_question(question, history_text):
            intent = "DATA_QUERY"
        else:
            try:
                intent = self._detect_intent(question, history_text)
            except LLMError as exc:
                return self._create_response(f"<p>{exc}</p>", question)

        if intent == "GREETING":
            return self._create_response(
                "Hello! I can help you with demand, forecast, inventory, and supply questions. How can I assist you?",
                question,
            )
        if intent == "CAPABILITY":
            return self._create_response(get_capabilities_response(current_role), question)
        if intent == "OUT_OF_SCOPE":
            return self._create_response(get_out_of_scope_response(), question)
        if intent == "DESTRUCTIVE_ACTION":
            return self._create_response(get_destructive_response(), question)

        if (
            is_unsafe_question(question)
            or is_logical_injection(question)
            or is_sensitive_data_request(question)
            or is_system_access_request(question)
            or is_suspicious_data_extraction(question)
        ):
            return {
                "question": question,
                "sql": None,
                "answer": {"type": "html", "value": BLOCKED_MESSAGE},
            }

        tables = load_schema_tables()

        if is_unauthorized_access(question, tables, current_role):
            return {
                "question": question,
                "sql": None,
                "answer": {"type": "html", "value": "<p>You are not authorised to view this information</p>"},
            }

        schema_text = build_schema_text(tables)

        try:
            sql = self._generate_sql(question, schema_text, history_text, staff_id, current_role)
        except LLMError as exc:
            return self._create_response(f"<p>{exc}</p>", question)

        is_unauthorized, message = validate_restricted_tables(sql, tables, current_role)
        if is_unauthorized:
            return {
                "question": question,
                "sql": None,
                "answer": {"type": "html", "value": f"<p>{message}</p>"},
            }

        if not is_select_query(sql):
            sanitized = "I'm unable to process that request. Please rephrase your question." if contains_sql_keywords(sql) else sql
            return {
                "question": question,
                "sql": None,
                "answer": {"type": "html", "value": f"<p>{sanitized}</p>"},
            }

        try:
            sql = validate_sql(sql)
            validate_join_tables(sql, schema_text)
            validate_columns(sql, schema_text)
        except SqlValidationError as exc:
            try:
                sql = self._fix_sql(sql, str(exc), schema_text, question)
            except LLMError as llm_exc:
                return self._create_response(f"<p>{llm_exc}</p>", question)

        try:
            rows = self._execute_sql(sql)
        except Exception as exc:  # SQL execution failed even after validation
            try:
                sql = self._fix_sql(sql, str(exc), schema_text, question)
                rows = self._execute_sql(sql)
            except Exception as retry_exc:
                return {
                    "question": question,
                    "sql": sql,
                    "answer": {
                        "type": "html",
                        "value": "<p>We're having difficulty running that query. Please rephrase your question and try again.</p>",
                    },
                }

        try:
            answer = self._explain_result(question, sql, rows, current_role)
        except LLMError as exc:
            answer = {"type": "html", "value": f"<p>{exc}</p>"}

        return {"question": question, "sql": sql, "answer": answer}

    # -------------------- Steps --------------------

    def _generate_sql(self, question: str, schema: str, history: str, staff_id: str, current_role: str) -> str:
        from datetime import datetime

        prompt = build_sql_generation_prompt(
            question, schema, history, staff_id, current_role, datetime.now().strftime("%Y-%m-%d"),
        )
        response = self._clean_ai_response(self._call_ai(prompt))
        try:
            parsed = json.loads(response)
            return parsed["sql"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise LLMError(f"AI returned invalid JSON while generating SQL: {response}") from exc

    def _fix_sql(self, sql: str, error: str, schema: str, question: str) -> str:
        prompt = _FIX_SQL_PROMPT_TEMPLATE.format(question=question, sql=sql, error=error, schema=schema)
        response = self._clean_ai_response(self._call_ai(prompt))
        parsed = json.loads(response)
        return parsed["sql"]

    @staticmethod
    def _execute_sql(sql: str) -> list[dict]:
        result = db.session.execute(text(sql))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def _explain_result(self, question: str, sql: str, rows: list[dict], current_role: str) -> dict:
        cleaned = [
            {k: v for k, v in row.items() if "id" not in k.lower() and k.lower() != "status"}
            for row in rows
        ]
        cleaned = [row for row in cleaned if row]

        prompt = _EXPLAIN_RESULT_PROMPT_TEMPLATE.format(
            question=question, sql=sql, data_json=json.dumps(cleaned, default=str), current_role=current_role,
        )
        response = self._clean_ai_response(self._call_ai(prompt))
        try:
            parsed = json.loads(response)
            return {"type": "html", "value": parsed["html"]}
        except (json.JSONDecodeError, KeyError):
            return {"type": "html", "value": "<p>No records found.</p>"}

    @staticmethod
    def _create_response(html: str, question: str = "") -> dict:
        return {"question": question, "sql": None, "answer": {"type": "html", "value": html}}
