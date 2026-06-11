from abc import ABC, abstractmethod
import json
from dataclasses import asdict

from sash.reporter import Report


class Formatter(ABC):
    @abstractmethod
    def format(self, report: Report) -> str: ...


class DefaultFormatter(Formatter):
    def format(self, report: Report) -> str:
        return NotImplemented


class JSONFormatter(Formatter):
    def __init__(self, indent: int = 2):
        self.indent = indent

    def format(self, report: Report) -> str:
        return json.dumps(
            asdict(report),
            indent=self.indent,
            default=str
        )


class CompactFormatter(Formatter):
    def format(self, report: Report) -> str:
        return NotImplemented
