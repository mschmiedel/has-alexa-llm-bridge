from abc import ABC, abstractmethod
from typing import List, Any, Dict

class HandlerResult:
    def __init__(self, text: str, should_end_session: bool = True, session_attributes: Dict[str, Any] = None):
        self.text = text
        self.should_end_session = should_end_session
        self.session_attributes = session_attributes or {}

class BaseHandler(ABC):
    @abstractmethod
    async def execute(self, parameters: List[Any], ha_service: Any, session_attributes: Dict[str, Any] = None, intent_name: str = None) -> HandlerResult:
        pass
