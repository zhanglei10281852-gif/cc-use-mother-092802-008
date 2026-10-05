"""服务层异常。"""

from __future__ import annotations


class NotFoundError(KeyError):
    """引用的实体或版本不存在。"""


class ConflictError(ValueError):
    """状态冲突，例如重复提交或版本号不连续。"""


class LockedError(ConflictError):
    """章节或报告版本已锁定，拒绝修改。"""
