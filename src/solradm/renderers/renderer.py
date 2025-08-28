from abc import abstractmethod
from typing import ContextManager


class Renderer(ContextManager):
    @abstractmethod
    def stop(self):
        pass