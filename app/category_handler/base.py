from abc import ABC, abstractmethod


class BaseHandler(ABC):
    @abstractmethod
    async def execute(self, user_query, ha_service):
        pass
