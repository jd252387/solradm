from abc import abstractmethod, ABC


class Filter(ABC):
    @abstractmethod
    def init(self):
        pass
