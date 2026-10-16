"""定长缓存：超过容量时丢掉最久没用过的一项。"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, Optional, TypeVar

V = TypeVar("V")


class LruDict(Generic[V]):
    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, capacity)
        self._data: OrderedDict[str, V] = OrderedDict()

    def get(self, key: str) -> Optional[V]:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: str, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def pop(self, key: str) -> Optional[V]:
        return self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)
