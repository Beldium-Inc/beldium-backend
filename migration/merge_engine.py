"""Row matching and merging shared by find_missing.py and merge.py.

How a row from the old database is handled, in order:

1. Build the v2 row from the old row using the table's TableMap.
2. Remap foreign keys. Each FK column holds an old primary key; it is replaced
   by the v2 primary key that parent row ended up with (matched or inserted).
   A parent that was skipped means the child is skipped too, unless the FK is
   marked on_missing="null".
3. Compute the natural keys (e.g. email, then phone). Primary keys are never
   compared: old and v2 IDs are unrelated.
4. Look the keys up in v2, plus every row already inserted in this run.
   - no match on any key         -> missing: insert it with a new primary key
   - one v2 row matches          -> duplicate: skip it, and map the old ID
                                    to that v2 row so children follow it
   - different v2 rows match     -> ambiguous: skip it, needs a person
   - no usable key (all empty)   -> skip it: cannot tell if it is a duplicate

A duplicate found on a secondary key only (e.g. same phone, different email)
is still treated as a duplicate but marked review=true in the log.
"""
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# ---------------------------------------------------------------- normalizers


def normalize_default(value):
    return "" if value is None else str(value).strip().lower()


def normalize_email(value):
    return "" if value is None else str(value).strip().lower()


def normalize_phone(value):
    """Digits only, last 10. Treats +2348012345678 and 08012345678 as equal."""
    digits = re.sub(r"\D", "", "" if value is None else str(value))
    return digits[-10:] if len(digits) >= 7 else ""


def normalize_company_name(value):
    """Lower case, punctuation dropped, 'limited' spelled 'ltd'."""
    text = re.sub(r"[^a-z0-9]+", " ", normalize_default(value))
    text = re.sub(r"\blimited\b", "ltd", text)
    return " ".join(text.split())


def normalize_registration_number(value):
    """RC 12345, RC-12345 and 12345 compare equal."""
    text = re.sub(r"[^a-z0-9]", "", normalize_default(value))
    return re.sub(r"^rc", "", text)


# ----------------------------------------------------------------- mapping


@dataclass(frozen=True)
class ForeignKey:
    column: str  # v2 column; holds the old parent ID until remapped
    references: str  # v2 table of the parent; must be merged earlier in MAPPINGS
    on_missing: str = "skip"  # "skip" the row, or set the column to "null"


@dataclass(frozen=True)
class TableMap:
    target: str
    source: str
    # v2 column -> old column name, or a function of the whole old row
    columns: dict[str, str | Callable[[dict], Any]]
    # alternatives in priority order; each is a tuple of v2 columns
    natural_keys: list[tuple[str, ...]]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    # v2 NOT NULL columns the old table has no equivalent for
    defaults: dict[str, Any] = field(default_factory=dict)
    normalizers: dict[str, Callable[[Any], str]] = field(default_factory=dict)
    source_pk: str = "id"
    target_pk: str = "id"
    source_where: str | None = None  # optional SQL filter on the old table
    # Optional SELECT that replaces the old table, for rows that need joins
    # (e.g. a licence together with the site its miner owns). `source` is then
    # only a name for the log.
    source_sql: str | None = None
    # Name in the summary and log; defaults to target. Needed when two old
    # tables feed the same v2 table.
    label: str | None = None

    @property
    def name(self):
        return self.label or self.target


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- engine


class ConfigError(Exception):
    pass


class Merger:
    def __init__(self, source, target, mappings, *, execute, log_path, schema="public"):
        self.source = source
        self.target = target
        self.mappings = mappings
        self.execute = execute
        self.schema = schema
        self.log_file = open(log_path, "w")
        self.id_maps: dict[str, dict[str, Any]] = {}
        self.summary: dict[str, dict[str, int]] = {}
        self.target_columns: dict[str, dict[str, dict]] = {}

    def close(self):
        self.log_file.close()

    # -- introspection and validation, before any row is touched

    def _columns(self, conn, table):
        rows = conn.execute(
            """
            SELECT column_name, data_type, is_nullable = 'YES', column_default IS NOT NULL OR is_identity = 'YES'
            FROM information_schema.columns WHERE table_schema = %s AND table_name = %s
            """,
            (self.schema, table),
        ).fetchall()
        return {name: {"type": type_, "nullable": nullable, "has_default": has_default}
                for name, type_, nullable, has_default in rows}

    def _source_columns(self, m):
        if not m.source_sql:
            return self._columns(self.source, m.source)
        cursor = self.source.execute(sql.SQL("SELECT * FROM ({}) s LIMIT 0").format(sql.SQL(m.source_sql)))
        return {col.name: {} for col in cursor.description}

    def validate(self):
        problems = []
        merged = set()
        for m in self.mappings:
            source_cols = self._source_columns(m)
            target_cols = self._columns(self.target, m.target)
            self.target_columns[m.target] = target_cols
            if not source_cols:
                problems.append(f"{m.target}: old table {m.source!r} does not exist")
            if not target_cols:
                problems.append(f"{m.target}: v2 table does not exist")
                continue
            for target_col, source_ref in m.columns.items():
                if target_col not in target_cols:
                    problems.append(f"{m.target}: mapped column {target_col!r} is not in v2")
                if isinstance(source_ref, str) and source_cols and source_ref not in source_cols:
                    problems.append(f"{m.target}.{target_col}: old column {m.source}.{source_ref} does not exist")
            for col in m.defaults:
                if col not in target_cols:
                    problems.append(f"{m.target}: default for {col!r}, which is not in v2")
            if source_cols and m.source_pk not in source_cols:
                problems.append(f"{m.target}: old primary key {m.source}.{m.source_pk} does not exist")
            covered = set(m.columns) | set(m.defaults) | {m.target_pk}
            uncovered = [c for c, info in target_cols.items()
                         if not info["nullable"] and not info["has_default"] and c not in covered]
            if uncovered:
                problems.append(f"{m.target}: NOT NULL v2 columns with no mapping or default: {', '.join(uncovered)}")
            pk = target_cols.get(m.target_pk)
            if pk and pk["type"] != "uuid" and not pk["has_default"]:
                problems.append(f"{m.target}: primary key is {pk['type']} with no default; cannot assign new IDs")
            for key in m.natural_keys:
                for col in key:
                    if col not in target_cols:
                        problems.append(f"{m.target}: natural key column {col!r} is not in v2")
            for fk in m.foreign_keys:
                if fk.references not in merged:
                    problems.append(f"{m.target}.{fk.column}: references {fk.references}, which is not merged before it")
                if fk.column not in m.columns:
                    problems.append(f"{m.target}.{fk.column}: FK column must also be listed in columns")
            merged.add(m.target)
        if problems:
            raise ConfigError("Mapping does not match the databases:\n- " + "\n- ".join(problems))

    # -- matching

    def _keys(self, m, row):
        keys = []
        for index, cols in enumerate(m.natural_keys):
            values = tuple(m.normalizers.get(c, normalize_default)(row.get(c)) for c in cols)
            keys.append((index, values) if all(values) else None)
        return keys

    def _load_index(self, m):
        cols = sorted({c for key in m.natural_keys for c in key})
        query = sql.SQL("SELECT {pk}, {cols} FROM {schema}.{table}").format(
            pk=sql.Identifier(m.target_pk),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            schema=sql.Identifier(self.schema),
            table=sql.Identifier(m.target),
        )
        index = {}
        for record in self.target.execute(query):
            row = dict(zip(cols, record[1:]))
            for key in self._keys(m, row):
                if key is not None:
                    index.setdefault(key, record[0])
        return index

    # -- one table

    def _log(self, **entry):
        self.log_file.write(json.dumps(entry, default=str) + "\n")

    def _insert(self, m, row):
        types = self.target_columns[m.target]
        pk_info = types[m.target_pk]
        values = dict(row)
        if pk_info["type"] == "uuid":
            values[m.target_pk] = uuid.uuid4()
        for col, value in values.items():
            if types[col]["type"] in ("json", "jsonb") and value is not None and not isinstance(value, Jsonb):
                values[col] = Jsonb(value)
        cols = list(values)
        query = sql.SQL("INSERT INTO {schema}.{table} ({cols}) VALUES ({vals}) RETURNING {pk}").format(
            schema=sql.Identifier(self.schema),
            table=sql.Identifier(m.target),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            pk=sql.Identifier(m.target_pk),
        )
        with self.target.transaction():  # savepoint: one bad row never aborts the run
            return self.target.execute(query, [values[c] for c in cols]).fetchone()[0]

    def merge_table(self, m):
        index = self._load_index(m)
        id_map = self.id_maps.setdefault(m.target, {})
        counts = self.summary.setdefault(m.name, {})

        def record(action, source_pk, **extra):
            counts[action] = counts.get(action, 0) + 1
            self._log(table=m.target, mapping=m.name, source_table=m.source, source_pk=source_pk, action=action, **extra)

        if m.source_sql:
            origin = sql.SQL("({}) s").format(sql.SQL(m.source_sql))
        else:
            origin = sql.SQL("{}.{}").format(sql.Identifier(self.schema), sql.Identifier(m.source))
        query = sql.SQL("SELECT * FROM {origin}{where} ORDER BY {pk}").format(
            origin=origin,
            where=sql.SQL(f" WHERE {m.source_where}") if m.source_where else sql.SQL(""),
            pk=sql.Identifier(m.source_pk),
        )
        cursor = self.source.cursor(row_factory=dict_row)
        for old in cursor.execute(query):
            source_pk = str(old[m.source_pk])
            row = {col: (ref(old) if callable(ref) else old[ref]) for col, ref in m.columns.items()}
            for col, default in m.defaults.items():
                if col not in row:
                    row[col] = default(old) if callable(default) else default

            unresolved = None
            for fk in m.foreign_keys:
                if row.get(fk.column) is None:
                    continue
                parent = self.id_maps.get(fk.references, {}).get(str(row[fk.column]))
                if parent is None and fk.on_missing == "null":
                    row[fk.column] = None
                elif parent is None:
                    unresolved = f"{fk.column} -> {fk.references} {row[fk.column]} was not merged"
                    break
                else:
                    row[fk.column] = parent
            if unresolved:
                record("skipped_unresolved_fk", source_pk, reason=unresolved)
                continue

            keys = self._keys(m, row)
            usable = [k for k in keys if k is not None]
            natural = {"/".join(m.natural_keys[k[0]]): list(k[1]) for k in usable}
            if not usable:
                record("skipped_no_natural_key", source_pk, reason="every natural key is empty")
                continue
            matches = {k[0]: index[k] for k in usable if k in index}
            matched_ids = set(map(str, matches.values()))

            if len(matched_ids) > 1:
                record("skipped_ambiguous", source_pk, natural_key=natural,
                       reason="different v2 rows match different keys",
                       candidates={"/".join(m.natural_keys[i]): str(pk) for i, pk in matches.items()})
                continue
            if matched_ids:
                target_pk = next(iter(matches.values()))
                id_map[source_pk] = target_pk
                first = min(matches)
                record("skipped_duplicate", source_pk, target_pk=str(target_pk),
                       matched_on="/".join(m.natural_keys[first]), natural_key=natural,
                       review=(0 not in matches and keys[0] is not None))
                continue

            if not self.execute:
                target_pk = f"new:{m.target}:{source_pk}"
                action = "missing"
            else:
                try:
                    target_pk = self._insert(m, row)
                except psycopg.Error as exc:
                    record("skipped_insert_failed", source_pk, natural_key=natural,
                           reason=str(exc).strip().splitlines()[0])
                    continue
                action = "inserted"
            id_map[source_pk] = target_pk
            for key in usable:
                index.setdefault(key, target_pk)
            record(action, source_pk, target_pk=str(target_pk), natural_key=natural)

    def run(self):
        self.validate()
        for m in self.mappings:
            self.merge_table(m)
        return self.summary


def print_summary(summary, heading):
    print(heading)
    for table, counts in summary.items():
        parts = ", ".join(f"{action}={n}" for action, n in sorted(counts.items()))
        print(f"  {table}: {parts or 'no rows'}")
