import base64
import inspect
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from croniter import croniter  # pip install croniter

from ._client import _RPAMonitorClient


@dataclass
class CommandDef:
    name: str
    func: Callable[..., Any]
    description: str
    args_schema: Dict[str, str]


@dataclass
class ScheduleEntry:
    id: int
    comando: str
    args: Dict[str, Any]
    tipo: str  # "unico" or "recorrente"
    data_hora: Optional[str]        # ISO 8601 for one time schedule
    cron: Optional[str]             # cron expression for recurrent
    ativo: bool
    ultima_execucao: Optional[str]
    proxima_execucao: Optional[str]


class _RPACommandManager:
    """
    Manages:
      - Command registration via decorator
      - Schedule persistence in JSON file
      - Background executor loop
      - Sending REGISTER_COMMANDS / EXEC_RESULT / SCHEDULE_ACK
      - Immediate/manual commands:
          * opcode 05 (COMMAND JSON)
          * opcode 90 (COMMAND_PUSH: cmd:args_b64:execucao_id)
    """

    def __init__(self) -> None:
        self._commands: Dict[str, CommandDef] = {}
        self._client: Optional[_RPAMonitorClient] = None
        self._lock = threading.RLock()

        # Schedules
        self._schedules: Dict[int, ScheduleEntry] = {}
        self._schedules_path: Optional[Path] = None
        self._ultima_sincronizacao: Optional[str] = None
        self._resultados_pendentes: List[Dict[str, Any]] = []

        # Executor
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Integration with client
    # ------------------------------------------------------------------
    def attach_client(self, client: _RPAMonitorClient) -> None:
        """
        Called by __init__.py after the client starts.
        Also triggers REGISTER_COMMANDS and starts scheduler.
        """
        with self._lock:
            self._client = client
            self._init_storage(client.rpa_id)
            self._load_schedules_unlocked()
            self._start_executor_unlocked()

        # Send registered commands
        self._send_register_commands()
        # Try to flush pending results
        self._flush_pending_results()

    def detach_client(self) -> None:
        with self._lock:
            self._client = None
            if self._thread and self._thread.is_alive():
                self._stop_event.set()
                self._thread.join(timeout=5)
            self._thread = None
            self._stop_event.clear()

    # ------------------------------------------------------------------
    # Command decorator
    # ------------------------------------------------------------------
    def command(
        self,
        name: Optional[str] = None,
        args_schema: Optional[Dict[str, str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Decorator @rpa.command("name", args_schema={...})
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or func.__name__
            desc = (func.__doc__ or "").strip()

            with self._lock:
                self._commands[cmd_name] = CommandDef(
                    name=cmd_name,
                    func=func,
                    description=desc,
                    args_schema=args_schema or {},
                )

            # If client is already running, resend REGISTER_COMMANDS
            self._send_register_commands()

            return func

        return decorator

    # ------------------------------------------------------------------
    # Called by client when receiving SCHEDULE_SYNC / COMMAND / COMMAND_PUSH
    # ------------------------------------------------------------------
    def handle_schedule_sync(self, payload: str) -> None:
        """
        Handles JSON payload received via opcode 11 (SCHEDULE_SYNC):

        [
          {
            "id": 123,
            "comando": "processar_notas",
            "args": {"mes": 12, "ano": 2025},
            "tipo": "unico",
            "data_hora": "2025-12-01T08:00:00",
            "cron": null,
            "ativo": true
          },
          ...
        ]
        """
        try:
            data = json.loads(payload)
            if not isinstance(data, list):
                raise ValueError("SCHEDULE_SYNC payload must be a list")

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            with self._lock:
                existentes = self._schedules
                novos: Dict[int, ScheduleEntry] = {}

                for raw in data:
                    sid = int(raw["id"])
                    existente = existentes.get(sid)

                    ultima_exec = existente.ultima_execucao if existente else None
                    proxima_exec = existente.proxima_execucao if existente else None

                    # If server sends proxima_execucao, overwrite
                    proxima_env = raw.get("proxima_execucao")
                    if proxima_env:
                        proxima_exec = proxima_env

                    entry = ScheduleEntry(
                        id=sid,
                        comando=str(raw["comando"]),
                        args=dict(raw.get("args") or {}),
                        tipo=str(raw.get("tipo") or "unico"),
                        data_hora=raw.get("data_hora"),
                        cron=raw.get("cron"),
                        ativo=bool(raw.get("ativo", True)),
                        ultima_execucao=ultima_exec,
                        proxima_execucao=proxima_exec or raw.get("data_hora"),
                    )
                    novos[sid] = entry

                # Any id not sent by server is considered cancelled
                self._schedules = novos
                self._ultima_sincronizacao = now_iso
                self._save_schedules_unlocked()

            # Send SCHEDULE_ACK (opcode 12)
            self._send_schedule_ack()
        except Exception as exc:
            print(f"[rpa-monitor-client] Error in handle_schedule_sync: {exc}")

    def handle_immediate_command(self, payload: str) -> None:
        """
        Handles opcode 05 (COMMAND) as immediate execution (JSON).

        Legacy format:
        {
          "id": 999,
          "comando": "processar_notas",
          "args": {...}
        }

        Optional newer format:
        {
          "id": 999,
          "comando": "processar_notas",
          "args": {...},
          "execucao_id": "abc123"
        }
        """
        try:
            data = json.loads(payload)
            nome = str(data["comando"])
            args = dict(data.get("args") or {})
            agendamento_id = data.get("id") or data.get("agendamento_id")
            execucao_id = data.get("execucao_id")
        except Exception as exc:
            print(f"[rpa-monitor-client] Invalid COMMAND payload: {exc}")
            return

        self._execute_adhoc(
            nome=nome,
            args=args,
            execucao_id=str(execucao_id) if execucao_id is not None else None,
            agendamento_id=int(agendamento_id) if agendamento_id is not None else None,
        )

    def handle_push_command(self, payload: str) -> None:
        """
        Handles opcode 90 (COMMAND_PUSH) with payload:

            NOME_COMANDO:ARGS_BASE64:EXECUCAO_ID

        Examples:

          - Without parameters:
              processar_texto::79

          - With parameters:
              processar_pessoa:eyJub21lIjogIkFuZHJleSIsICJpZGFkZSI6IDMwfQ==:80
        """
        try:
            parts = payload.split(":", 2)
            if len(parts) < 3:
                raise ValueError(
                    "COMMAND_PUSH payload must be "
                    "'comando:args_base64:execucao_id'"
                )

            nome = parts[0].strip()
            args_b64 = parts[1].strip()

            print(f"[DEBUG] payload recebido: '{payload}'")
            print(f"[DEBUG] args_b64: '{args_b64}' (len={len(args_b64)})")
            if args_b64:
                decoded = base64.b64decode(args_b64)
                print(f"[DEBUG] decoded bytes: {decoded}")

            execucao_id = parts[2].strip()

            if not nome:
                raise ValueError("Empty command name in COMMAND_PUSH")

            # Decode args from Base64 -> JSON -> dict
            args = {}
            if args_b64:  # Só tenta decodificar se não estiver vazio
                try:
                    args_bytes = base64.b64decode(args_b64)
                    if args_bytes:  # Só tenta parsear JSON se tiver conteúdo
                        args_json = args_bytes.decode("utf-8")
                        if args_json.strip():  # String não-vazia
                            args = json.loads(args_json)
                            if not isinstance(args, dict):
                                raise ValueError("Args JSON must be an object")
                except Exception as exc:
                    print(
                        "[rpa-monitor-client] Failed to decode/parse args "
                        f"in COMMAND_PUSH: {exc}"
                    )
                    args = {}

            self._execute_adhoc(
                nome=nome,
                args=args,
                execucao_id=execucao_id or None,
                agendamento_id=None,
            )

        except Exception as exc:
            print(f"[rpa-monitor-client] Invalid COMMAND_PUSH payload: {exc}")

    # ------------------------------------------------------------------
    # Schedule persistence
    # ------------------------------------------------------------------
    def _init_storage(self, rpa_id: str) -> None:
        base = Path(os.path.expanduser("~")) / ".rpa_monitor" / rpa_id
        base.mkdir(parents=True, exist_ok=True)
        self._schedules_path = base / "schedules.json"

    def _load_schedules_unlocked(self) -> None:
        if not self._schedules_path or not self._schedules_path.exists():
            self._schedules = {}
            self._ultima_sincronizacao = None
            self._resultados_pendentes = []
            return

        try:
            raw = json.loads(self._schedules_path.read_text(encoding="utf-8"))
        except Exception:
            self._schedules = {}
            self._ultima_sincronizacao = None
            self._resultados_pendentes = []
            return

        ags: Dict[int, ScheduleEntry] = {}
        for item in raw.get("agendamentos", []):
            try:
                entry = ScheduleEntry(
                    id=int(item["id"]),
                    comando=str(item["comando"]),
                    args=dict(item.get("args") or {}),
                    tipo=str(item.get("tipo") or "unico"),
                    data_hora=item.get("data_hora"),
                    cron=item.get("cron"),
                    ativo=bool(item.get("ativo", True)),
                    ultima_execucao=item.get("ultima_execucao"),
                    proxima_execucao=item.get("proxima_execucao"),
                )
                ags[entry.id] = entry
            except Exception:
                continue

        self._schedules = ags
        self._ultima_sincronizacao = raw.get("ultima_sincronizacao")
        self._resultados_pendentes = raw.get("resultados_pendentes", [])

    def _save_schedules_unlocked(self) -> None:
        if not self._schedules_path:
            return
        data = {
            "agendamentos": [asdict(e) for e in self._schedules.values()],
            "ultima_sincronizacao": self._ultima_sincronizacao,
            "resultados_pendentes": self._resultados_pendentes,
        }
        tmp_path = self._schedules_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._schedules_path)

    # ------------------------------------------------------------------
    # Background executor
    # ------------------------------------------------------------------
    def _start_executor_unlocked(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._executor_loop,
            name="rpa-scheduler",
            daemon=True,
        )
        self._thread.start()

    def _executor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                with self._lock:
                    entries = list(self._schedules.values())

                for entry in entries:
                    self._maybe_execute(entry, now)

                # Try to resend pending results
                self._flush_pending_results()

            except Exception as exc:
                print(f"[rpa-monitor-client] Error in scheduler loop: {exc}")

            # Check every 10 seconds
            self._stop_event.wait(10.0)

    def _maybe_execute(self, entry: ScheduleEntry, now: datetime) -> None:
        if not entry.ativo:
            return

        # No next execution defined: initialize
        if not entry.proxima_execucao:
            if entry.tipo == "unico" and entry.data_hora:
                entry.proxima_execucao = entry.data_hora
            elif entry.tipo == "recorrente" and entry.cron:
                proxima = self._calc_next_from_cron(entry.cron, now)
                if not proxima:
                    # invalid cron -> disable this schedule
                    print(
                        "[rpa-monitor-client] Disabling schedule "
                        f"{entry.id} due to invalid cron: {entry.cron}"
                    )
                    entry.ativo = False
                    with self._lock:
                        self._schedules[entry.id] = entry
                        self._save_schedules_unlocked()
                    return
                entry.proxima_execucao = proxima
            else:
                return

        try:
            prox = entry.proxima_execucao
            # Garantir que datetime é timezone-aware
            if prox.endswith("Z"):
                dt_next = datetime.fromisoformat(prox.replace("Z", "+00:00"))
            elif "+" in prox or "-" in prox[10:]:  # Tem offset de timezone
                dt_next = datetime.fromisoformat(prox)
            else:
                # Assume UTC se não tiver timezone
                dt_next = datetime.fromisoformat(prox).replace(tzinfo=timezone.utc)
        except Exception as exc:
            print(
                "[rpa-monitor-client] Invalid proxima_execucao for schedule "
                f"{entry.id}: {entry.proxima_execucao} ({exc})"
            )
            entry.ativo = False
            with self._lock:
                self._schedules[entry.id] = entry
                self._save_schedules_unlocked()
            return

        if dt_next <= now:
            self._execute_entry(entry)

    def _calc_next_from_cron(
        self,
        expr: str,
        base: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        Calculates next datetime from a cron expression.
        Returns None if expression is invalid.
        """
        base_dt = base or datetime.now(timezone.utc)
        try:
            it = croniter(expr, base_dt)
            nxt = it.get_next(datetime)
        except Exception as exc:
            print(
                f"[rpa-monitor-client] Invalid cron expression '{expr}': {exc}"
            )
            return None

        return nxt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------
    # Strict call helper (signature must match)
    # ------------------------------------------------------------------
    def _strict_call(self, func: Callable[..., Any], args: Dict[str, Any]) -> Any:
        """
        Strict rules (no tolerance):

        - If function has no parameters and args != {} -> error
        - If function has required parameters and args == {} -> error
        - If a required parameter is missing -> error
        - If there is an extra parameter -> error
        """
        sig = inspect.signature(func)
        params = sig.parameters

        # No parameters declared
        if len(params) == 0:
            if args:
                raise TypeError("This command does not accept parameters")
            return func()

        # Function has parameters
        required = [
            name for name, p in params.items()
            if p.default is inspect.Parameter.empty
        ]

        # No args and there are required parameters
        if not args and required:
            raise TypeError(
                "Required parameters were not provided: "
                + ", ".join(required)
            )

        # Check required parameters
        for r in required:
            if r not in args:
                raise TypeError(f"Required parameter '{r}' was not provided")

        # Check extra parameters
        for k in args.keys():
            if k not in params:
                raise TypeError(f"Unexpected parameter '{k}'")

        return func(**args)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _execute_entry(self, entry: ScheduleEntry) -> None:
        """
        Executes a scheduled entry and updates schedule state.
        """
        cmd_def = self._commands.get(entry.comando)
        if not cmd_def:
            print(
                "[rpa-monitor-client] Command not registered: "
                f"{entry.comando} (schedule {entry.id})"
            )
            return

        start = time.time()
        status = "sucesso"
        resultado: Any = None
        erro: Optional[str] = None

        try:
            resultado = self._strict_call(cmd_def.func, entry.args)
        except Exception as exc:
            status = "erro"
            erro = f"{type(exc).__name__}: {exc}"

        duracao_ms = int((time.time() - start) * 1000)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Update schedule
        with self._lock:
            if entry.id in self._schedules:
                stored = self._schedules[entry.id]
                stored.ultima_execucao = now_iso
                if stored.tipo == "recorrente" and stored.cron:
                    stored.proxima_execucao = self._calc_next_from_cron(
                        stored.cron,
                        datetime.now(timezone.utc),
                    )
                else:
                    stored.ativo = False
                    stored.proxima_execucao = None
                self._save_schedules_unlocked()

        result_payload = {
            "execucao_id": None,  # execucao_id for schedules is managed on server
            "agendamento_id": entry.id,
            "comando": entry.comando,
            "status": status,
            "resultado": resultado,
            "duracao_ms": duracao_ms,
            "erro": erro,
        }

        ok = self._send_exec_result(result_payload)
        if not ok:
            with self._lock:
                self._resultados_pendentes.append(result_payload)
                self._save_schedules_unlocked()

    def _execute_adhoc(
        self,
        nome: str,
        args: Dict[str, Any],
        execucao_id: Optional[str] = None,
        agendamento_id: Optional[int] = None,
    ) -> None:
        """
        Executes a command without touching schedules (COMMAND / COMMAND_PUSH).
        """
        cmd_def = self._commands.get(nome)
        status = "sucesso"
        resultado: Any = None
        erro: Optional[str] = None

        start = time.time()

        if not cmd_def:
            status = "erro"
            erro = f"Command not registered: {nome}"
        else:
            try:
                resultado = self._strict_call(cmd_def.func, args)
            except Exception as exc:
                status = "erro"
                erro = f"{type(exc).__name__}: {exc}"

        duracao_ms = int((time.time() - start) * 1000)

        result_payload = {
            "execucao_id": execucao_id,
            "agendamento_id": agendamento_id,
            "comando": nome,
            "status": status,
            "resultado": resultado,
            "duracao_ms": duracao_ms,
            "erro": erro,
        }

        ok = self._send_exec_result(result_payload)
        if not ok:
            with self._lock:
                self._resultados_pendentes.append(result_payload)
                self._save_schedules_unlocked()

    # ------------------------------------------------------------------
    # Sending 10, 12, 13
    # ------------------------------------------------------------------
    def _send_register_commands(self) -> None:
        client = self._client
        if not client:
            return

        with self._lock:
            cmds = [
                {
                    "nome": c.name,
                    "descricao": c.description,
                    "args_schema": c.args_schema,
                }
                for c in self._commands.values()
            ]

        try:
            payload = json.dumps(cmds, ensure_ascii=True)
            client._send_message(op="10", nivel="INFO", payload=payload)
        except Exception as exc:
            print(f"[rpa-monitor-client] Failed to send REGISTER_COMMANDS: {exc}")

    def _send_schedule_ack(self) -> None:
        client = self._client
        if not client:
            return

        with self._lock:
            ids = list(self._schedules.keys())
            ts = self._ultima_sincronizacao

        payload = {
            "ids": ids,
            "ultima_sincronizacao": ts,
        }

        try:
            data = json.dumps(payload, ensure_ascii=True)
            client._send_message(op="12", nivel="INFO", payload=data)
        except Exception as exc:
            print(f"[rpa-monitor-client] Failed to send SCHEDULE_ACK: {exc}")

    def _send_exec_result(self, payload: Dict[str, Any]) -> bool:
        client = self._client
        if not client:
            return False

        try:
            data = json.dumps(payload, ensure_ascii=True)
            client._send_message(op="13", nivel="INFO", payload=data)
            return True
        except Exception as exc:
            print(f"[rpa-monitor-client] Failed to send EXEC_RESULT: {exc}")
            return False

    def _flush_pending_results(self) -> None:
        client = self._client
        if not client:
            return

        with self._lock:
            pendentes = list(self._resultados_pendentes)

        if not pendentes:
            return

        enviados: List[Dict[str, Any]] = []
        for item in pendentes:
            if self._send_exec_result(item):
                enviados.append(item)

        if enviados:
            with self._lock:
                self._resultados_pendentes = [
                    it for it in self._resultados_pendentes if it not in enviados
                ]
                self._save_schedules_unlocked()


# Global manager
_manager = _RPACommandManager()


class _RPACommandAPI:
    """
    Public API:

        from rpa_monitor_client import rpa

        @rpa.command("name", args_schema={...})
        def my_func(...):
            ...
    """

    def command(
        self,
        name: Optional[str] = None,
        args_schema: Optional[Dict[str, str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return _manager.command(name=name, args_schema=args_schema)

    # Internal: called by core
    def _attach_client(self, client: _RPAMonitorClient) -> None:
        _manager.attach_client(client)

    def _detach_client(self) -> None:
        _manager.detach_client()

    # Called by client when receiving messages
    def _handle_schedule_sync(self, payload: str) -> None:
        _manager.handle_schedule_sync(payload)

    def _handle_immediate_command(self, payload: str) -> None:
        _manager.handle_immediate_command(payload)

    def _handle_push_command(self, payload: str) -> None:
        _manager.handle_push_command(payload)


rpa = _RPACommandAPI()
