import dataclasses


@dataclasses.dataclass
class Context:
    name: str | None
    zk: str
    kubecontext: str | None = None
    namespace: str | None = None

    def as_dict(self) -> dict:
        dumped = {"name": self.name, "zk": self.zk}
        if self.kubecontext is not None:
            dumped["kubecontext"] = self.kubecontext
        if self.namespace is not None:
            dumped["namespace"] = self.namespace
        return dumped
