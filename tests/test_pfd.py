"""Hand-worked pfd geometry and limits. Numbers in comments were calculated separately from the code."""
import json

import pytest

from ific_guard.engines.pfd import (
    MASK_DIR,
    GroundPoint,
    evaluate_gso,
    finland_points,
    gso_geometry,
    load_masks,
    pfd_db,
    spreading_loss_db,
    x_db,
    y_db,
)

MASKS = load_masks()


def mask(mask_id):
    return MASKS.by_id(mask_id)


# ---- geometry. Re = 6378 km, Rs = 42164 km, so Re^2 = 40 678 884, Rs^2 = 1 777 802 896, 2ReRs = 537 843 984


def test_satellite_on_finlands_longitude_at_60n():
    # cos(psi) = cos 60 x cos 0 = 0.5. d^2 = 40 678 884 + 1 777 802 896 - 537 843 984 x 0.5 = 1 549 559 788
    # d = 39 364.45 km. Elevation = atan((0.5 - 0.151266) / sin 60) = atan(0.348734 / 0.866025) = 21.93 deg
    g = gso_geometry(60, 25, 25)
    assert g.distance_km == pytest.approx(39364.45, abs=0.01)
    assert g.angle_deg == pytest.approx(21.934, abs=0.001)


def test_satellite_on_finlands_longitude_in_the_far_north():
    # cos(psi) = cos 70 = 0.342020. d^2 = 1 818 481 780 - 537 843 984 x 0.342020 = 1 634 528 303, d = 40 429.30 km
    # Elevation = atan((0.342020 - 0.151266) / sin 70) = atan(0.190754 / 0.939693) = 11.475 deg
    g = gso_geometry(70, 25, 25)
    assert g.distance_km == pytest.approx(40429.30, abs=0.01)
    assert g.angle_deg == pytest.approx(11.475, abs=0.001)


def test_satellite_near_the_edge_of_visibility():
    # 72 deg away at 60N: cos(psi) = 0.5 x cos 72 = 0.154508, just above Re/Rs = 0.151266
    # d^2 = 1 818 481 780 - 537 843 984 x 0.154508 = 1 735 380 314, d = 41 657.90 km, elevation 0.188 deg
    g = gso_geometry(60, 25, 97)
    assert g.distance_km == pytest.approx(41657.90, abs=0.01)
    assert g.angle_deg == pytest.approx(0.188, abs=0.001)


def test_satellite_below_the_horizon_is_not_a_number():
    # 73 deg away at 60N: cos(psi) = 0.146186, below Re/Rs = 0.151266
    assert gso_geometry(60, 25, 98) is None
    result = evaluate_gso(98, -50, 40, mask("art21-8025-8500"), [GroundPoint(60, 25)])
    assert result.visible is False
    assert result.worst is None
    assert result.above_limit is False
    assert result.points_visible == 0


def test_directly_under_the_satellite_looks_straight_up():
    g = gso_geometry(0, 25, 25)
    assert g.angle_deg == pytest.approx(90)
    assert g.distance_km == pytest.approx(42164 - 6378)


# ---- pfd


def test_spreading_loss():
    # 10 log10(4 pi x (39 364 450 m)^2) = 10 log10(1.9472e16) = 162.894 dB
    assert spreading_loss_db(39364.45) == pytest.approx(162.894, abs=0.001)
    with pytest.raises(ValueError):
        spreading_loss_db(0)


def test_pfd_in_a_reference_bandwidth():
    # -54 dBW/Hz + 60 dB (1 MHz) + 50 dBi - 162.894 dB = -106.894
    assert pfd_db(-54, 1e6, 50, 39364.45) == pytest.approx(-106.894, abs=0.001)
    # -50 dBW/Hz + 36.021 dB (4 kHz) + 5 dBi - 162.894 dB = -171.873
    assert pfd_db(-50, 4000, 5, 39364.45) == pytest.approx(-171.873, abs=0.001)


def test_a_narrow_emission_is_capped_at_its_total_power():
    # Density alone would give -30 + 60 = +30 dBW in 1 MHz, but the carrier only has 2 dBW in total
    assert pfd_db(-30, 1e6, 0, 1000, peak_power_dbw=2) == pytest.approx(2 - spreading_loss_db(1000))
    # When density gives less than the total power, the cap changes nothing
    assert pfd_db(-50, 4000, 0, 1000, peak_power_dbw=2) == pytest.approx(pfd_db(-50, 4000, 0, 1000))


def test_evaluation_gives_the_margin_at_the_worst_point():
    # 17.7-19.3 GHz at 70N: pfd = -54 + 60 + 50 - 163.126 = -107.126. Limit = -115 + 0.5 x (11.475 - 5) = -111.762
    # Margin +4.637 at 70N. At 60N: pfd = -106.894, limit = -106.533, margin -0.361. So 70N is the worst.
    points = [GroundPoint(60, 25, "south"), GroundPoint(70, 25, "north")]
    result = evaluate_gso(25, -54, 50, mask("art21-17700-19300"), points)
    assert result.worst.label == "north"
    assert result.worst.value_db == pytest.approx(-107.126, abs=0.001)
    assert result.worst.limit_db == pytest.approx(-111.762, abs=0.001)
    assert result.worst.margin_db == pytest.approx(4.637, abs=0.002)
    assert result.above_limit is True
    assert result.points_evaluated == result.points_visible == 2
    assert result.calc_inputs["gain_dbi"] == 50
    assert result.citation["clause"].startswith("Art. 21")
    assert any("Free-space" in a for a in result.assumptions)
    assert json.dumps(result.to_dict())


def test_exactly_at_the_limit_is_not_above_it():
    m = mask("art21-8025-8500")
    point = GroundPoint(60, 25)
    g = gso_geometry(60, 25, 25)
    # Choose a gain that lands exactly on the limit
    gain = m.limit_db(g.angle_deg) - pfd_db(-50, m.ref_bw_hz, 0, g.distance_km)
    result = evaluate_gso(25, -50, gain, m, [point])
    assert result.worst.margin_db == pytest.approx(0, abs=1e-9)
    assert result.to_dict()["worst"]["margin_db"] <= 1e-9


def test_rerunning_gives_identical_numbers():
    a = evaluate_gso(33.5, -54, 50, mask("art21-17700-19300"))
    b = evaluate_gso(33.5, -54, 50, mask("art21-17700-19300"))
    assert a.to_dict() == b.to_dict()


# ---- limits, read from printed pages 287-290 of RR 2024 Vol. 1 (Table 21-4)
# (mask, [(angle, expected)]) at each breakpoint, between them, and beyond
TABLE_21_4 = [
    ("art21-1525-1530", [(0, -154), (5, -154), (15, -149), (25, -144), (90, -144)]),
    ("art21-2025-2110", [(0, -154), (5, -154), (15, -149), (25, -144), (90, -144)]),
    ("art21-2200-2300", [(0, -154), (5, -154), (15, -149), (25, -144), (90, -144)]),
    ("art21-2500-2690", [(0, -136), (5, -136), (15, -130.5), (25, -125), (90, -125)]),
    ("art21-3400-4200-gso", [(0, -152), (5, -152), (15, -147), (25, -142), (90, -142)]),
    ("art21-8025-8500", [(0, -150), (5, -150), (15, -145), (25, -140), (90, -140)]),
    ("art21-10700-11700-gso", [(0, -150), (5, -150), (15, -145), (25, -140), (90, -140)]),
    ("art21-10700-11700-ngso", [(0, -126), (5, -126), (15, -121), (25, -116), (90, -116)]),
    ("art21-10700-12500-ngso-heo", [(0, -129), (5, -129), (15, -121.5), (25, -114), (90, -114)]),
    ("art21-11700-12500-ngso", [(0, -124), (5, -124), (15, -119), (25, -114), (90, -114)]),
    ("art21-17700-19300", [(0, -115), (5, -115), (15, -110), (25, -105), (90, -105)]),
    ("art21-17700-19300-heo", [(0, -120), (3, -120), (12, -112), (25, -105), (90, -105)]),
    ("art21-19300-19700", [(0, -115), (5, -115), (15, -110), (25, -105), (90, -105)]),
    ("art21-19300-19700-heo", [(0, -120), (3, -120), (12, -112), (25, -105), (90, -105)]),
]


@pytest.mark.parametrize("mask_id, expected", TABLE_21_4)
def test_article_21_limits_match_the_printed_table(mask_id, expected):
    for angle, limit in expected:
        assert mask(mask_id).limit_db(angle) == pytest.approx(limit), f"{mask_id} at {angle} deg"


@pytest.mark.parametrize("angle", [4.999, 5.001, 24.999, 25.001])
def test_limits_have_no_jump_at_the_breakpoints(angle):
    m = mask("art21-8025-8500")
    assert abs(m.limit_db(angle) - m.limit_db(round(angle))) < 0.01


def test_angle_outside_the_range_is_refused():
    with pytest.raises(ValueError):
        mask("art21-8025-8500").limit_db(-0.1)
    with pytest.raises(ValueError):
        mask("art21-8025-8500").limit_db(90.1)


def test_reference_bandwidths_match_the_printed_table():
    four_khz = ["art21-2200-2300", "art21-3400-4200-gso", "art21-8025-8500", "art21-10700-11700-gso"]
    one_mhz = ["art21-2500-2690", "art21-3400-4200-ngso", "art21-10700-11700-ngso", "art21-17700-19300", "art21-19300-19700"]
    assert {mask(i).ref_bw_hz for i in four_khz} == {4000}
    assert {mask(i).ref_bw_hz for i in one_mhz} == {1e6}


def test_x_parameter_for_17_7_to_19_3_ghz_non_gso():
    # RR 21.16.6: X = 0 for N <= 50; 5/119 x (N - 50) to 288; 1/69 x (N + 402) to 999; then from Nv
    assert x_db(50) == 0
    assert x_db(288) == pytest.approx(10.0)  # 5/119 x 238
    assert x_db(289) == pytest.approx(691 / 69)
    assert x_db(999) == pytest.approx(1401 / 69)
    assert x_db(1000, n_visible=50) == pytest.approx(20.3)  # 10 log 50 = 17.0 is below the 20.3 floor
    assert x_db(1000, n_visible=1000) == pytest.approx(30)
    assert x_db(6001, n_visible=1000) == pytest.approx(31)
    with pytest.raises(ValueError, match="Nv"):
        x_db(1000)
    m = mask("art21-17700-19300-ngso-x")
    # -115 - X up to 5 deg; -115 - X + ((10 + X) / 20)(d - 5) to 25 deg; -105 above. With X = 10:
    assert m.limit_db(0, 10) == pytest.approx(-125)
    assert m.limit_db(5, 10) == pytest.approx(-125)
    assert m.limit_db(15, 10) == pytest.approx(-115)
    assert m.limit_db(25, 10) == pytest.approx(-105)
    assert m.limit_db(90, 10) == pytest.approx(-105)


def test_y_parameter_for_3_4_to_4_2_ghz_non_gso():
    # RR 21.16.15: Y = 0 for max(NN, NS) <= 2, otherwise 5 log(max(NN, NS))
    assert y_db(2) == 0
    assert y_db(100) == pytest.approx(10)
    m = mask("art21-3400-4200-ngso")
    # -138 - Y up to 5 deg; -138 - Y + (12 + Y)(d - 5)/20 to 25 deg; -126 above. With Y = 10:
    assert m.limit_db(5, 10) == pytest.approx(-148)
    assert m.limit_db(15, 10) == pytest.approx(-137)
    assert m.limit_db(25, 10) == pytest.approx(-126)
    assert m.limit_db(90, 10) == pytest.approx(-126)


def test_a_parameter_mask_refuses_to_guess_its_parameter():
    with pytest.raises(ValueError, match="needs X"):
        mask("art21-17700-19300-ngso-x").limit_db(10)


# ---- Appendix 5 is a coordination trigger, labelled apart from limits. Printed p. 165, notes p. 166


def test_appendix_5_is_kept_separate_from_article_21():
    for m in MASKS.masks:
        if m.mask_id.startswith("art21"):
            assert m.kind == "pfd_art21" and "21.16" in m.citation["clause"]
        else:
            assert m.kind == "pfd_app5" and "Appendix 5" in m.citation["clause"]
    assert MASKS.select(1525, 1530, "pfd_app5", "GSO") != MASKS.select(1525, 1530, "pfd_art21", "GSO")


@pytest.mark.parametrize(
    "mask_id, p, slope",
    [
        ("app5-1525-1530-gso-other", -128, 0.5),
        ("app5-1525-1530-gso-analogue-4k", -146, 0.5),
        ("app5-2160-2200-gso-other", -128, 0.5),
        ("app5-2160-2200-ngso-other", -123, 0.5),
        ("app5-2160-2200-ngso-analogue-4k", -141, 0.5),
        ("app5-2483-2500-mss-gso-4k", -146, 0.5),
    ],
)
def test_appendix_5_thresholds_follow_note_2(mask_id, p, slope):
    m = mask(mask_id)
    assert m.limit_db(0) == pytest.approx(p)
    assert m.limit_db(5) == pytest.approx(p)
    assert m.limit_db(15) == pytest.approx(p + 10 * slope)
    assert m.limit_db(25) == pytest.approx(p + 20 * slope)
    assert m.limit_db(90) == pytest.approx(p + 20 * slope)


def test_note_9_replaces_the_table_body_for_finland():
    # NOTE 9 lists Finland: MSS -142.5 in 4 kHz and -124.5 in 1 MHz; RDSS -152 in 4 kHz and -128 in 1 MHz
    assert mask("app5-2483-2500-mss-ngso-4k").limit_db(0) == -142.5
    assert mask("app5-2483-2500-mss-ngso-1m").limit_db(0) == -124.5
    assert mask("app5-2483-2500-mss-ngso-4k").limit_db(25) == pytest.approx(-142.5 + 20 * 0.65)
    assert mask("app5-2483-2500-rdss-ngso-4k").limit_db(45) == -152
    assert mask("app5-2483-2500-rdss-ngso-1m").limit_db(45) == -128


# ---- choosing a mask


def ids(*args, **kwargs):
    return {m.mask_id for m in MASKS.select(*args, **kwargs)}


def test_selection_at_band_edges():
    assert ids(8400, 8500, "pfd_art21", "GSO") == {"art21-8025-8500"}
    assert ids(8500, 8600, "pfd_art21", "GSO") == set()  # touching the edge is not an overlap
    assert ids(7900, 8025, "pfd_art21", "GSO") == set()
    assert ids(8024, 8026, "pfd_art21", "GSO") == {"art21-8025-8500"}


def test_selection_splits_gso_from_non_gso():
    assert ids(3500, 3600, "pfd_art21", "GSO") == {"art21-3400-4200-gso"}
    assert ids(3500, 3600, "pfd_art21", "NGSO") == {"art21-3400-4200-ngso"}


def test_selection_splits_non_gso_rows_by_orbit_shape():
    # RR 21.16.17 and 21.16.18: inclination 35-145 deg with apogee above 18 000 km, or not
    leo = ids(11000, 11100, "pfd_art21", "NGSO", inclination_deg=53, apogee_km=550)
    heo = ids(11000, 11100, "pfd_art21", "NGSO", inclination_deg=63.4, apogee_km=39000)
    unknown = ids(11000, 11100, "pfd_art21", "NGSO")
    assert leo == {"art21-10700-11700-ngso"}
    assert heo == {"art21-10700-12500-ngso-heo"}
    assert unknown == leo | heo


def test_the_strict_highly_inclined_row_never_applies_to_gso():
    assert ids(18000, 18100, "pfd_art21", "GSO") == {"art21-17700-19300"}
    assert ids(19500, 19600, "pfd_art21", "GSO") == {"art21-19300-19700"}


def test_no_mask_for_finland_is_distinguished_from_not_transcribed():
    assert ids(12600, 12700, "pfd_art21", "GSO") == set()
    assert {g["status"] for g in MASKS.gaps(12600, 12700)} == {"no_limit_for_finland"}
    assert ids(11800, 11900, "pfd_art21", "GSO") == set()
    assert "no_limit_for_finland" in {g["status"] for g in MASKS.gaps(11800, 11900)}
    assert ids(7300, 7400, "pfd_art21", "GSO") == set()
    assert {g["status"] for g in MASKS.gaps(7300, 7400)} == {"not_covered"}


def test_priority_bands_are_covered():
    for low, high in [(3400, 4200), (8025, 8500), (10700, 11700), (17700, 19300), (19300, 19700), (2025, 2110), (2200, 2300)]:
        assert MASKS.select(low, high, "pfd_art21", "GSO"), f"{low}-{high}"


def test_every_mask_is_well_formed_and_cited():
    raw = json.loads((MASK_DIR / "limit_masks.json").read_text(encoding="utf-8"))
    assert len({m["mask_id"] for m in raw["masks"]}) == len(raw["masks"])
    assert {s["sha256"] for s in raw["sources"]["RR"]}
    for m in MASKS.masks:
        angles = [b[0] for b in m.breakpoints]
        assert angles[0] == 0 and angles[-1] == 90 and angles == sorted(angles), m.mask_id
        assert m.citation["doc"] == "RR" and m.citation["clause"] and m.citation["printed_page"], m.mask_id
        assert m.unit == "dB(W/m2)" and m.ref_bw_hz in (4000, 1e6) and m.services, m.mask_id


# ---- Finland


def test_finland_points_cover_the_country_and_nothing_far_outside():
    points = finland_points()
    lats = [p.lat_deg for p in points]
    lons = [p.lon_deg for p in points]
    assert min(lats) == pytest.approx(59.816, abs=0.01)
    assert max(lats) == pytest.approx(70.065, abs=0.01)  # the far north must be in, where GSO arrives lowest
    assert min(lons) == pytest.approx(19.519, abs=0.01)  # Åland
    assert max(lons) == pytest.approx(31.537, abs=0.01)
    labels = {p.label for p in points}
    assert labels == {"grid", "boundary"}
    grid = {(p.lat_deg, p.lon_deg) for p in points if p.label == "grid"}
    assert (61.0, 25.0) in grid  # Tampere area is in
    assert (69.0, 27.0) in grid  # Inari area is in
    assert (69.5, 25.0) not in grid  # Karasjok, Norway is out
    assert (59.5, 25.0) not in grid  # the Gulf of Finland and Estonia are out
    assert (65.0, 33.0) not in grid  # Russia is out
    assert (68.0, 19.0) not in grid  # Sweden is out


def test_northern_finland_gets_the_lowest_angle_and_decides_the_worst_case():
    # 8025-8500 MHz with a satellite due south: limits are strictest at low elevation, which means the north
    result = evaluate_gso(25, -55, 40, mask("art21-8025-8500"))
    assert result.points_evaluated == len(finland_points())
    assert result.worst.lat_deg > 69.5
    assert result.worst.angle_deg < 12


def test_the_default_grid_sees_visibility_change_across_finland():
    # A satellite far east clears the horizon from eastern Finland before the west
    result = evaluate_gso(100, -55, 40, mask("art21-8025-8500"))
    assert 0 < result.points_visible < result.points_evaluated
    assert result.worst.lon_deg > 25


def test_caller_can_add_stations_to_the_grid():
    station = GroundPoint(69.9, 27.0, "station 123")
    result = evaluate_gso(25, -55, 40, mask("art21-8025-8500"), (*finland_points(), station))
    assert result.points_evaluated == len(finland_points()) + 1


def test_a_satellite_finland_cannot_see_at_all_reports_not_visible():
    result = evaluate_gso(-120, -55, 40, mask("art21-8025-8500"))
    assert result.visible is False and result.worst is None
