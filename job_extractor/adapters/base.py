from abc import ABC, abstractmethod
from job_extractor.models import CollectionResult
class BaseAdapter(ABC):
    platform_name: str
    priority: int = 100

    @classmethod
    @abstractmethod
    def match(cls, url: str) -> bool: ...
    @abstractmethod
    def collect(self, url: str) -> CollectionResult: ...
