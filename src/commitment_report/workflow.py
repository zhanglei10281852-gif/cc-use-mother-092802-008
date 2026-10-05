"""章节编制工作流：分阶段锁定、审阅意见、退回重报。

状态机：
    DRAFT → SUBMITTED → UNDER_REVIEW → LOCKED
                          ↓（退回）
                        RETURNED →（重新提交，轮次 +1）→ SUBMITTED
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .errors import ConflictError, LockedError, NotFoundError


class ChapterState(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    LOCKED = "locked"
    RETURNED = "returned"


@dataclass(frozen=True)
class ReviewComment:
    comment_id: str
    chapter_id: str
    round: int
    author: str
    body: str
    created_at: datetime
    resolved: bool = False


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    title: str
    stage: str  # 所属阶段，用于分阶段锁定
    state: ChapterState
    round: int  # 提交轮次，退回重报时递增
    indicator_ids: tuple[str, ...]
    comments: tuple[ReviewComment, ...] = ()


class ChapterStore:
    def __init__(self) -> None:
        self._chapters: dict[str, Chapter] = {}
        self._comment_seq = 0

    def create(
        self, chapter_id: str, title: str, stage: str, indicator_ids: tuple[str, ...] = ()
    ) -> Chapter:
        if chapter_id in self._chapters:
            raise ConflictError(f"章节已存在: {chapter_id}")
        chapter = Chapter(chapter_id, title, stage, ChapterState.DRAFT, 0, indicator_ids)
        self._chapters[chapter_id] = chapter
        return chapter

    def get(self, chapter_id: str) -> Chapter:
        try:
            return self._chapters[chapter_id]
        except KeyError:
            raise NotFoundError(f"章节不存在: {chapter_id}") from None

    def all(self) -> list[Chapter]:
        return [self._chapters[k] for k in sorted(self._chapters)]

    # -- 状态迁移 -----------------------------------------------------------

    def _update(self, chapter_id: str, **changes) -> Chapter:
        chapter = self.get(chapter_id)
        if chapter.state is ChapterState.LOCKED:
            raise LockedError(f"章节已锁定，拒绝修改: {chapter_id}")
        updated = replace(chapter, **changes)
        self._chapters[chapter_id] = updated
        return updated

    def submit(self, chapter_id: str) -> Chapter:
        chapter = self.get(chapter_id)
        if chapter.state is ChapterState.DRAFT:
            return self._update(chapter_id, state=ChapterState.SUBMITTED, round=1)
        if chapter.state is ChapterState.RETURNED:
            # 退回内容重新提交，轮次递增，历史审阅意见保留
            return self._update(
                chapter_id, state=ChapterState.SUBMITTED, round=chapter.round + 1
            )
        raise ConflictError(f"章节 {chapter_id} 当前状态 {chapter.state.value} 不可提交")

    def start_review(self, chapter_id: str) -> Chapter:
        chapter = self.get(chapter_id)
        if chapter.state is not ChapterState.SUBMITTED:
            raise ConflictError(f"章节 {chapter_id} 未处于待审状态")
        return self._update(chapter_id, state=ChapterState.UNDER_REVIEW)

    def add_comment(
        self, chapter_id: str, author: str, body: str, when: datetime
    ) -> ReviewComment:
        chapter = self.get(chapter_id)
        if chapter.state is ChapterState.LOCKED:
            raise LockedError(f"章节已锁定，拒绝修改: {chapter_id}")
        if chapter.state not in (ChapterState.SUBMITTED, ChapterState.UNDER_REVIEW):
            raise ConflictError(f"章节 {chapter_id} 当前状态不可记录审阅意见")
        self._comment_seq += 1
        comment = ReviewComment(
            comment_id=f"C-{self._comment_seq:04d}",
            chapter_id=chapter_id,
            round=chapter.round,
            author=author,
            body=body,
            created_at=when,
        )
        self._update(chapter_id, comments=chapter.comments + (comment,))
        return comment

    def return_chapter(self, chapter_id: str) -> Chapter:
        chapter = self.get(chapter_id)
        if chapter.state is not ChapterState.UNDER_REVIEW:
            raise ConflictError(f"章节 {chapter_id} 未在审阅中，不可退回")
        return self._update(chapter_id, state=ChapterState.RETURNED)

    def lock(self, chapter_id: str) -> Chapter:
        chapter = self.get(chapter_id)
        if chapter.state is ChapterState.LOCKED:
            return chapter
        if chapter.state not in (ChapterState.SUBMITTED, ChapterState.UNDER_REVIEW):
            raise ConflictError(
                f"章节 {chapter_id} 状态为 {chapter.state.value}，须先提交并通过审阅"
            )
        updated = replace(chapter, state=ChapterState.LOCKED)
        self._chapters[chapter_id] = updated
        return updated

    def lock_stage(self, stage: str) -> list[Chapter]:
        """分阶段锁定：锁定该阶段全部已提交/在审章节。"""
        locked: list[Chapter] = []
        for chapter in self.all():
            if chapter.stage != stage:
                continue
            if chapter.state in (ChapterState.SUBMITTED, ChapterState.UNDER_REVIEW):
                locked.append(self.lock(chapter.chapter_id))
        return locked
