"""Sample menus enforce reading gates and immutable factual measurements."""

import copy
import unittest

from seam import Fault

from glass.actors import SECOND
from glass.choices import sample_choices
from glass.personas import mixed_population


def request():
    actor = mixed_population(211)[0]
    return {
        "actor_id": actor["id"],
        "profile": actor["profile"],
        "memory": actor["memory"],
        "at_ns": 0,
        "observation": {"status": None, "retested": False},
    }


class SampleChoiceTests(unittest.TestCase):
    def test_submit_and_terminal_finish_are_the_only_valid_initial_end_states(self):
        value = request()
        original = copy.deepcopy(value)
        menu = sample_choices(value)
        self.assertEqual([item["id"] for item in menu], ["submit"])
        self.assertEqual(menu[0]["decision"]["wake_after_ns"], 0)
        self.assertEqual(value, original)
        value["memory"] = menu[0]["decision"]["memory"]
        for status in ("released", "scrapped", "dropped", "overdue", "report_failed"):
            value["observation"]["status"] = status
            self.assertEqual([item["id"] for item in sample_choices(value)], ["finish"])

    def test_reading_is_not_offered_before_deadline_and_defer_does_not_reset_it(self):
        value = request()
        value["memory"] = sample_choices(value)[0]["decision"]["memory"]
        value["observation"]["status"] = "running"
        early = sample_choices(value)
        self.assertEqual([item["id"] for item in early], ["wait"])
        self.assertEqual(early[0]["decision"]["wake_after_ns"], SECOND)
        value["memory"] = early[0]["decision"]["memory"]
        value["at_ns"] = SECOND - 1
        self.assertEqual(sample_choices(value)[0]["decision"]["wake_after_ns"], 1)
        value["at_ns"] = SECOND
        ready = sample_choices(value)
        self.assertEqual([item["id"] for item in ready], ["read", "wait"])
        self.assertEqual(
            ready[0]["decision"]["action"]["body"]["value"], value["profile"]["value"]
        )
        self.assertEqual(ready[1]["decision"]["memory"]["ready_at_ns"], SECOND)

    def test_retest_requires_new_delay_and_third_reading_is_forbidden(self):
        value = request()
        value["observation"] = {"status": "running", "retested": True}
        value["memory"] = {"phase": "wait", "readings": 1, "ready_at_ns": None}
        value["at_ns"] = 10 * SECOND
        menu = sample_choices(value)
        self.assertEqual([item["id"] for item in menu], ["wait"])
        self.assertEqual(menu[0]["decision"]["memory"]["ready_at_ns"], 11 * SECOND)
        value["memory"]["readings"] = 2
        with self.assertRaises(Fault):
            sample_choices(value)

    def test_queued_and_reporting_waits_always_advance_time(self):
        value = request()
        value["memory"]["phase"] = "wait"
        for status in ("queued", "received", "retest", "report_pending"):
            value["observation"]["status"] = status
            menu = sample_choices(value)
            self.assertEqual([item["id"] for item in menu], ["wait"])
            self.assertGreater(menu[0]["decision"]["wake_after_ns"], 0)
