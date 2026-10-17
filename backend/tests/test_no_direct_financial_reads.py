"""Architecture boundary: only the repository queries facts and documents.

The checker parses application modules with :mod:`ast`. It does not import or
execute them. ``PointInTimeRepository`` is the only application read path.
A later service is scanned automatically and calls that repository
(``visible_documents``, ``visible_fact_versions``, ``latest_visible_fact``).
It does not join ``ALLOWED_RELATIVE_PATHS``.

Extend the guard by editing the sets below, then add a snippet in this file:

* A new SQLAlchemy call shape (for example a future loader helper) goes in
  ``_QUERY_CALL_NAMES``, ``_JOIN_CALL_NAMES``, ``_FILTER_CALL_NAMES``, or
  ``_SQL_CALL_NAMES``.
* A new ORM class that must stay behind the repository goes in
  ``_ENTITY_EXPORTS`` and ``_SQL_TABLES``.
* ``ALLOWED_RELATIVE_PATHS`` grows only when a module is deliberately a read
  path. Adding a service there so it can call ``select(Fact)`` removes the
  check for that file.

Supported query shapes
----------------------
These are flagged when an argument, or the receiver of ``Table.select()``,
resolves to ``Fact`` or ``Document`` (the class, a column, ``__table__``, or
an ``aliased`` / ``alias`` of one):

* ``select``, including ``sa.select`` and ``from sqlalchemy import select as …``
* ``Table.select()`` on ``Fact.__table__`` or ``Document.__table__``
* ``query``, ``with_entities``, ``add_columns``
* ``get``, ``get_one`` when the first argument is the class or an alias of it.
  A mapping call shaped the same way (``cache.get(Fact)``) is reported too.
* ``join``, ``outerjoin``, ``join_from`` (a string's ``str.join`` is ignored)
* ``where``, ``filter``, ``having``, ``order_by``, ``group_by``
* ``joinedload``, ``selectinload``, ``subqueryload``, ``lazyload``,
  ``immediateload``, ``contains_eager``, ``with_loader_criteria``

Raw SQL is flagged when the first argument of ``text``, ``execute``, or
``exec_driver_sql`` is a constant string, a constant f-string, a constant
``+`` concatenation, or a name bound to one of those, and the text has
``FROM`` or ``JOIN`` of ``facts`` or ``documents`` (optional ``ONLY``,
``public``, and double quotes).

Names count when they are imported from ``titan.models``, ``titan.models.fact``,
or ``titan.models.document``, defined in the same file as the ORM class
(``__tablename__`` of ``facts`` or ``documents``), or assigned from one of
those (including a tuple or list that contains one). Relative imports are
resolved using the file's package.

Skipped paths
-------------
The tree walk reads ``backend/src/titan/**/*.py`` except
``pit/repository.py``. It does not read Alembic (``backend/alembic``), tests
(``backend/tests``), ``conftest.py``, or a directory named ``fixtures``.
Migrations and tests build schema and seed rows. Test fixtures also select
from ``facts`` on purpose. The detector still reports that select when it is
pointed at a test file; the walk never points it there.

Inserts are ignored. Ingestion will insert documents and facts outside the
repository. ``session.add(Document(...))`` and ``insert(Fact)`` stay legal.
Model declarations, ``ForeignKey("documents.id")``, ``event.listens_for``,
annotations, comments, and docstrings are not queries.

Limits of static detection
--------------------------
The checker cannot see a query it cannot name from the source of one call:

* ``select(model)`` when ``model`` is a parameter or other non-binding name,
  including a caller that passes ``Fact`` into that function.
* ``entity = getattr(models, "Fact")`` and any other computed attribute.
* ``exec`` or ``eval`` of source that would have been flagged as text.
* SQL whose table name is interpolated (``text(f"SELECT * FROM {table}")``
  or ``text("SELECT * FROM " + table)`` when ``table`` is not a constant).
* A parameter named ``Fact`` or ``Document`` shadows the imported class.
* A comparison stored on its own (``criterion = Fact.known_at <= cutoff``)
  and later passed to ``where(criterion)``. Assigning the column itself
  (``column = Fact.known_at``) is visible.
* ``joinedload(Company.filings)`` when the relationship attribute is not an
  expression that names ``Fact`` or ``Document``.
* Writes (``insert``, ``update``, ``delete``) and relationship lazy-loads
  that are ordinary attribute reads.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_SRC_ROOT = _BACKEND / "src"
_APP_ROOT = _SRC_ROOT / "titan"

# Extension point for a second read path. Services do not belong here.
ALLOWED_RELATIVE_PATHS = frozenset({"pit/repository.py"})
_SKIP_PARTS = frozenset({"fixtures", "__pycache__"})

_ENTITY_EXPORTS: dict[str, dict[str, str]] = {
    "titan.models": {
        "Fact": "titan.models.Fact",
        "Document": "titan.models.Document",
    },
    "titan.models.fact": {"Fact": "titan.models.fact.Fact"},
    "titan.models.document": {"Document": "titan.models.document.Document"},
}
_SUBMODULES: dict[str, frozenset[str]] = {
    "titan": frozenset({"models"}),
    "titan.models": frozenset({"fact", "document"}),
}
_EXACT_ENTITIES = frozenset(
    qual for exports in _ENTITY_EXPORTS.values() for qual in exports.values()
)
_CLASS_TABLES = {"Fact": "facts", "Document": "documents"}

_QUERY_CALL_NAMES = frozenset(
    {
        "select",
        "query",
        "with_entities",
        "add_columns",
        "with_loader_criteria",
        "joinedload",
        "selectinload",
        "subqueryload",
        "lazyload",
        "immediateload",
        "contains_eager",
    }
)
_JOIN_CALL_NAMES = frozenset({"join", "outerjoin", "join_from"})
_FILTER_CALL_NAMES = frozenset({"where", "filter", "having", "order_by", "group_by"})
_IDENTITY_CALL_NAMES = frozenset({"get", "get_one"})
_SQL_CALL_NAMES = frozenset({"text", "execute", "exec_driver_sql"})
_SELECT_PATHS = frozenset(
    {
        "sqlalchemy.select",
        "sqlalchemy.sql.select",
        "sqlalchemy.sql.expression.select",
        "sqlalchemy.future.select",
    }
)
_TEXT_PATHS = frozenset(
    {
        "sqlalchemy.text",
        "sqlalchemy.sql.text",
        "sqlalchemy.sql.expression.text",
    }
)
_SQLALCHEMY_MODULES = frozenset(
    {
        "sqlalchemy",
        "sqlalchemy.sql",
        "sqlalchemy.sql.expression",
        "sqlalchemy.future",
    }
)
_ALIAS_CALLS = frozenset({"aliased", "alias"})

_SQL_TABLE = re.compile(
    r"""(?ix)
    \b(?:from|join)\s+
    (?:only\s+)?
    (?:(?:"public"|public)\s*\.\s*)?
    (?:
        "(?:facts|documents)"
        |
        (?:facts|documents)\b
    )
    """
)


@dataclass(frozen=True)
class QueryViolation:
    """One SQLAlchemy read of facts or documents."""

    line: int
    col: int
    pattern: str


def is_guarded_module(path: Path) -> bool:
    """Return whether ``path`` is application code the guard must parse."""
    resolved = path.resolve()
    if resolved.suffix != ".py" or resolved.name == "conftest.py":
        return False
    try:
        relative = resolved.relative_to(_APP_ROOT)
    except ValueError:
        return False
    if relative.as_posix() in ALLOWED_RELATIVE_PATHS:
        return False
    return not any(part in _SKIP_PARTS for part in relative.parts)


def financial_query_violations(
    source: str,
    *,
    package: str | None = None,
) -> list[QueryViolation]:
    """Return SQLAlchemy reads of ``Fact`` or ``Document`` in ``source``."""
    scanner = _FinancialQueryScanner(package)
    scanner.visit(ast.parse(source))
    return scanner.violations


def guarded_violations() -> list[str]:
    """Return ``path:line:col: pattern`` hits under the application package."""
    found: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if not is_guarded_module(path):
            continue
        source = path.read_text(encoding="utf-8")
        package = _package_of(path)
        for hit in financial_query_violations(source, package=package):
            relative = path.relative_to(_APP_ROOT).as_posix()
            found.append(f"{relative}:{hit.line}:{hit.col}: {hit.pattern}")
    return found


def _package_of(path: Path) -> str:
    relative = path.resolve().relative_to(_SRC_ROOT).with_suffix("")
    return ".".join(relative.parts[:-1])


def _is_financial(qual: str) -> bool:
    if qual in _EXACT_ENTITIES:
        return True
    return any(qual.startswith(f"{entity}.") for entity in _EXACT_ENTITIES)


def _is_exact_entity(qual: str) -> bool:
    return qual in _EXACT_ENTITIES


def _matches(patterns: set[str], names: frozenset[str], paths: frozenset[str]) -> bool:
    return bool(patterns & names or patterns & paths)


def _canonical(patterns: set[str], names: frozenset[str], path_label: str) -> str:
    overlap = patterns & names
    if overlap:
        return min(overlap)
    return path_label


def _assigned_tablename(statement: ast.stmt) -> ast.expr | None:
    if isinstance(statement, ast.Assign):
        targets: tuple[ast.expr, ...] = tuple(statement.targets)
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        targets = (statement.target,)
        value = statement.value
    else:
        return None
    named = any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in targets)
    if named:
        return value
    return None


def _entity_defined_by_class(node: ast.ClassDef) -> str | None:
    table = _CLASS_TABLES.get(node.name)
    if table is None:
        return None
    for statement in node.body:
        value = _assigned_tablename(statement)
        if isinstance(value, ast.Constant) and value.value == table:
            module = "fact" if node.name == "Fact" else "document"
            return f"titan.models.{module}.{node.name}"
    return None


def _absolute_module(package: str | None, module: str | None, level: int) -> str | None:
    if level == 0:
        return module
    if not package:
        return None
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    if module:
        return f"{base}.{module}"
    return base


def _argument_names(args: ast.arguments) -> Iterator[str]:
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        yield arg.arg
    if args.vararg is not None:
        yield args.vararg.arg
    if args.kwarg is not None:
        yield args.kwarg.arg


class _FinancialQueryScanner(ast.NodeVisitor):
    """Collect query calls whose names resolve to Fact or Document."""

    def __init__(self, package: str | None) -> None:
        self.package = package
        self.scopes: list[dict[str, str]] = [{}]
        self.sql_scopes: list[dict[str, str]] = [{}]
        self.violations: list[QueryViolation] = []

    def _push(self) -> None:
        self.scopes.append({})
        self.sql_scopes.append({})

    def _pop(self) -> None:
        self.scopes.pop()
        self.sql_scopes.pop()

    def _bind(self, name: str, qual: str) -> None:
        self.scopes[-1][name] = qual

    def _bind_sql(self, name: str, sql: str) -> None:
        self.sql_scopes[-1][name] = sql

    def _lookup(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if name in scope:
                value = scope[name]
                return value or None
        return None

    def _lookup_sql(self, name: str) -> str | None:
        for scope in reversed(self.sql_scopes):
            if name in scope:
                value = scope[name]
                return value or None
        return None

    def _flag(self, node: ast.Call, pattern: str) -> None:
        self.violations.append(
            QueryViolation(line=node.lineno, col=node.col_offset, pattern=pattern)
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self._push()
        for name in _argument_names(node.args):
            self._bind(name, "")
            self._bind_sql(name, "")
        for statement in node.body:
            self.visit(statement)
        self._pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        defined = _entity_defined_by_class(node)
        if defined is not None:
            self._bind(node.name, defined)
        self._push()
        for statement in node.body:
            self.visit(statement)
        self._pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self._bind(alias.asname, alias.name)
            else:
                self._bind(alias.name.split(".", 1)[0], alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _absolute_module(self.package, node.module, node.level)
        if module is None:
            return
        exports = _ENTITY_EXPORTS.get(module, {})
        children = _SUBMODULES.get(module, frozenset())
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name == "*":
                for exported, qual in exports.items():
                    self._bind(exported, qual)
                continue
            if alias.name in exports:
                self._bind(local, exports[alias.name])
                continue
            if alias.name in children:
                self._bind(local, f"{module}.{alias.name}")
                continue
            if module in _SQLALCHEMY_MODULES and alias.name in {"select", "text"}:
                self._bind(local, f"{module}.{alias.name}")

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_assignment(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind_assignment(node.target, node.value)
        if node.annotation is not None:
            self.visit(node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
            existing = self._lookup_sql(node.target.id)
            added = self._folded_sql(node.value)
            if existing and added is not None:
                self._bind_sql(node.target.id, existing + added)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_assignment(node.target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _bind_assignment(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            folded = self._folded_sql(value)
            copied = self._copied_reference(value)
            if copied:
                self._bind(target.id, copied)
            elif folded is not None:
                self._bind(target.id, "")
            if folded is not None:
                self._bind_sql(target.id, folded)
            elif copied:
                self._bind_sql(target.id, "")
            return
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for element, item in zip(target.elts, value.elts, strict=True):
                self._bind_assignment(element, item)

    def _copied_reference(self, value: ast.expr) -> str | None:
        if isinstance(value, ast.Name):
            qual = self._lookup(value.id)
            if qual and _is_financial(qual):
                return qual
            return None
        if isinstance(value, ast.Attribute):
            qual = self._qualify(value)
            if qual and _is_financial(qual):
                return qual
            return None
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            for element in value.elts:
                copied = self._copied_reference(element)
                if copied and _is_financial(copied):
                    return copied
            return None
        if isinstance(value, ast.Call):
            callee = value.func
            name = callee.id if isinstance(callee, ast.Name) else None
            if name is None and isinstance(callee, ast.Attribute):
                name = callee.attr
            if name in _ALIAS_CALLS and value.args:
                return self._copied_reference(value.args[0])
        return None

    def _qualify(self, node: ast.expr) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        parts.reverse()
        if isinstance(current, ast.Name):
            base = self._lookup(current.id)
        elif isinstance(current, ast.Call):
            base = self._copied_reference(current)
        else:
            return None
        if not base:
            return None
        if parts:
            return f"{base}.{'.'.join(parts)}"
        return base

    def _folded_sql(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self._lookup_sql(node.id)
        if isinstance(node, ast.JoinedStr):
            chunks: list[str] = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    chunks.append(part.value)
                else:
                    return None
            return "".join(chunks)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._folded_sql(node.left)
            right = self._folded_sql(node.right)
            if left is None or right is None:
                return None
            return left + right
        return None

    def _callee_patterns(self, func: ast.expr) -> set[str]:
        patterns: set[str] = set()
        if isinstance(func, ast.Name):
            patterns.add(func.id)
            bound = self._lookup(func.id)
            if bound:
                patterns.add(bound)
            return patterns
        if isinstance(func, ast.Attribute):
            patterns.add(func.attr)
            base = self._qualify(func.value)
            if base:
                patterns.add(f"{base}.{func.attr}")
            return patterns
        return patterns

    def _check_call(self, node: ast.Call) -> None:
        patterns = self._callee_patterns(node.func)
        if self._is_table_select(node, patterns):
            self._flag(node, "Table.select")
            return
        if _matches(patterns, _SQL_CALL_NAMES, _TEXT_PATHS) and self._sql_hits(node):
            self._flag(node, _canonical(patterns, _SQL_CALL_NAMES, "text"))
            return
        if self._is_string_join(node):
            return
        if _matches(patterns, _IDENTITY_CALL_NAMES, frozenset()) and self._loads_entity(node):
            self._flag(node, _canonical(patterns, _IDENTITY_CALL_NAMES, "get"))
            return
        groups = (
            (_QUERY_CALL_NAMES, _SELECT_PATHS, "select"),
            (_JOIN_CALL_NAMES, frozenset(), "join"),
            (_FILTER_CALL_NAMES, frozenset(), "where"),
        )
        for names, paths, label in groups:
            if _matches(patterns, names, paths) and self._args_financial(node):
                self._flag(node, _canonical(patterns, names, label))
                return

    def _is_table_select(self, node: ast.Call, patterns: set[str]) -> bool:
        if not _matches(patterns, frozenset({"select"}), _SELECT_PATHS):
            return False
        if not isinstance(node.func, ast.Attribute):
            return False
        receiver = self._qualify(node.func.value)
        return bool(receiver and _is_financial(receiver))

    def _is_string_join(self, node: ast.Call) -> bool:
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "join"
            and isinstance(func.value, ast.Constant)
            and isinstance(func.value.value, str)
        )

    def _loads_entity(self, node: ast.Call) -> bool:
        if not node.args:
            return False
        qual = self._copied_reference(node.args[0])
        return bool(qual and _is_exact_entity(qual))

    def _sql_hits(self, node: ast.Call) -> bool:
        if not node.args:
            return False
        folded = self._folded_sql(node.args[0])
        return bool(folded and _SQL_TABLE.search(folded))

    def _args_financial(self, node: ast.Call) -> bool:
        expressions = [*node.args, *(keyword.value for keyword in node.keywords)]
        return any(self._tree_has_financial(expression) for expression in expressions)

    def _tree_has_financial(self, node: ast.AST) -> bool:
        if isinstance(node, ast.expr) and self._copied_reference(node):
            return True
        return any(self._tree_has_financial(child) for child in ast.iter_child_nodes(node))


def _program(body: str) -> str:
    header = """\
from sqlalchemy import select, text
from sqlalchemy.orm import aliased, selectinload
from titan.models.company import Company
from titan.models.document import Document
from titan.models.fact import Fact
"""
    return header + body


def test_supported_sqlalchemy_reads_are_flagged() -> None:
    samples = (
        "def load():\n    return select(Fact)\n",
        "def load():\n    return select(\n        Document\n    )\n",
        "def load():\n    return select(Fact.value)\n",
        "def load():\n    return select(Fact, Document.accession_number)\n",
        "def load(session):\n    return session.query(Document)\n",
        "def load(session, document_id):\n    return session.get(Document, document_id)\n",
        "def load(session, fact_id):\n    return session.get_one(Fact, fact_id)\n",
        (
            "def load():\n"
            "    return select(Company).join(Document, Document.company_id == Company.id)\n"
        ),
        (
            "def load():\n"
            "    return select(Company).outerjoin(Fact, Fact.company_id == Company.id)\n"
        ),
        (
            "def load():\n"
            "    return select(Company).join_from(\n"
            "        Document, Fact, Fact.document_id == Document.id\n"
            "    )\n"
        ),
        "def load():\n    return Fact.__table__.select()\n",
        "def load():\n    return select(Company).where(Fact.known_at <= cutoff)\n",
        "def load():\n    return select(Company).order_by(Document.known_at)\n",
        "def load():\n    filing = aliased(Document)\n    return select(filing)\n",
        "def load():\n    columns = (Fact.value, Document.id)\n    return select(*columns)\n",
        "def load():\n    return select(Company).options(selectinload(Document.company_id))\n",
        'def load():\n    return text("SELECT value FROM facts WHERE company_id = 1")\n',
        'def load():\n    return text("SELECT * FROM documents d JOIN facts f ON f.document_id = d.id")\n',
        'def load():\n    return text(f"SELECT * FROM facts")\n',
        'def load():\n    return text("SELECT * FROM " + "documents")\n',
        'def load():\n    return text(\'SELECT 1 FROM ONLY public.facts\')\n',
        'def load():\n    return text(\'SELECT 1 FROM "public"."documents"\')\n',
        (
            "def load(session):\n"
            '    statement = "SELECT value FROM facts WHERE company_id = :company_id"\n'
            "    return session.execute(statement)\n"
        ),
        (
            "def load(connection):\n"
            '    return connection.exec_driver_sql("SELECT 1 FROM facts")\n'
        ),
    )
    missed = [sample for sample in samples if not financial_query_violations(_program(sample))]
    assert missed == []


def test_import_aliases_still_resolve_to_the_orm_classes() -> None:
    renamed = (
        "from sqlalchemy import select as compile_select\n"
        "from titan.models.fact import Fact as FinancialFact\n"
        "from titan.models.document import Document as Filing\n"
        "def load(session, filing_id):\n"
        "    return compile_select(FinancialFact), session.get(Filing, filing_id)\n"
    )
    module_alias = (
        "from sqlalchemy import select\n"
        "import titan.models.fact as fact_mod\n"
        "import titan.models as models\n"
        "def load():\n"
        "    return select(fact_mod.Fact), select(models.Document)\n"
    )
    star = (
        "from sqlalchemy import select\n"
        "from titan.models import *\n"
        "def load():\n"
        "    return select(Fact)\n"
    )
    relative = (
        "from sqlalchemy import select\n"
        "from ..models.fact import Fact\n"
        "def load():\n"
        "    return select(Fact)\n"
    )
    through_package = (
        "import sqlalchemy as sa\n"
        "from titan.models.document import Document\n"
        "def load():\n"
        "    return sa.select(Document), sa.text('SELECT 1 FROM facts')\n"
    )
    assert financial_query_violations(renamed)
    assert financial_query_violations(module_alias)
    assert financial_query_violations(star)
    assert financial_query_violations(through_package)
    assert financial_query_violations(relative, package="titan.services")
    assert financial_query_violations(relative) == []


def test_schema_declarations_and_prose_are_not_queries() -> None:
    source = '''
"""
rows = select(Fact).where(Fact.known_at <= as_of)
SELECT value FROM facts
"""
# session.get(Document, document_id)

from sqlalchemy import ForeignKey, ForeignKeyConstraint, text, event
from sqlalchemy.orm import Mapped, mapped_column

class Fact(Base):
    __tablename__ = "facts"
    document_id: Mapped[object] = mapped_column(
        ForeignKey("documents.id"),
    )

class Document(Base):
    __tablename__ = "documents"

def declare():
    ForeignKeyConstraint(
        ["document_id", "company_id"],
        ["documents.id", "documents.company_id"],
    )
    text("gen_random_uuid()")
    text("'{}'::jsonb")
    text("SELECT to_regclass('public.facts')")

@event.listens_for(Fact, "before_insert")
def check(target: Fact) -> Fact:
    return target
'''
    assert financial_query_violations(source) == []


def test_inserts_and_repository_calls_are_not_queries() -> None:
    source = """
from sqlalchemy import insert
from titan.models.document import Document
from titan.models.fact import Fact
from titan.pit.repository import PointInTimeRepository

def write(session, repo: PointInTimeRepository, company_id, as_of):
    session.add(Document(accession_number="0000000000-00-000000"))
    session.execute(insert(Fact))
    return repo.visible_fact_versions(company_id=company_id, as_of=as_of)
"""
    assert financial_query_violations(source) == []


def test_string_join_is_not_a_sql_join() -> None:
    source = """
from titan.models.fact import Fact

def label():
    return ", ".join([Fact.__tablename__])
"""
    assert financial_query_violations(source) == []


def test_static_detection_misses_indirect_queries() -> None:
    """These are the known blind spots. A hit here means the limit changed."""
    indirect = """
from sqlalchemy import select
from titan.models.fact import Fact

def load(model):
    return select(model)

def run():
    return load(Fact)
"""
    computed = """
from sqlalchemy import select
import titan.models as models

def load():
    entity = getattr(models, "Fact")
    return select(entity)
"""
    executed = """
def load():
    exec("from sqlalchemy import select\\nfrom titan.models.fact import Fact\\nselect(Fact)")
"""
    interpolated = """
from sqlalchemy import text

def load(table):
    return text("SELECT * FROM " + table), text(f"SELECT * FROM {table}")
"""
    shadowed = """
from sqlalchemy import select
from titan.models.fact import Fact

def load(Fact):
    return select(Fact)
"""
    stored_criterion = """
from sqlalchemy import select
from titan.models.company import Company
from titan.models.fact import Fact

def load(cutoff):
    criterion = Fact.known_at <= cutoff
    return select(Company).where(criterion)
"""
    samples = (indirect, computed, executed, interpolated, shadowed, stored_criterion)
    assert [financial_query_violations(sample) for sample in samples] == [[], [], [], [], [], []]


def test_migrations_and_test_fixtures_are_outside_the_walk() -> None:
    version = next((_BACKEND / "alembic" / "versions").glob("*.py"))
    schema_test = _BACKEND / "tests" / "test_schema_db.py"
    pit_test = _BACKEND / "tests" / "test_pit_repository.py"
    assert is_guarded_module(_BACKEND / "alembic" / "env.py") is False
    assert is_guarded_module(version) is False
    assert is_guarded_module(schema_test) is False
    assert is_guarded_module(pit_test) is False
    assert is_guarded_module(_APP_ROOT / "fixtures" / "seed.py") is False
    assert is_guarded_module(_APP_ROOT / "pit" / "repository.py") is False
    assert is_guarded_module(_APP_ROOT / "models" / "fact.py") is True

    migration_hits = financial_query_violations(version.read_text(encoding="utf-8"))
    assert migration_hits == []
    # The schema test really does select a fact column. Exclusion is a path
    # rule, not a blind spot in the detector. Catalog probes such as
    # to_regclass('public.facts') stay quiet even if that file is parsed.
    schema_source = schema_test.read_text(encoding="utf-8")
    schema_lines = schema_source.splitlines()
    schema_hits = financial_query_violations(schema_source)
    assert any(hit.pattern == "select" for hit in schema_hits)
    assert all("to_regclass" not in schema_lines[hit.line - 1] for hit in schema_hits)


def test_orm_modules_declare_tables_without_querying_them() -> None:
    for name in ("base.py", "document.py", "fact.py"):
        source = (_APP_ROOT / "models" / name).read_text(encoding="utf-8")
        assert financial_query_violations(source, package="titan.models") == []


def test_repository_is_the_only_allowed_financial_read_path() -> None:
    repository = _APP_ROOT / "pit" / "repository.py"
    source = repository.read_text(encoding="utf-8")
    patterns = {
        hit.pattern for hit in financial_query_violations(source, package="titan.pit")
    }
    assert {"select", "join", "where", "order_by"} <= patterns
    assert "known_at <= cutoff" in source
    assert "Document.known_at <= cutoff" in source
    assert ALLOWED_RELATIVE_PATHS == frozenset({"pit/repository.py"})


def test_application_code_queries_facts_and_documents_only_in_the_repository() -> None:
    assert guarded_violations() == []
