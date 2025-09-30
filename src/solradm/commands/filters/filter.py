from abc import abstractmethod, ABC
from typing import List


class Filter(ABC):
    @abstractmethod
    def init(self):
        pass

    def describe(self) -> List[str]:
        """Return human-friendly explanations of the applied filter."""
        return []
