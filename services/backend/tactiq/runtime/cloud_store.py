"""Application queries over the bounded PostgreSQL runtime, never a SQLite shim."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .access import AnalysisReservation
from .cloud_worker import ExecutionClaim
from .contracts import Aggregate, MAX_RECORD_BYTES, MediaRef, RevisionConflictError, bounded_json, cloud_identifier
from .postgres import PostgresStore


class CloudApplicationStore(PostgresStore):
    def commit(self, tenant_id: str, match_id: str, state: dict, **kwargs):
        claim = kwargs.get("claim")
        if claim is not None and claim.job_id != match_id:
            raise RevisionConflictError("Execution does not own this analysis")
        return super().commit(tenant_id, match_id, state, **kwargs)

    def migrate(self) -> None:
        super().migrate()
        with self._transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(854917020603)")
            connection.execute(Path(__file__).with_name("migrations").joinpath("003_application.sql").read_text())

    def healthcheck(self) -> None:
        with self._transaction() as connection:
            runtime = connection.execute("SELECT version FROM tactiq_cloud_schema WHERE singleton").fetchone()
            application = connection.execute("SELECT singleton FROM tactiq_cloud_application_schema").fetchone()
            access = connection.execute("SELECT version FROM tactiq_access_schema WHERE singleton").fetchone()
            connection.execute("SELECT approved_limit FROM tactiq_cloud_processing_window LIMIT 1").fetchone()
            connection.execute("SELECT script_id FROM tactiq_cloud_scripts LIMIT 1").fetchone()
            connection.execute("SELECT operation_id FROM tactiq_cloud_generation_results LIMIT 1").fetchone()
            if not runtime or runtime["version"] != 1 or not application or not access or access["version"] != 2:
                raise RuntimeError("Cloud migrations are incomplete or unsupported")

    def admit(
        self, connection: Any, reservation: AnalysisReservation, kind: str, payload: dict,
        *, locator: str | None = None, waiting_input: bool = False,
    ) -> None:
        """Called inside PostgresAccessStore's quota+ownership transaction."""
        tenant, match = reservation.tenant_id, reservation.analysis_id
        self._scope(tenant, match)
        encoded = bounded_json(payload, MAX_RECORD_BYTES)
        inserted = connection.execute(
            """INSERT INTO tactiq_cloud_jobs(tenant_id,job_id,kind,payload,max_attempts)
               VALUES (%s,%s,%s,%s::jsonb,3) ON CONFLICT DO NOTHING RETURNING job_id""",
            (tenant, match, kind, encoded),
        ).fetchone()
        if inserted is None:
            row = connection.execute(
                "SELECT kind,payload FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s",
                (tenant, match),
            ).fetchone()
            if row != {"kind": kind, "payload": json.loads(encoded)}:
                raise RevisionConflictError("Analysis retry does not match admitted work")
            return
        state = {
            "match_id": match, "tenant_id": tenant, "status": "created",
            "scenario_id": payload.get("scenario_id", "video_upload" if payload.get("analysis_mode") == "events" else "cv_upload"),
            "scenario_recipe_version": payload.get("recipe_version", 1), "seed": payload.get("seed", 0),
            "score": {} if kind != "scenario" else {"team_a": 0, "team_b": 0},
            "match_clock_ms": 0, "last_sequence": 0,
            "team_display_names": payload.get("team_display_names") or {"team_a": "Team A", "team_b": "Team B"},
            "_cloud": {"input_ready": not waiting_input, "event_count": 0, "replay_count": 0},
        }
        connection.execute(
            """INSERT INTO tactiq_cloud_matches(tenant_id,match_id,revision,state)
               VALUES (%s,%s,1,%s::jsonb)""", (tenant, match, json.dumps(state)),
        )
        if locator is not None:
            if not isinstance(locator, str) or not 1 <= len(locator) <= 2048:
                raise ValueError("Invalid private source locator")
            connection.execute(
                "INSERT INTO tactiq_cloud_inputs(tenant_id,match_id,locator) VALUES (%s,%s,%s)",
                (tenant, match, locator),
            )
        if not waiting_input:
            self._enqueue(connection, tenant, match)

    def save_script(self, tenant_id: str, match_id: str, script_id: str, payload: dict) -> dict:
        self._scope(tenant_id, match_id)
        cloud_identifier(script_id, "script_id")
        encoded = bounded_json(payload, MAX_RECORD_BYTES)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO tactiq_cloud_scripts VALUES (%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING""", (tenant_id, match_id, script_id, encoded),
            )
            return connection.execute(
                """SELECT payload FROM tactiq_cloud_scripts
                   WHERE tenant_id=%s AND match_id=%s AND script_id=%s""",
                (tenant_id, match_id, script_id),
            ).fetchone()["payload"]

    def load_script(self, tenant_id: str, match_id: str, script_id: str) -> dict | None:
        self._scope(tenant_id, match_id)
        cloud_identifier(script_id, "script_id")
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT payload FROM tactiq_cloud_scripts
                   WHERE tenant_id=%s AND match_id=%s AND script_id=%s""",
                (tenant_id, match_id, script_id),
            ).fetchone()
            return row["payload"] if row else None

    def generation_result(self, tenant_id: str, match_id: str, operation_id: str) -> dict | None:
        self._scope(tenant_id, match_id)
        cloud_identifier(operation_id, "operation_id")
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT payload FROM tactiq_cloud_generation_results
                   WHERE tenant_id=%s AND match_id=%s AND operation_id=%s""",
                (tenant_id, match_id, operation_id),
            ).fetchone()
            return row["payload"] if row else None

    def save_generation_result(self, tenant_id: str, match_id: str, operation_id: str, payload: dict) -> None:
        self._scope(tenant_id, match_id)
        cloud_identifier(operation_id, "operation_id")
        encoded = bounded_json(payload, MAX_RECORD_BYTES)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO tactiq_cloud_generation_results VALUES (%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING""", (tenant_id, match_id, operation_id, encoded),
            )

    def job(self, tenant_id: str, job_id: str) -> dict | None:
        self._scope(tenant_id, job_id)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT job_id,status,result,error,attempts,max_attempts
                   FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s""", (tenant_id, job_id),
            ).fetchone()
        if row is not None:
            row["match_id"] = job_id
        return row

    def list_matches(self, tenants: tuple[str, ...], *, after: str = "", limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 100 or not 1 <= len(tenants) <= 100:
            raise ValueError("Invalid history page")
        for tenant in tenants:
            cloud_identifier(tenant, "tenant_id")
        if after:
            cloud_identifier(after, "after")
        with self._transaction() as connection:
            return connection.execute(
                """SELECT match_id,revision,jsonb_build_object(
                       'match_id',match_id,'tenant_id',tenant_id,'status',state->>'status',
                       'scenario_id',state->>'scenario_id','scenario_recipe_version',state->'scenario_recipe_version',
                       'seed',state->'seed','score',state->'score','match_clock_ms',state->'match_clock_ms',
                       'last_sequence',state->'last_sequence','team_display_names',state->'team_display_names'
                   ) AS state FROM tactiq_cloud_matches
                   WHERE tenant_id=ANY(%s) AND match_id>%s ORDER BY match_id LIMIT %s""",
                (list(tenants), after, limit),
            ).fetchall()

    def load_owned(self, tenants: tuple[str, ...], match_id: str) -> Aggregate | None:
        cloud_identifier(match_id, "match_id")
        if not 1 <= len(tenants) <= 100:
            raise ValueError("Workspace membership exceeds the supported bound")
        for tenant in tenants:
            cloud_identifier(tenant, "tenant_id")
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT tenant_id,revision,state FROM tactiq_cloud_matches
                   WHERE tenant_id=ANY(%s) AND match_id=%s LIMIT 2""", (list(tenants), match_id),
            ).fetchall()
        if len(row) > 1:
            raise RevisionConflictError("Ambiguous analysis ownership")
        return None if not row else Aggregate(row[0]["tenant_id"], match_id, row[0]["revision"], row[0]["state"])

    def input_locator(self, claim: ExecutionClaim, owner: str) -> str:
        with self._transaction() as connection:
            self._fence(connection, claim, owner)
            row = connection.execute(
                "SELECT locator FROM tactiq_cloud_inputs WHERE tenant_id=%s AND match_id=%s",
                (claim.tenant_id, claim.job_id),
            ).fetchone()
            if row is None:
                raise ValueError("Source locator is unavailable")
            return row["locator"]

    def replay_at(self, tenant_id: str, match_id: str, at_ms: int) -> tuple[dict, str | None] | None:
        self._scope(tenant_id, match_id)
        if type(at_ms) is not int or not 0 <= at_ms <= 2**63 - 1:
            raise ValueError("Invalid source clock")
        with self._transaction() as connection:
            frame = connection.execute(
                """SELECT payload FROM tactiq_cloud_records
                   WHERE tenant_id=%s AND match_id=%s AND stream='replay'
                   AND (payload->>'timestamp_ms')::bigint<=%s
                   ORDER BY (payload->>'timestamp_ms')::bigint DESC LIMIT 1""",
                (tenant_id, match_id, at_ms),
            ).fetchone()
            if frame is None:
                return None
            event = connection.execute(
                """SELECT record_id FROM tactiq_cloud_records
                   WHERE tenant_id=%s AND match_id=%s AND stream='events' AND position=%s""",
                (tenant_id, match_id, frame["payload"]["sequence"]),
            ).fetchone()
            return frame["payload"], event["record_id"] if event else None

    def attach_input(self, reference: MediaRef) -> bool:
        """Only one validated upload wins; register, mark ready and enqueue atomically."""
        from .blob import validate_reference

        validate_reference(reference)
        with self._transaction() as connection:
            job = connection.execute(
                """SELECT status FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s FOR UPDATE""",
                (reference.tenant_id, reference.match_id),
            ).fetchone()
            row = connection.execute(
                "SELECT state FROM tactiq_cloud_matches WHERE tenant_id=%s AND match_id=%s FOR UPDATE",
                (reference.tenant_id, reference.match_id),
            ).fetchone()
            if job is None or row is None:
                raise ValueError("Admitted upload is unavailable")
            if row["state"]["_cloud"]["input_ready"]:
                return False
            if job["status"] != "queued":
                raise RevisionConflictError("Upload was cancelled")
            self._register(connection, reference)
            state = row["state"]
            state["_cloud"].update(input_ready=True, input_media_id=reference.media_id)
            connection.execute(
                """UPDATE tactiq_cloud_matches SET state=%s::jsonb,revision=revision+1
                   WHERE tenant_id=%s AND match_id=%s""",
                (json.dumps(state), reference.tenant_id, reference.match_id),
            )
            self._enqueue(connection, reference.tenant_id, reference.match_id)
            return True

    @staticmethod
    def _register(connection: Any, reference: MediaRef) -> None:
        connection.execute(
            """INSERT INTO tactiq_cloud_media(tenant_id,match_id,media_id,size,content_type,etag)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (reference.tenant_id, reference.match_id, reference.media_id,
             reference.size, reference.content_type, reference.etag),
        )

    def register_output(self, reference: MediaRef, claim: ExecutionClaim, owner: str) -> None:
        from .blob import validate_reference

        validate_reference(reference)
        if (reference.tenant_id, reference.match_id) != (claim.tenant_id, claim.job_id):
            raise ValueError("Output does not belong to this execution")
        with self._transaction() as connection:
            self._fence(connection, claim, owner)
            self._register(connection, reference)
            self._fence(connection, claim, owner)

    def _fence(self, connection: Any, claim: ExecutionClaim, owner: str) -> None:
        if connection.execute(
            """SELECT job_id FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s
               AND fence=%s AND lease_owner=%s AND status='running' AND NOT cancel_requested
               AND lease_until>clock_timestamp() FOR UPDATE""", self._claim_params(claim, owner),
        ).fetchone() is None:
            raise RevisionConflictError("Execution lease was lost")

    def pause(self, tenant_id: str, job_id: str) -> bool:
        self._scope(tenant_id, job_id)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='paused',fence=fence+1,
                   lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND status IN ('queued','running') RETURNING job_id""",
                (tenant_id, job_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """UPDATE tactiq_cloud_matches SET state=state||'{"status":"paused"}'::jsonb,
                       revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=%s AND match_id=%s""",
                    (tenant_id, job_id),
                )
            return row is not None

    def resume(self, tenant_id: str, job_id: str) -> bool:
        self._scope(tenant_id, job_id)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='queued',attempts=0,
                   cancel_requested=false,error=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND status='paused' RETURNING job_id""",
                (tenant_id, job_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """UPDATE tactiq_cloud_matches SET state=state||'{"status":"created","error":null}'::jsonb,
                       revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=%s AND match_id=%s""",
                    (tenant_id, job_id),
                )
                ready = connection.execute(
                    """SELECT (state->'_cloud'->>'input_ready')::boolean AS ready
                       FROM tactiq_cloud_matches WHERE tenant_id=%s AND match_id=%s""", (tenant_id, job_id),
                ).fetchone()
                if ready is not None and ready["ready"]:
                    self._enqueue(connection, tenant_id, job_id)
            return row is not None

    def request_cancel(self, tenant_id: str, job_id: str) -> bool:
        self._scope(tenant_id, job_id)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET cancel_requested=true,status='cancelled',fence=fence+1,
                   lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND status IN ('queued','running','paused')
                   RETURNING job_id""", (tenant_id, job_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """UPDATE tactiq_cloud_matches
                       SET state=state||'{"status":"failed","error":"analysis_cancelled"}'::jsonb,
                       revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=%s AND match_id=%s""",
                    (tenant_id, job_id),
                )
            return row is not None

    def terminal(self, reference) -> bool:
        row = self.job(reference.tenant_id, reference.job_id)
        return row is None or row["status"] in {"succeeded", "cancelled", "dead_letter", "paused"}

    def recover(self, limit: int = 100) -> int:
        """Repair broker loss/expired deliveries; fenced claims absorb duplicate hints."""
        if not 1 <= limit <= 100:
            raise ValueError("Invalid recovery batch")
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT j.tenant_id,j.job_id FROM tactiq_cloud_jobs j
                   JOIN tactiq_cloud_matches m ON (m.tenant_id=j.tenant_id AND m.match_id=j.job_id)
                   WHERE (j.status='queued' OR (j.status='running' AND j.lease_until<=clock_timestamp()))
                   AND (m.state->'_cloud'->>'input_ready')::boolean
                   AND NOT EXISTS (SELECT 1 FROM tactiq_cloud_outbox o
                       WHERE o.tenant_id=j.tenant_id AND o.job_id=j.job_id AND o.status='pending')
                   ORDER BY j.updated_at LIMIT %s FOR UPDATE OF j SKIP LOCKED""", (limit,),
            ).fetchall()
            for row in rows:
                self._enqueue(connection, row["tenant_id"], row["job_id"])
            return len(rows)

    def media_page(self, tenant_id: str, match_id: str, *, after: str = "", limit: int = 100) -> list[MediaRef]:
        self._scope(tenant_id, match_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid media page size")
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT media_id,size,content_type,etag FROM tactiq_cloud_media
                   WHERE tenant_id=%s AND match_id=%s AND media_id>%s ORDER BY media_id LIMIT %s""",
                (tenant_id, match_id, after, limit),
            ).fetchall()
            return [MediaRef(tenant_id, match_id, **row) for row in rows]

    def delete_expired(self, tenant_id: str, match_id: str) -> None:
        """System retention only, after AccessStore atomically expires ownership."""
        self._scope(tenant_id, match_id)
        with self._transaction() as connection:
            connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='cancelled',fence=fence+1,
                   cancel_requested=true,lease_owner=NULL,lease_until=NULL WHERE tenant_id=%s AND job_id=%s""",
                (tenant_id, match_id),
            )
            connection.execute("DELETE FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s", (tenant_id, match_id))
            connection.execute(
                "DELETE FROM tactiq_cloud_matches WHERE tenant_id=%s AND match_id=%s", (tenant_id, match_id),
            )
