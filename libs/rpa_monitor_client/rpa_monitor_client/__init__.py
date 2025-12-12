# rpa_monitor_client/__init__.py
from typing import Optional

from ._client import _RPAMonitorClient
from ._config import RPAConfig, load_from_env
from ._logging_api import rpa_log, set_client
from ._commands import rpa

__all__ = [
    "setup_rpa_monitor",
    "auto_setup_rpa_monitor",
    "shutdown_rpa_monitor",
    "rpa_log",
    "rpa",
]

_client: Optional[_RPAMonitorClient] = None


def _create_client_from_config(cfg: RPAConfig) -> _RPAMonitorClient:
    return _RPAMonitorClient(
        rpa_id=cfg.rpa_id,
        host=cfg.host,
        port=cfg.port,
        region=cfg.region,
        heartbeat_interval=cfg.heartbeat_interval,
        transport=cfg.transport,
    )


def setup_rpa_monitor(
    rpa_id: str,
    host: str,
    port: Optional[int] = None,
    region: str = "default",
    heartbeat_interval: int = 5,
    transport: str = "tcp",
) -> None:
    """
    Configuração manual do cliente de monitoramento.
    """
    global _client
    if _client is not None:
        return

    cfg = RPAConfig(
        rpa_id=rpa_id,
        host=host,
        port=port,
        region=region,
        heartbeat_interval=heartbeat_interval,
        transport=transport,
    )
    client = _create_client_from_config(cfg)
    ok = client.start()
    if not ok:
        raise RuntimeError("Não foi possível inicializar o cliente de monitoramento")

    _client = client
    set_client(client)
    rpa._attach_client(client)


def auto_setup_rpa_monitor() -> None:
    """
    Configuração automática a partir de variáveis de ambiente.
    """
    global _client
    if _client is not None:
        return

    cfg = load_from_env()
    client = _create_client_from_config(cfg)
    ok = client.start()
    if not ok:
        raise RuntimeError("Não foi possível inicializar o cliente de monitoramento")

    _client = client
    set_client(client)
    rpa._attach_client(client)


def shutdown_rpa_monitor() -> None:
    """
    Finaliza o cliente de monitoramento.
    """
    global _client
    if _client is not None:
        _client.stop()
        _client = None
        set_client(None)  # remove do rpa_log
        rpa._detach_client()
