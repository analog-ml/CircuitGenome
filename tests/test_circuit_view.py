"""Tests for the shared structural view."""
import pytest

from circuitgenome.sizer.shared.circuit_view import adoption_warnings


def test_no_warning_when_nothing_was_adopted():
    assert adoption_warnings([]) == []


def test_adoption_warning_names_every_device_and_its_slot():
    (w,) = adoption_warnings([("m2_load", "load"), ("m1_load", "load")])
    assert "2 MOSFET(s) unplaced" in w
    # refs are sorted so the advisory is stable across runs
    assert "m1_load, m2_load -> 'load'" in w
    assert "recognizer gap" in w


def test_adoption_warning_groups_by_slot():
    (w,) = adoption_warnings([("m1_load", "load"), ("m9_bias_gen", "bias_gen")])
    assert "m9_bias_gen -> 'bias_gen'" in w and "m1_load -> 'load'" in w
    assert w.index("bias_gen") < w.index("m1_load")  # slots sorted
