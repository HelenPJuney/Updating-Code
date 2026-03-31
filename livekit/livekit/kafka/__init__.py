
print("[FILE] Entering:Kafka __init__.py")
from .producer import CallRequestProducer, get_producer   # noqa: F401

__all__ = ["CallRequestProducer", "get_producer"]
print("[FILE] Exit: Kafka__init__.py")