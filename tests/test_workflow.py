import unittest

import support
from commitment_report.errors import ConflictError, LockedError
from commitment_report.workflow import ChapterState


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.svc = support.make_service()
        self.svc.create_chapter("CH-ENERGY", "能源部门进展", "sectoral", ["IND-ENERGY"])
        self.svc.create_chapter("CH-INDUSTRY", "工业部门进展", "sectoral", ["IND-INDUSTRY"])
        self.svc.create_chapter("CH-NATIONAL", "国家汇总", "national")

    def test_full_review_cycle_with_return_and_resubmit(self):
        svc = self.svc
        svc.submit_chapter("CH-ENERGY")
        svc.start_review("CH-ENERGY")
        svc.add_review_comment("CH-ENERGY", "审阅人甲", "请补充煤炭分项的来源页码")
        svc.return_chapter("CH-ENERGY")
        self.assertEqual(svc.chapters.get("CH-ENERGY").state, ChapterState.RETURNED)

        svc.submit_chapter("CH-ENERGY")  # 退回后重新提交
        chapter = svc.chapters.get("CH-ENERGY")
        self.assertEqual(chapter.state, ChapterState.SUBMITTED)
        self.assertEqual(chapter.round, 2)
        self.assertEqual(len(chapter.comments), 1)  # 历史审阅意见保留
        self.assertEqual(chapter.comments[0].round, 1)

    def test_stage_lock_locks_only_ready_chapters_of_that_stage(self):
        self.svc.submit_chapter("CH-ENERGY")
        self.svc.submit_chapter("CH-NATIONAL")
        # CH-INDUSTRY 仍是草稿
        locked = self.svc.lock_stage("sectoral")
        self.assertEqual([c.chapter_id for c in locked], ["CH-ENERGY"])
        self.assertEqual(
            self.svc.chapters.get("CH-ENERGY").state, ChapterState.LOCKED
        )
        self.assertEqual(
            self.svc.chapters.get("CH-INDUSTRY").state, ChapterState.DRAFT
        )
        self.assertEqual(
            self.svc.chapters.get("CH-NATIONAL").state, ChapterState.SUBMITTED
        )

    def test_locked_chapter_rejects_changes(self):
        self.svc.submit_chapter("CH-ENERGY")
        self.svc.lock_chapter("CH-ENERGY")
        with self.assertRaises(LockedError):
            self.svc.add_review_comment("CH-ENERGY", "审阅人乙", "锁定后意见")
        with self.assertRaises((LockedError, ConflictError)):
            self.svc.return_chapter("CH-ENERGY")

    def test_draft_chapter_cannot_be_locked_or_returned(self):
        with self.assertRaises(ConflictError):
            self.svc.lock_chapter("CH-ENERGY")
        with self.assertRaises(ConflictError):
            self.svc.return_chapter("CH-ENERGY")


if __name__ == "__main__":
    unittest.main()
