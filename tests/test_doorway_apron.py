import numpy as np
import pytest

from survai.apron import ApronColor, EmployeeLabels
from survai.doorway import Crossing, DoorCounter
from survai.identity import PersonIdentities, center
from survai.tracks import Track


def test_door_counts_both_directions_without_jitter_or_lost_track_crossings():
    door = DoorCounter(((0.5, 0.8), (0.5, 0.2)))  # Inside is to the right.
    door.update({1: (0.4, 0.5), 2: (0.4, 0.1), 3: (0.4, 0.5)}, 0)
    door.update({1: (0.499, 0.5), 2: (0.6, 0.1)}, 0.1)
    door.update({1: (0.508, 0.5)}, 0.2)
    assert (door.came_in, door.left) == (0, 0)
    assert door.update({1: (0.6, 0.5)}, 0.3) == [Crossing(1, "came in", 1, 0)]
    door.update({1: (0.601, 0.5), 3: (0.6, 0.5)}, 0.9)
    assert (door.came_in, door.left) == (1, 0)
    assert door.update({1: (0.4, 0.5)}, 1.0) == [Crossing(1, "left", 1, 1)]
    door.update({1: (0.39, 0.5)}, 1.1)
    assert (door.came_in, door.left) == (1, 1)


@pytest.mark.parametrize(("boxes", "entries"), [
    # The first two positions were previously discarded by track confirmation.
    ([(.93, 1.0), (.88, .99), (.83, .96)], 1),
    # The first box is clipped by the exterior frame edge and already has its center inside.
    ([(.81, 1.0), (.77, .99), (.73, .97)], 1),
    # A person already inside who moves away from the doorway is not an entry.
    ([(.81, .97), (.77, .95), (.73, .92)], 0),
    # A clipped stationary person does not count as an entry.
    ([(.81, 1.0), (.81, 1.0), (.81, 1.0)], 0),
])
def test_new_track_counts_entry_only_with_a_doorway_approach(boxes, entries):
    door = DoorCounter(((.92, .2), (.92, .85)))
    tracker = PersonIdentities(1.5, .08, 5)
    frame = np.zeros((180, 320, 3), np.uint8)

    for tick, (left, right) in enumerate(boxes):
        now = tick / 12
        visible = tracker.update(frame, [Track(42, "person", (left, .2, right, .8))], now)
        door.update_tracks(visible, tracker.confirmed_paths, lambda track: center(track.box), now)
        if tick < 2:
            assert (door.came_in, door.left) == (0, 0)

    assert (door.came_in, door.left) == (entries, 0)
    if entries:
        for tick, (left, right) in enumerate([(.87, .99), (.92, 1.0), (.93, 1.0)], start=3):
            now = tick / 12
            visible = tracker.update(frame, [Track(42, "person", (left, .2, right, .8))], now)
            door.update_tracks(visible, tracker.confirmed_paths, lambda track: center(track.box), now)
        assert (door.came_in, door.left) == (1, 1)


def test_apron_match_requires_torso_coverage_and_consistent_observations():
    frame = np.full((200, 200, 3), 135, np.uint8)
    employee = Track(1, "person", (0, 0, 0.5, 1))
    guest = Track(2, "person", (0.5, 0, 1, 1))
    apron = ApronColor("#dd3020")
    frame[65:163, 25:75] = (32, 48, 221)
    frame[65:100, 125:175] = (32, 48, 221)  # A matching accent on the shirt.
    assert apron.matches(frame, employee) is True
    assert apron.matches(frame, guest) is False
    labels = EmployeeLabels()
    for tick in range(3):
        labels.update({employee.id: True, guest.id: False}, tick * 0.1)
    assert labels.employees == {employee.id}
    labels.update({employee.id: False}, 0.3)
    labels.update({}, 1)
    assert labels.employees == {employee.id}
    labels.update({}, 6)
    assert not labels.employees


def test_rgb_phone_marker_and_track_label():
    import cv2

    from survai.employee_marker import PhoneMarker

    image = np.zeros((240, 240, 3), np.uint8)
    for index, color in enumerate(((0, 0, 245), (0, 240, 0), (230, 0, 0))):
        image[75 + index * 16:89 + index * 16, 42:70] = color
    # Another person has the same colors, arranged horizontally.
    for index, color in enumerate(((0, 0, 245), (0, 240, 0), (230, 0, 0))):
        image[80:95, 146 + index * 20:163 + index * 20] = color
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    employee = Track(1, "person", (0, 0, 0.5, 1))
    customer = Track(2, "person", (0.5, 0, 1, 1))
    marker = PhoneMarker()
    assert marker.matches(hsv, employee)
    assert not marker.matches(hsv, customer)
    labels = EmployeeLabels(sticky=True)
    for tick in (0, 0.1, 0.2):
        labels.update({1: True, 2: False}, tick)
    assert labels.employees == {1}
    for tick in (1, 2, 3, 4, 5, 6):
        labels.update({1: False, 2: False}, tick)
    assert labels.employees == {1}
    labels.update({2: False}, 12)
    assert not labels.employees
