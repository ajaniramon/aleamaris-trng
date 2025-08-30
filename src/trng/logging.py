"""Utilidades de logging para AleaMaris.

Logger raíz "aleamaris" con:
- Formato estilo log4j (por defecto): "YYYY-MM-DD HH:MM:SS,mmm LEVEL logger - mensaje [pid=.., rid=..] k=v ..."
- Opción JSON (opcional vía env)
- Handler a consola (stderr)
- Handler a fichero con rotación por tamaño

Incluye un filtro que inyecta un request_id (si existe) desde un contextvar
para correlacionar peticiones. Evita loguear bytes crudos; usa métricas.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

import contextvars

# Contexto por petición/hilo
request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class _ContextFilter(logging.Filter):
    """Inyecta campos de contexto (request_id, pid) en cada registro."""

    def __init__(self, include_pid: bool = True) -> None:
        super().__init__()
        self.include_pid = include_pid

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            rid = request_id_ctx.get()
        except Exception:
            rid = None
        setattr(record, "request_id", rid)
        if self.include_pid:
            setattr(record, "pid", os.getpid())
        return True


def _iso8601_now() -> str:
    # Timestamp ISO8601 con milisegundos y zona Z (UTC)
    t = time.time()
    ms = int((t - int(t)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{ms:03d}Z"


class JSONLogFormatter(logging.Formatter):
    """Formateador JSON estable, sin bytes crudos."""

    def __init__(self, include_source: bool = True) -> None:
        super().__init__()
        self.include_source = include_source

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: Dict[str, Any] = {
            "ts": _iso8601_now(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # extras conocidos
        if hasattr(record, "request_id") and getattr(record, "request_id") is not None:
            payload["request_id"] = getattr(record, "request_id")
        if hasattr(record, "pid"):
            payload["pid"] = getattr(record, "pid")
        # source opcional
        if self.include_source:
            payload["source"] = f"{record.pathname}:{record.lineno}"
        # extra fields (si el caller pasó extra={...})
        for k, v in record.__dict__.items():
            if k in ("msg", "args", "levelname", "levelno", "name", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                     "created", "msecs", "relativeCreated", "thread", "threadName",
                     "processName", "process", "request_id", "pid"):
                continue
            # sólo serializables sencillos
            if isinstance(v, (str, int, float, bool)) or v is None:
                payload.setdefault("extra", {})[k] = v
        # excepción si aplica
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Formato estilo log4j con extras k=v y pid/rid opcionales."""

    def __init__(self) -> None:
        # YYYY-MM-DD HH:MM:SS,mmm LEVEL logger - message
        fmt = "%(asctime)s,%(msecs)03d %(levelname)s %(name)s - %(message)s"
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        base = super().format(record)
        # Sufijo con pid/rid
        rid = getattr(record, "request_id", None)
        pid = getattr(record, "pid", None)
        suffix = []
        if pid is not None:
            suffix.append(f"pid={pid}")
        if rid:
            suffix.append(f"rid={rid}")
        # Añadir pares k=v de extras simples
        extra_parts = []
        for k, v in record.__dict__.items():
            if k in ("msg", "args", "levelname", "levelno", "name", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                     "created", "msecs", "relativeCreated", "thread", "threadName",
                     "processName", "process", "request_id", "pid", "asctime"):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                extra_parts.append(f"{k}={v}")
        if suffix:
            base += " [" + ", ".join(suffix) + "]"
        if extra_parts:
            base += " " + " ".join(extra_parts)
        return base


def setup_logging() -> logging.Logger:
    """Configura logging global para AleaMaris.

    Variables de entorno:
    - ALEAMARIS_LOG_LEVEL (DEBUG|INFO|WARNING|ERROR)
    - ALEAMARIS_LOG_FILE (ruta; por defecto logs/aleamaris.log)
    - ALEAMARIS_LOG_JSON (1/0) para usar JSON en ambos handlers
    - ALEAMARIS_LOG_MAX_BYTES (rotación)
    - ALEAMARIS_LOG_BACKUPS (copias)
    - ALEAMARIS_LOG_INCLUDE_SOURCE (1/0) añade file:line
    - ALEAMARIS_LOG_INCLUDE_PID (1/0)
    """
    level_str = os.getenv("ALEAMARIS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)
    log_file = os.getenv("ALEAMARIS_LOG_FILE", os.path.join("logs", "aleamaris.log"))
    json_console = os.getenv("ALEAMARIS_LOG_JSON", "0").lower() in ("1", "true", "yes")
    max_bytes = int(os.getenv("ALEAMARIS_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backups = int(os.getenv("ALEAMARIS_LOG_BACKUPS", "5"))
    include_source = os.getenv("ALEAMARIS_LOG_INCLUDE_SOURCE", "1").lower() in ("1", "true", "yes")
    include_pid = os.getenv("ALEAMARIS_LOG_INCLUDE_PID", "1").lower() in ("1", "true", "yes")

    root = logging.getLogger("aleamaris")
    # Evitar duplicados si se llama 2 veces
    if getattr(root, "_configured", False):
        return root

    root.setLevel(level)

    # Filtro de contexto
    ctx_filter = _ContextFilter(include_pid=include_pid)

    # Handler consola
    sh = logging.StreamHandler(stream=sys.stderr)
    sh.setLevel(level)
    sh.addFilter(ctx_filter)
    sh.setFormatter(JSONLogFormatter(include_source=include_source) if json_console else ConsoleFormatter())
    root.addHandler(sh)

    # Handler fichero con rotación
    try:
        Path(os.path.dirname(log_file) or ".").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        fh.setLevel(level)
        fh.addFilter(ctx_filter)
        fh.setFormatter(JSONLogFormatter(include_source=include_source) if json_console else ConsoleFormatter())
        root.addHandler(fh)
    except Exception as e:
        # Si no podemos abrir fichero, al menos consola
        root.error("No se pudo inicializar log a fichero", extra={"error": str(e), "file": log_file})

    # No propagar al root global de Python
    root.propagate = False
    setattr(root, "_configured", True)
    root.info("logging inicializado", extra={
        "log_level": level_str,
        "log_file": log_file,
        "json_console": json_console,
        "max_bytes": max_bytes,
        "backups": backups,
    })
    return root


def get_logger(name: str) -> logging.Logger:
    """Obtiene un logger hijo de aleamaris."""
    base = logging.getLogger("aleamaris")
    return base.getChild(name)
