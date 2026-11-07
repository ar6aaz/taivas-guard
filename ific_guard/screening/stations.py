"""Loads Traficom's special-interest station spreadsheet (synthetic data, ticket 05).

Bad rows are kept and flagged, not dropped: what a flag means for a result is the caller's decision.
The one row removed is an exact duplicate, and the row it duplicates records that in its flags.
"""
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATIONS_PATH = (
    Path(__file__).resolve().parents[2] / "docs/challenge/Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx"
)
HEADER_ROW = 11
FIRST_DATA_ROW = 13  # row 12 repeats the header in Finnish
COLUMNS = (
    "Station number", "Class of licence", "Longitude", "Latitude", "Ground height (m)", "Mast height (m)",
    "Mast type", "Channel bandwidth", "TX frequency", "RX frequency", "Ant height AGL (m)", "Ant height ASL (m)",
    "Ant gain (dB)", "Class of transmission", "Transmission bandwidth", "Radiation direction (°)", "Polarisation",
    "Radioation power", "Radioation power (ITU)", "Radiation power unit", "Transmission line (dB)", "Directivity",
)  # spelling as in the sheet

DUPLICATE_REMOVED = "exact_duplicate_row_removed"
STATION_NUMBER_REUSED = "station_number_not_unique"
NO_RX = "no_rx_frequency"
NO_TX = "no_tx_frequency"
BAD_FREQUENCY = "unparsed_frequency"
NO_CHANNEL_BW = "no_channel_bandwidth_used_transmission_bandwidth"
NO_BANDWIDTH = "no_usable_bandwidth"
BW_MISMATCH = "channel_and_transmission_bandwidth_differ"
MVV_GAIN = "mvv_gain_not_real"
POWER_IMPLAUSIBLE = "power_implausible"
NEGATIVE_HEIGHT = "negative_antenna_height"
OUTSIDE_FINLAND = "coordinates_outside_finland_bounding_box"

# Separates the 67 no-class rows in ticket 02 question 8 (133 MW and up) from every other row.
IMPLAUSIBLE_POWER_W = 100e6
# A rough box, for the sheet's warning that coordinates may be outside Finland. Not a border test.
FINLAND_LAT = (59.0, 70.5)
FINLAND_LON = (19.0, 32.0)

_UNIT_MHZ = {"k": 1e-3, "M": 1.0, "G": 1e3}
_POWER_W = {"mW": 1e-3, "W": 1.0, "kW": 1e3}
_FREQUENCY = re.compile(r"(\d+(?:[.,]\d+)?)\s*([kMG])Hz")
# 300.000 kHz, 5000k, 7,6k, or the ITU form 8M00 / 10k0, where the unit letter is the decimal point
_BANDWIDTH = re.compile(r"(\d+(?:[.,]\d+)?)\s*([kMG])(?:Hz)?(\d*)")


SIDE_RX = "rx"
SIDE_TX = "tx"
SIDE_BOTH = "both"  # RX and TX are the same band, as on broadcast rows


@dataclass(frozen=True)
class ProtectedBand:
    side: str
    low_mhz: float
    high_mhz: float

    def overlaps(self, low_mhz: float, high_mhz: float) -> bool:
        return low_mhz < self.high_mhz and high_mhz > self.low_mhz


@dataclass(frozen=True)
class Station:
    row: int  # row in the sheet; station_no alone is not unique
    station_no: int
    licence_class: str | None
    lon_deg: float | None
    lat_deg: float | None
    ground_height_m: float | None
    mast_height_m: float | None
    ant_height_agl_m: float | None  # as given, including the negative value on row 108
    ant_height_asl_m: float | None
    ant_gain_db: float | None  # None for MVV rows, whose values are not real; see MVV_GAIN
    tx_mhz: float | None
    rx_mhz: float | None
    channel_bw_mhz: float | None
    transmission_bw_mhz: float | None
    transmission_class: str | None
    radiation_direction_deg: float | None
    polarisation: str | None
    power: float | None  # as given: ERP or EIRP, and whether it is really watts, is ticket 02 question 8
    power_unit: str | None
    power_itu: str | None
    line_loss_db: float | None
    directivity: str | None
    quality_flags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.station_no}@R{self.row}"

    @property
    def bandwidth_mhz(self) -> float | None:
        return self.channel_bw_mhz if self.channel_bw_mhz is not None else self.transmission_bw_mhz

    def _band_mhz(self, centre_mhz: float | None) -> tuple[float, float] | None:
        if centre_mhz is None or self.bandwidth_mhz is None:
            return None
        return centre_mhz - self.bandwidth_mhz / 2, centre_mhz + self.bandwidth_mhz / 2

    @property
    def rx_band_mhz(self) -> tuple[float, float] | None:
        return self._band_mhz(self.rx_mhz)

    @property
    def tx_band_mhz(self) -> tuple[float, float] | None:
        return self._band_mhz(self.tx_mhz)

    @property
    def protected_bands(self) -> tuple[ProtectedBand, ...]:
        """Traficom: "every TX is received in Finland", so a TX band is protected as well as an RX band."""
        rx, tx = self.rx_band_mhz, self.tx_band_mhz
        if rx is not None and rx == tx:
            return (ProtectedBand(SIDE_BOTH, *rx),)
        return tuple(ProtectedBand(side, *band) for side, band in ((SIDE_RX, rx), (SIDE_TX, tx)) if band is not None)


def _text(value: Any) -> str | None:
    """Blank cells are a mix of None and empty text."""
    text = "" if value is None else str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    """Numbers can be text and use a decimal comma. Blank or unreadable gives None."""
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def parse_frequency_mhz(value: Any) -> float | None:
    match = _FREQUENCY.fullmatch(_text(value) or "")
    return round(float(match[1].replace(",", ".")) * _UNIT_MHZ[match[2]], 9) if match else None


def parse_bandwidth_mhz(value: Any) -> float | None:
    match = _BANDWIDTH.fullmatch(_text(value) or "")
    if not match or (match[3] and not match[1].isdigit()):
        return None
    decimals = f".{match[3]}" if match[3] else ""
    return round(float(match[1].replace(",", ".") + decimals) * _UNIT_MHZ[match[2]], 9)


def _quality_flags(cells: dict[str, Any]) -> list[str]:
    flags = []
    for column, missing in (("RX frequency", NO_RX), ("TX frequency", NO_TX)):
        if _text(cells[column]) is None:
            flags.append(missing)
        elif parse_frequency_mhz(cells[column]) is None:
            flags.append(BAD_FREQUENCY)

    channel = parse_bandwidth_mhz(cells["Channel bandwidth"])
    transmission = parse_bandwidth_mhz(cells["Transmission bandwidth"])
    if channel is None:
        flags.append(NO_BANDWIDTH if transmission is None else NO_CHANNEL_BW)
    elif transmission is not None and abs(channel - transmission) > 1e-9:
        flags.append(BW_MISMATCH)

    if _text(cells["Class of licence"]) == "MVV" and _number(cells["Ant gain (dB)"]) is not None:
        flags.append(MVV_GAIN)
    power, to_watts = _number(cells["Radioation power"]), _POWER_W.get(_text(cells["Radiation power unit"]) or "")
    if power is not None and to_watts is not None and power * to_watts > IMPLAUSIBLE_POWER_W:
        flags.append(POWER_IMPLAUSIBLE)
    height = _number(cells["Ant height AGL (m)"])
    if height is not None and height < 0:
        flags.append(NEGATIVE_HEIGHT)
    lat, lon = _number(cells["Latitude"]), _number(cells["Longitude"])
    if lat is None or lon is None or not (FINLAND_LAT[0] <= lat <= FINLAND_LAT[1] and FINLAND_LON[0] <= lon <= FINLAND_LON[1]):
        flags.append(OUTSIDE_FINLAND)
    return flags


def _station(row: int, cells: dict[str, Any], flags: list[str]) -> Station:
    licence_class = _text(cells["Class of licence"])
    return Station(
        row=row,
        station_no=int(cells["Station number"]),
        licence_class=licence_class,
        lon_deg=_number(cells["Longitude"]),
        lat_deg=_number(cells["Latitude"]),
        ground_height_m=_number(cells["Ground height (m)"]),
        mast_height_m=_number(cells["Mast height (m)"]),
        ant_height_agl_m=_number(cells["Ant height AGL (m)"]),
        ant_height_asl_m=_number(cells["Ant height ASL (m)"]),
        ant_gain_db=None if licence_class == "MVV" else _number(cells["Ant gain (dB)"]),
        tx_mhz=parse_frequency_mhz(cells["TX frequency"]),
        rx_mhz=parse_frequency_mhz(cells["RX frequency"]),
        channel_bw_mhz=parse_bandwidth_mhz(cells["Channel bandwidth"]),
        transmission_bw_mhz=parse_bandwidth_mhz(cells["Transmission bandwidth"]),
        transmission_class=_text(cells["Class of transmission"]),
        radiation_direction_deg=_number(cells["Radiation direction (°)"]),
        polarisation=_text(cells["Polarisation"]),
        power=_number(cells["Radioation power"]),
        power_unit=_text(cells["Radiation power unit"]),
        power_itu=_text(cells["Radioation power (ITU)"]),
        line_loss_db=_number(cells["Transmission line (dB)"]),
        directivity=_text(cells["Directivity"]),
        quality_flags=tuple(flags),
    )


def load_stations(path: Path = STATIONS_PATH) -> list[Station]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = [tuple(row[: len(COLUMNS)]) for row in workbook.worksheets[0].iter_rows(values_only=True)]
    finally:
        workbook.close()
    if len(rows) < HEADER_ROW or rows[HEADER_ROW - 1] != COLUMNS:
        raise ValueError(f"{path.name}: row {HEADER_ROW} is not the expected header, so the layout has changed")

    first_row_of: dict[tuple, int] = {}
    has_duplicate = set()
    for number, row in enumerate(rows, 1):
        if number < FIRST_DATA_ROW or _text(row[0]) is None:
            continue
        if row in first_row_of:
            has_duplicate.add(first_row_of[row])
        else:
            first_row_of[row] = number

    rows_per_number = Counter(row[0] for row in first_row_of)
    stations = []
    for row, number in first_row_of.items():
        cells = dict(zip(COLUMNS, row))
        flags = _quality_flags(cells)
        if rows_per_number[row[0]] > 1:
            flags.append(STATION_NUMBER_REUSED)
        if number in has_duplicate:
            flags.append(DUPLICATE_REMOVED)
        stations.append(_station(number, cells, flags))
    return stations
