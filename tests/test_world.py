import time

from conftest import FLOOR_FEET, MON, make, snap, step, feet
from stickfigure.world.geometry import Rect, subtract_span, visible_top_edges

# -- geometry ------------------------------------------------------------------


def test_subtract_span_splits():
    assert subtract_span([(0, 100)], (40, 60)) == [(0, 40), (60, 100)]
    assert subtract_span([(0, 100)], (-10, 200)) == []
    assert subtract_span([(0, 100)], (100, 200)) == [(0, 100)]


def test_upper_window_occludes_lower_edge():
    top = Rect(100, 300, 500, 700)
    lower = Rect(0, 400, 800, 900)
    edges = visible_top_edges([top, lower], [MON], headroom=80)
    assert edges[0] == [(100, 500)]
    assert edges[1] == [(0, 100), (500, 800)]


def test_window_below_upper_does_not_occlude():
    upper = Rect(0, 100, 400, 200)  # ends well above the lower edge's headroom
    lower = Rect(0, 500, 400, 800)
    assert visible_top_edges([upper, lower], [MON], headroom=80)[1] == [(0, 400)]


def test_maximized_edge_has_no_headroom():
    maxed = Rect(0, 0, 1920, 1040)
    assert visible_top_edges([maxed], [MON], headroom=80) == [[]]


def test_edge_clipped_to_screen():
    off = Rect(1800, 500, 2400, 900)
    assert visible_top_edges([off], [MON], headroom=80) == [[(1800, 1920)]]


# -- physics -------------------------------------------------------------------

WIN = Rect(700, 600, 1300, 1000)


def test_falls_to_taskbar_floor():
    world, fig = make()
    step(world, fig, 2)
    assert abs(feet(fig) - FLOOR_FEET) < 3
    assert fig.grounded


def test_lands_on_window_and_is_carried():
    world, fig = make(WIN)
    fig.body.position = (1000, 400)
    step(world, fig, 2)
    assert abs(feet(fig) - (600 - 3)) < 3
    assert world.carry_target is not None

    x0 = fig.body.position.x
    world.sync(snap(WIN.translated(150, -100)), fig.body)
    assert abs(fig.body.position.x - (x0 + 150)) < 1e-6
    step(world, fig, 0.5)
    assert abs(feet(fig) - (500 - 3)) < 3


def test_jumps_up_through_platform():
    world, fig = make(WIN)
    fig.body.position = (1000, 800)  # below the window's top edge
    fig.body.velocity = (0, -1800)
    step(world, fig, 2)
    assert abs(feet(fig) - (600 - 3)) < 3  # rose through, landed on top


def test_walking_under_a_ledge_does_not_pop_onto_it():
    ledge = Rect(900, FLOOR_FEET - 70, 1400, 1040)  # top edge 70px above the floor
    world, fig = make(ledge)
    fig.body.position = (600, 900)
    step(world, fig, 1)
    fig.walk_to(1100)
    step(world, fig, 5)
    assert abs(fig.body.position.x - 1100) < 5
    assert abs(feet(fig) - FLOOR_FEET) < 3


def test_drop_through_platform():
    world, fig = make(WIN)
    fig.body.position = (1000, 400)
    step(world, fig, 2)
    assert fig.drop_through()
    step(world, fig, 2)
    assert abs(feet(fig) - FLOOR_FEET) < 3


def test_falls_when_window_disappears():
    world, fig = make(WIN)
    fig.body.position = (1000, 400)
    step(world, fig, 2)
    world.sync(snap(), fig.body)
    step(world, fig, 2)
    assert abs(feet(fig) - FLOOR_FEET) < 3


def test_hard_throw_tumbles_and_knocks_down():
    world, fig = make()
    fig.body.position = (400, 500)
    fig.grab((400, 500))
    t = time.perf_counter()
    fig._cursor_samples.extend([(t - 0.03, 400, 500), (t, 460, 470)])
    fig.release()
    assert fig.tumbling
    step(world, fig, 2.0)
    assert fig.grounded and not fig.tumbling
    assert fig.knocked > 0


def test_throw_velocity_is_capped():
    world, fig = make()
    fig.grab((500, 500))
    t = time.perf_counter()
    fig._cursor_samples.extend([(t - 0.03, 0, 500), (t, 900, 500)])
    fig.release()
    assert abs(fig.body.velocity.length - fig.cfg.max_throw_speed) < 1
