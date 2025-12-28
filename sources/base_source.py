from abc import ABC, abstractmethod
from typing import Optional, Tuple, List
import aiohttp
from ..main import Book, SearchResult

class BaseSource(ABC):
    """数据源基类，定义通用接口"""
    
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[SearchResult]:
        """搜索书籍"""
        pass

    @abstractmethod
    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        pass

    @abstractmethod
    def get_search_type(self) -> str:
        """获取搜索类型标识"""
        pass